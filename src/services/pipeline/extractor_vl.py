"""
Pipeline de extracción alternativo basado en Qwen3-VL (visión multimodal).
Reemplaza el OCR (Surya) + LLM de texto (Qwen3) por un único modelo visual
que recibe las imágenes del documento directamente.

Convive con extractor.py (OCR clásico) sin tocarlo — se expone vía /procesar-vl.
"""
import os
import re
import io
import json
import base64
import logging
import requests
from PIL import Image
from pdf2image import convert_from_path, pdfinfo_from_path

from .extractor import _PROMPT_MAP, TipoDocumentoNoSoportado

logger = logging.getLogger(__name__)

URL_OLLAMA = os.getenv("URL_OLLAMA")
MODELO_VL = os.getenv("MODELO_VL", "qwen3-vl:8b")


def _imagen_a_base64(pil_image: Image.Image) -> str:
    buf = io.BytesIO()
    pil_image.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def pdf_a_imagenes(ruta_pdf: str, dpi: int = 200) -> list:
    """Convierte todas las páginas del PDF a imágenes PIL."""
    info = pdfinfo_from_path(ruta_pdf)
    total = info["Pages"]
    imagenes = []
    for n in range(1, total + 1):
        imgs = convert_from_path(ruta_pdf, dpi=dpi, first_page=n, last_page=n)
        imagenes.extend(imgs)
    return imagenes


def ocr_paginas_vl(imagenes: list) -> dict:
    """
    Extrae texto por página usando qwen3-vl (para alimentar al clasificador).
    keep_alive=300 mantiene el modelo cargado entre páginas de la misma solicitud.
    Filtra páginas de Terms & Conditions legales.
    """
    _KEYWORDS_LEGAL = ["LIABILITY", "INDEMNIFY", "WARRANT", "JURISDICTION", "ARBITRATION", "CLAUSE"]

    textos = {}
    for i, img in enumerate(imagenes, 1):
        img_b64 = _imagen_a_base64(img)
        respuesta = requests.post(
            URL_OLLAMA,
            json={
                "model": MODELO_VL,
                "prompt": "Extract all text from this page exactly as it appears. Output only the raw text, no explanations.",
                "images": [img_b64],
                "stream": False,
                "think": False,
                "keep_alive": 300,
                "options": {
                    "num_ctx": 8192,
                    "num_predict": 4096,
                    "temperature": 0,
                },
            },
            timeout=300,
        )
        if respuesta.status_code != 200:
            logger.warning(f"OCR VL error página {i}: {respuesta.status_code}")
            textos[i] = ""
            continue

        texto = respuesta.json().get("response", "")

        palabras = len(texto.split())
        if palabras > 600:
            hits = sum(1 for k in _KEYWORDS_LEGAL if k in texto.upper())
            if hits >= 3:
                logger.info(f"Página {i} identificada como T&C legal — omitida")
                textos[i] = ""
                continue

        textos[i] = texto

    return textos


def extraer_documento_vl(imagenes: list, tipo_documento: str) -> dict:
    """Extracción vía qwen3-vl: el modelo ve las imágenes directamente, sin OCR previo."""
    prompt_sistema = _PROMPT_MAP.get(tipo_documento)
    if not prompt_sistema:
        raise TipoDocumentoNoSoportado(f"Sin prompt configurado para tipo de documento: {tipo_documento}")

    imagenes_b64 = [_imagen_a_base64(img) for img in imagenes]

    prompt_usuario = (
        "Extrae todos los datos del documento según el esquema JSON definido. "
        "Si hay varias páginas, analiza TODAS las imágenes y combina la información."
    )

    respuesta = requests.post(
        URL_OLLAMA,
        json={
            "model": MODELO_VL,
            "system": prompt_sistema,
            "prompt": prompt_usuario,
            "images": imagenes_b64,
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


def procesar_pdf_vl(ruta_pdf: str, tipo_documento: str = "DOCUMENTO_TRANSPORTE") -> dict:
    """Entrypoint del pipeline VL: PDF → imágenes → extracción."""
    imagenes = pdf_a_imagenes(ruta_pdf)
    return extraer_documento_vl(imagenes, tipo_documento)
