"""
Pipeline híbrido: Qianfan-OCR (visión) para transcribir + Qwen3:14b para extracción JSON.

- Qianfan-OCR es un modelo VL especializado en OCR de documentos — lee mejor
  números en tablas y mantiene el layout.
- Qwen3:14b sigue siendo el responsable de mapear el texto al JSON estructurado.

Convive con extractor.py y extractor_vl.py — se expone vía /procesar-qianfan.
"""
import os
import io
import base64
import logging
import requests

from pdf2image import convert_from_path, pdfinfo_from_path

from .extractor import extraer_documento, TipoDocumentoNoSoportado

logger = logging.getLogger(__name__)

URL_OLLAMA = os.getenv("URL_OLLAMA")
MODELO_OCR_VL = os.getenv("MODELO_OCR_VL", "glm-ocr:bf16")
TIMEOUT_OCR_VL = int(os.getenv("TIMEOUT_OCR_VL", "600"))


def _imagen_a_base64(pil_image) -> str:
    buf = io.BytesIO()
    pil_image.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def ocr_paginas_qianfan(imagenes: list) -> dict:
    """
    Transcribe cada página con Qianfan-OCR y devuelve {n: texto}.
    Filtra páginas de Terms & Conditions legales como hace el pipeline VL.
    """
    _KEYWORDS_LEGAL = ["LIABILITY", "INDEMNIFY", "WARRANT", "JURISDICTION", "ARBITRATION", "CLAUSE"]
    textos = {}
    for i, img in enumerate(imagenes, 1):
        img_b64 = _imagen_a_base64(img)
        respuesta = requests.post(
            URL_OLLAMA,
            json={
                "model": MODELO_OCR_VL,
                "prompt": "Extract all text from this page exactly as it appears, preserving the layout and reading order. Output only the raw text.",
                "images": [img_b64],
                "stream": False,
                "keep_alive": 300,
                "options": {
                    "num_ctx": 8192,
                    "num_predict": 4096,
                    "temperature": 0,
                },
            },
            timeout=TIMEOUT_OCR_VL,
        )
        if respuesta.status_code != 200:
            logger.warning(f"Qianfan OCR error página {i}: {respuesta.status_code}")
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


def _pdf_a_imagenes_qianfan(ruta_pdf: str, dpi: int = 500) -> list:
    """Convierte PDF a imágenes sin preprocesamiento — Qianfan lee mejor las imágenes originales."""
    info = pdfinfo_from_path(ruta_pdf)
    total = info["Pages"]
    imagenes = []
    for n in range(1, total + 1):
        imgs = convert_from_path(ruta_pdf, dpi=dpi, first_page=n, last_page=n)
        imagenes.extend(imgs)
    return imagenes


def ocr_pdf_qianfan(ruta_pdf: str) -> str:
    """PDF → imágenes → texto concatenado por Qianfan-OCR (compatible con parsear_textos_ocr)."""
    imagenes = _pdf_a_imagenes_qianfan(ruta_pdf)
    textos = ocr_paginas_qianfan(imagenes)
    return "\n\n--- NUEVA PAGINA ---\n\n".join(textos.get(i, "") for i in sorted(textos))


def procesar_pdf_qianfan(ruta_pdf: str, tipo_documento: str = "DOCUMENTO_TRANSPORTE") -> dict:
    """Entrypoint del pipeline híbrido: Qianfan-OCR transcribe, Qwen3:14b extrae."""
    texto = ocr_pdf_qianfan(ruta_pdf)
    logger.info(f"[Qianfan OCR] Texto extraído ({len(texto)} chars):\n{texto[:2000]}")
    return extraer_documento(texto, tipo_documento)
