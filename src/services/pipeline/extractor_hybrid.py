"""
Pipeline híbrido: Surya detecta regiones (layout) + GLM-OCR lee cada región.

- Surya DetectionPredictor: segmenta la página en bboxes (sin OCR).
- Los bboxes se agrupan verticalmente para evitar decenas de llamadas a Ollama.
- GLM-OCR transcribe cada bloque recortado con máxima calidad.
- Qwen3:14b extrae el JSON final.

Separación clave:
  - Surya recibe la imagen preprocesada (escala de grises + CLAHE) → mejor detección.
  - GLM recibe recortes de la imagen ORIGINAL en color + sharpening propio → mejor OCR.
"""
import io
import os
import json
import base64
import logging

from pdf2image import convert_from_path, pdfinfo_from_path

from .extractor import (
    detectar_regiones, extraer_documento,
    _preprocess_image, _preprocess_crop_for_glm,
)
from .extractor_glm import _llamar_glm

logger = logging.getLogger(__name__)

_KEYWORDS_LEGAL = ["LIABILITY", "INDEMNIFY", "WARRANT", "JURISDICTION", "ARBITRATION", "CLAUSE"]


def _imagen_a_base64(pil_image) -> str:
    buf = io.BytesIO()
    pil_image.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _agrupar_bboxes(bboxes: list, gap_vertical: int = 40) -> list:
    """
    Agrupa bboxes cercanos en bloques para reducir llamadas a GLM-OCR.
    Ordena por y1. Bboxes con gap vertical <= gap_vertical px se fusionan.
    Devuelve lista de bboxes envolventes [x1, y1, x2, y2].
    """
    if not bboxes:
        return []

    ordenados = sorted(bboxes, key=lambda b: b[1])
    bloques = []
    actual = list(ordenados[0])

    for b in ordenados[1:]:
        gap = b[1] - actual[3]  # top_siguiente - bottom_actual
        if gap <= gap_vertical:
            actual[0] = min(actual[0], b[0])
            actual[1] = min(actual[1], b[1])
            actual[2] = max(actual[2], b[2])
            actual[3] = max(actual[3], b[3])
        else:
            bloques.append(actual)
            actual = list(b)

    bloques.append(actual)
    return bloques


def _recortar(pil_image, bbox, padding: int = 10):
    w, h = pil_image.size
    x1 = max(0, int(bbox[0]) - padding)
    y1 = max(0, int(bbox[1]) - padding)
    x2 = min(w, int(bbox[2]) + padding)
    y2 = min(h, int(bbox[3]) + padding)
    return pil_image.crop((x1, y1, x2, y2))


def _leer_bloque(img_b64: str, bbox) -> str:
    """
    Lee un bloque con GLM-OCR.
    Bloques de tabla (ratio ancho/alto > 5): corre ambos modos para no perder
    texto flotante fuera de celdas (ej. "FOB SHANGHAI" encima de la tabla).
    Bloques de texto: solo Text Recognition.
    """
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    es_tabla = h > 0 and (w / h) > 5

    texto = _llamar_glm(img_b64, "Text Recognition: ")
    if es_tabla:
        tabla = _llamar_glm(img_b64, "Table Recognition: ")
        if tabla.strip() and tabla.strip() != texto.strip():
            return f"{texto}\n{tabla}"
    return texto


def _dump_hybrid_page(pdf_base: str, num_pagina: int, bboxes, bloques, partes_detalle, texto_final) -> None:
    """Dump de debug del pipeline híbrido. Activado por OCR_DEBUG_DUMP=/dir."""
    dump_dir = os.getenv("OCR_DEBUG_DUMP")
    if not dump_dir:
        return
    try:
        os.makedirs(dump_dir, exist_ok=True)
        out_path = os.path.join(dump_dir, f"{pdf_base}_hybrid_p{num_pagina}.json")
        data = {
            "pagina": num_pagina,
            "n_bboxes_surya": len(bboxes),
            "n_bloques_agrupados": len(bloques),
            "bloques": [
                {
                    "bbox": [round(float(v), 1) for v in b["bbox"]],
                    "area": int(b["area"]),
                    "es_tabla": b["es_tabla"],
                    "ratio_w_h": round(b["ratio"], 2),
                    "texto_glm": b["texto"],
                }
                for b in partes_detalle
            ],
            "texto_final": texto_final,
        }
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"Hybrid dump pág {num_pagina}: {out_path}")
    except Exception as e:
        logger.warning(f"Falló dump hybrid pág {num_pagina}: {e}")


def ocr_paginas_hybrid(imagenes_originales: list, imagenes_surya: list, pdf_base: str = "doc") -> dict:
    """
    Detección Surya (en imagenes_surya) → agrupamiento → GLM-OCR por bloque (en imagenes_originales).
    Devuelve {n: texto}.
    """
    textos = {}

    for i, (img_orig, img_surya) in enumerate(zip(imagenes_originales, imagenes_surya), 1):
        bboxes = detectar_regiones([img_surya])[0]
        if not bboxes:
            logger.warning(f"Página {i}: Surya no detectó regiones")
            textos[i] = ""
            continue

        bloques = _agrupar_bboxes(bboxes, gap_vertical=40)
        logger.info(f"Página {i}: {len(bboxes)} bboxes → {len(bloques)} bloques para GLM-OCR")

        partes = []
        partes_detalle = []
        for bbox in bloques:
            area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
            if area < 1000:
                continue

            recorte = _recortar(img_orig, bbox)
            recorte = _preprocess_crop_for_glm(recorte)
            img_b64 = _imagen_a_base64(recorte)
            try:
                texto_bloque = _leer_bloque(img_b64, bbox)
            except Exception as e:
                logger.warning(f"GLM-OCR error bloque página {i}: {e}")
                texto_bloque = ""
            if texto_bloque.strip():
                partes.append(texto_bloque)
            w = bbox[2] - bbox[0]
            h = bbox[3] - bbox[1]
            ratio = (w / h) if h > 0 else 0
            partes_detalle.append({
                "bbox": bbox,
                "area": area,
                "ratio": ratio,
                "es_tabla": ratio > 5,
                "texto": texto_bloque,
            })

        texto = "\n".join(partes)
        _dump_hybrid_page(pdf_base, i, bboxes, bloques, partes_detalle, texto)

        texto_upper = texto.upper()

        if "OVERLEAF INSTRUCTION" in texto_upper:
            logger.info(f"Página {i} identificada como instrucciones (Overleaf) — omitida")
            textos[i] = ""
            continue

        palabras = len(texto.split())
        if palabras > 600:
            hits = sum(1 for k in _KEYWORDS_LEGAL if k in texto_upper)
            if hits >= 3:
                logger.info(f"Página {i} identificada como T&C legal — omitida")
                textos[i] = ""
                continue

        textos[i] = texto

    return textos


def ocr_pdf_hybrid(ruta_pdf: str) -> str:
    info = pdfinfo_from_path(ruta_pdf)
    total = info["Pages"]

    imagenes_originales = []
    imagenes_surya = []
    for n in range(1, total + 1):
        imgs = convert_from_path(ruta_pdf, dpi=500, first_page=n, last_page=n)
        for img in imgs:
            imagenes_originales.append(img)
            imagenes_surya.append(_preprocess_image(img))

    pdf_base = os.path.splitext(os.path.basename(ruta_pdf))[0]
    textos = ocr_paginas_hybrid(imagenes_originales, imagenes_surya, pdf_base=pdf_base)
    return "\n\n--- NUEVA PAGINA ---\n\n".join(textos.get(i, "") for i in sorted(textos))


def procesar_pdf_hybrid(ruta_pdf: str, tipo_documento: str = "DOCUMENTO_TRANSPORTE") -> dict:
    """Entrypoint: Surya layout + GLM-OCR transcribe, Qwen3:14b extrae JSON."""
    texto = ocr_pdf_hybrid(ruta_pdf)
    logger.info(f"[HYBRID] Texto enviado a Qwen ({len(texto)} chars):\n{texto[:2000]}")
    return extraer_documento(texto, tipo_documento)
