import os
import re
import json
import logging
import requests
import cv2
import numpy as np
from PIL import Image
from pdf2image import convert_from_path, pdfinfo_from_path
from surya.recognition import RecognitionPredictor
from surya.detection import DetectionPredictor
from surya.foundation import FoundationPredictor

from ..prompts import transporte, factura, lista_embalaje, certificado_origen

logger = logging.getLogger(__name__)

URL_OLLAMA = os.getenv("URL_OLLAMA")
MODELO = os.getenv("MODELO", "qwen3:14b")

_recognition_predictor = None
_detection_predictor = None

_PROMPT_MAP = {
    "DOCUMENTO_TRANSPORTE": transporte.PROMPT_SISTEMA,
    "FACTURA_COMERCIAL": factura.PROMPT_SISTEMA,
    "LISTA_EMBALAJE": lista_embalaje.PROMPT_SISTEMA,
    "CERTIFICADO_ORIGEN": certificado_origen.PROMPT_SISTEMA,
}


class TipoDocumentoNoSoportado(Exception):
    """El tipo de documento no tiene prompt configurado para extracción."""


def cargar_modelos():
    global _recognition_predictor, _detection_predictor
    if _recognition_predictor is None:
        print("Cargando modelos de Surya en GPU...")
        _foundation_predictor = FoundationPredictor()
        _recognition_predictor = RecognitionPredictor(_foundation_predictor)
        _detection_predictor = DetectionPredictor()
        print("Modelos cargados.")


def _preprocess_image(pil_image: Image.Image) -> Image.Image:
    """
    Denoising suave + contraste adaptativo (CLAHE).
    Ayuda al OCR en escaneados ruidosos o con poco contraste sin alterar el layout.
    """
    img = np.array(pil_image)

    if len(img.shape) == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    else:
        gray = img

    # fastNlMeans: limpia ruido conservando bordes de texto.
    denoised = cv2.fastNlMeansDenoising(gray, h=7, templateWindowSize=7, searchWindowSize=21)

    # CLAHE: realza contraste local sin saturar zonas claras/oscuras.
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(denoised)

    rgb = cv2.cvtColor(enhanced, cv2.COLOR_GRAY2RGB)
    return Image.fromarray(rgb)


def _get_bbox(text_line):
    """Devuelve [x1, y1, x2, y2] del text_line o None si no está disponible."""
    bbox = getattr(text_line, "bbox", None)
    if bbox is not None and len(bbox) == 4:
        return bbox
    polygon = getattr(text_line, "polygon", None)
    if polygon and len(polygon) >= 2:
        xs = [p[0] for p in polygon]
        ys = [p[1] for p in polygon]
        return [min(xs), min(ys), max(xs), max(ys)]
    return None


def _group_text_lines(text_lines, v_gap_px: int = 50):
    """
    Agrupa líneas consecutivas que estén a menos de v_gap_px píxeles de distancia vertical.
    Líneas en columnas horizontalmente separadas (>100px sin overlap) inician un bloque nuevo.
    NO reordena — preserva el orden original de Surya en todo momento.

    v_gap_px=50: a 500 DPI un interlineado normal (~17px) queda dentro del bloque;
    un salto de párrafo (~80-100px) abre uno nuevo.
    """
    if not text_lines:
        return []

    groups = []
    current_group = [text_lines[0]]

    for line in text_lines[1:]:
        prev_bbox = _get_bbox(current_group[-1])
        curr_bbox = _get_bbox(line)

        if prev_bbox is None or curr_bbox is None:
            current_group.append(line)
            continue

        vertical_gap = curr_bbox[1] - prev_bbox[3]  # top_curr - bottom_prev
        h_overlap = min(prev_bbox[2], curr_bbox[2]) - max(prev_bbox[0], curr_bbox[0])
        different_column = h_overlap < -100  # separados >100px horizontalmente

        if vertical_gap <= v_gap_px and not different_column:
            current_group.append(line)
        else:
            groups.append(current_group)
            current_group = [line]

    groups.append(current_group)
    return groups


def _reconstruct_text(text_lines) -> str:
    """
    Agrupa líneas por proximidad espacial y las envuelve en [BLOQUE_N].
    Preserva el orden original de Surya — nunca reordena líneas.
    """
    groups = _group_text_lines(text_lines)
    parts = []
    n = 1
    for group in groups:
        lines = [getattr(tl, "text", "") or "" for tl in group]
        lines = [l for l in lines if l.strip()]
        if not lines:
            continue
        parts.append(f"[BLOQUE_{n}]\n" + "\n".join(lines) + f"\n[/BLOQUE_{n}]")
        n += 1
    return "\n\n".join(parts)


def ocr_pdf(ruta_pdf: str) -> str:
    cargar_modelos()

    info = pdfinfo_from_path(ruta_pdf)
    total_paginas = info["Pages"]

    texto_paginas = []
    for num_pagina in range(1, total_paginas + 1):
        imagenes = convert_from_path(
            ruta_pdf,
            dpi=500,
            first_page=num_pagina,
            last_page=num_pagina,
        )
        # Preprocesamiento (denoising + contraste adaptativo).
        imagenes = [_preprocess_image(img) for img in imagenes]

        predicciones = _recognition_predictor(imagenes, det_predictor=_detection_predictor)
        for pagina in predicciones:
            texto_pagina = _reconstruct_text(pagina.text_lines)
            texto_upper = texto_pagina.upper()
            palabras = len(texto_pagina.split())

            es_legal = False
            if palabras > 600:
                keywords = ["LIABILITY", "INDEMNIFY", "WARRANT", "JURISDICTION", "ARBITRATION", "CLAUSE"]
                hits = sum(1 for k in keywords if k in texto_upper)
                if hits >= 3:
                    es_legal = True

            if not es_legal:
                texto_paginas.append(texto_pagina)

        del imagenes, predicciones

    return "\n\n--- NUEVA PAGINA ---\n\n".join(texto_paginas)


def extraer_documento(texto_documento: str, tipo_documento: str) -> dict:
    prompt_sistema = _PROMPT_MAP.get(tipo_documento)
    if not prompt_sistema:
        raise TipoDocumentoNoSoportado(f"Sin prompt configurado para tipo de documento: {tipo_documento}")

    prompt_usuario = (
        "NOTA DE FORMATO: El texto fue extraído por OCR y organizado en bloques espaciales "
        "[BLOQUE_N]...[/BLOQUE_N]. Las líneas dentro de un mismo bloque estaban físicamente "
        "próximas en el documento original. Usa esto como pista contextual: un teléfono, "
        "email o dirección que aparece dentro del mismo bloque que el nombre del SELLER o "
        "SHIPPER pertenece a esa entidad; si está en un bloque distinto (por ejemplo, en el "
        "pie de página o en el bloque del BUYER), NO lo asignes al vendedor ni viceversa.\n\n"
        f"Documento a procesar:\n\n{texto_documento}"
    )

    respuesta = requests.post(
        URL_OLLAMA,
        json={
            "model": MODELO,
            "system": prompt_sistema,
            "prompt": prompt_usuario,
            "format": "json",
            "stream": False,
            "think": False,
            "keep_alive": 0,
            "options": {
                "num_ctx": 24576,
                "num_predict": 12288,
                "temperature": 0,
                "top_p": 0.9,
                "top_k": 20,
                "repeat_penalty": 1.0,
                "seed": 42,
            },
        },
        timeout=600,
    )

    if respuesta.status_code != 200:
        raise RuntimeError(f"Error de Ollama: {respuesta.status_code} - {respuesta.text}")

    res_json = respuesta.json().get("response", "")
    match = re.search(r"\{.*\}", res_json, re.DOTALL)
    if not match:
        raise ValueError(f"La IA no devolvió un JSON válido. Respuesta: {res_json[:500]}")

    return json.loads(match.group(0))


def detectar_regiones(imagenes: list) -> list[list]:
    """Corre solo DetectionPredictor y devuelve [[x1,y1,x2,y2], ...] por página."""
    cargar_modelos()
    resultados = _detection_predictor(imagenes)
    paginas = []
    for pagina in resultados:
        bboxes = []
        for det in pagina.bboxes:
            b = getattr(det, "bbox", None)
            if b and len(b) == 4:
                bboxes.append(list(b))
        paginas.append(bboxes)
    return paginas


def procesar_pdf(ruta_pdf: str, tipo_documento: str = "DOCUMENTO_TRANSPORTE") -> dict:
    import torch
    import gc

    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    texto = ocr_pdf(ruta_pdf)

    global _recognition_predictor, _detection_predictor
    _recognition_predictor = None
    _detection_predictor = None
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    return extraer_documento(texto, tipo_documento)
