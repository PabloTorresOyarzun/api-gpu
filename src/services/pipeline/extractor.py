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


def _reconstruct_text(text_lines) -> str:
    """
    Une las líneas detectadas por Surya preservando su orden natural de lectura.
    Surya ya entrega text_lines ordenados correctamente — re-ordenar por bboxes
    en escaneados con skew leve mezcla filas adyacentes en tablas, así que
    confiamos en el orden original.
    """
    return "\n".join(getattr(tl, "text", "") or "" for tl in text_lines)


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
