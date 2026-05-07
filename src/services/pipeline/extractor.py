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


def cargar_modelos():
    global _recognition_predictor, _detection_predictor
    if _recognition_predictor is None:
        print("Cargando modelos de Surya en GPU...")
        _recognition_predictor = RecognitionPredictor()
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


def _reconstruct_text_layout_aware(text_lines) -> str:
    """
    Reconstruye el texto preservando estructura espacial:
    - Agrupa fragmentos OCR en filas visuales según overlap vertical de sus bboxes.
    - Dentro de cada fila, ordena de izquierda a derecha y separa con espacios proporcionales
      al gap horizontal (indicador de columnas).
    Esto ayuda al LLM a interpretar tablas correctamente.
    """
    if not text_lines:
        return ""

    entries = []
    for tl in text_lines:
        bbox = getattr(tl, "bbox", None)
        text = getattr(tl, "text", "") or ""
        if bbox and len(bbox) == 4:
            x0, y0, x1, y1 = bbox
            entries.append({
                "x0": float(x0), "y0": float(y0),
                "x1": float(x1), "y1": float(y1),
                "text": text,
            })
        else:
            # Sin bbox: anexar al final como una línea propia.
            entries.append(None)
            entries[-1] = {"x0": 0, "y0": 1e9 + len(entries), "x1": 0, "y1": 1e9 + len(entries) + 1, "text": text}

    if not entries:
        return ""

    # Ordenar por y0 (top-to-bottom).
    entries.sort(key=lambda e: (e["y0"], e["x0"]))

    # Agrupar en filas visuales por overlap vertical.
    rows = []
    current_row = [entries[0]]
    for e in entries[1:]:
        last = current_row[-1]
        h = max(last["y1"] - last["y0"], e["y1"] - e["y0"], 1.0)
        # Misma fila si el centro vertical de e cae dentro del rango vertical del último item de la fila.
        e_center = (e["y0"] + e["y1"]) / 2
        if last["y0"] - h * 0.3 <= e_center <= last["y1"] + h * 0.3:
            current_row.append(e)
        else:
            rows.append(current_row)
            current_row = [e]
    rows.append(current_row)

    # Reconstruir cada fila ordenando por x0 e insertando separación proporcional al gap.
    output_lines = []
    for row in rows:
        row.sort(key=lambda e: e["x0"])
        parts = [row[0]["text"]]
        for prev, curr in zip(row, row[1:]):
            gap = curr["x0"] - prev["x1"]
            avg_h = (prev["y1"] - prev["y0"] + curr["y1"] - curr["y0"]) / 2
            # Gap > altura promedio sugiere columna distinta → separador más fuerte.
            if gap > avg_h * 1.5:
                parts.append("    ")
            else:
                parts.append(" ")
            parts.append(curr["text"])
        output_lines.append("".join(parts))

    return "\n".join(output_lines)


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

        langs = [["es", "en"]] * len(imagenes)
        predicciones = _recognition_predictor(imagenes, langs, det_predictor=_detection_predictor)
        for pagina in predicciones:
            texto_pagina = _reconstruct_text_layout_aware(pagina.text_lines)
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
        raise ValueError(f"Sin prompt configurado para tipo de documento: {tipo_documento}")

    prompt_usuario = f"Documento a procesar:\n\n{texto_documento}"

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
                "num_ctx": 16384,
                "num_predict": 4096,
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
