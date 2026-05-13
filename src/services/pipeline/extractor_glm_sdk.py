"""
Pipeline GLM-OCR vía contenedor `glm-ocr-sdk` (HTTP).

El SDK glmocr[selfhosted] no se puede instalar junto a surya-ocr en el mismo
contenedor (conflicto de Pillow y torch). Por eso vive en un contenedor aparte
(docker/glmocr/) y aquí solo llamamos a su endpoint HTTP /parse.

El SDK hace internamente: layout analysis (PP-DocLayout-V3) + recognition por
región vía Ollama → markdown estructurado. El markdown se pasa luego a
Qwen3:14b para la extracción JSON.
"""
import os
import io
import base64
import logging

import requests
from pdf2image import convert_from_path, pdfinfo_from_path

from .extractor import extraer_documento, TipoDocumentoNoSoportado

logger = logging.getLogger(__name__)

URL_GLM_SDK = os.getenv("URL_GLM_SDK", "http://glm-ocr-sdk:5002/parse")
TIMEOUT_GLM_SDK = int(os.getenv("TIMEOUT_GLM_SDK", "600"))


def _imagen_a_base64(pil_image) -> str:
    buf = io.BytesIO()
    pil_image.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _parse_pagina(img_b64: str) -> str:
    """Llama al contenedor glm-ocr-sdk y devuelve el markdown estructurado."""
    respuesta = requests.post(
        URL_GLM_SDK,
        json={"image_base64": img_b64},
        timeout=TIMEOUT_GLM_SDK,
    )
    if respuesta.status_code != 200:
        logger.warning(f"glm-ocr-sdk respondió {respuesta.status_code}: {respuesta.text[:200]}")
        return ""
    return respuesta.json().get("markdown", "")


def _pdf_a_imagenes(ruta_pdf: str, dpi: int = 300) -> list:
    info = pdfinfo_from_path(ruta_pdf)
    total = info["Pages"]
    imagenes = []
    for n in range(1, total + 1):
        imgs = convert_from_path(ruta_pdf, dpi=dpi, first_page=n, last_page=n)
        imagenes.extend(imgs)
    return imagenes


def ocr_paginas_glm_sdk(imagenes: list) -> dict:
    """Parsea cada página vía el contenedor SDK y devuelve {n: markdown}."""
    _KEYWORDS_LEGAL = ["LIABILITY", "INDEMNIFY", "WARRANT", "JURISDICTION", "ARBITRATION", "CLAUSE"]
    textos = {}

    for i, img in enumerate(imagenes, 1):
        img_b64 = _imagen_a_base64(img)
        try:
            markdown = _parse_pagina(img_b64)
        except Exception as e:
            logger.warning(f"glm-ocr-sdk error página {i}: {e}")
            markdown = ""

        palabras = len(markdown.split())
        if palabras > 600:
            hits = sum(1 for k in _KEYWORDS_LEGAL if k in markdown.upper())
            if hits >= 3:
                logger.info(f"Página {i} identificada como T&C legal — omitida")
                textos[i] = ""
                continue

        textos[i] = markdown

    return textos


def ocr_pdf_glm_sdk(ruta_pdf: str) -> str:
    imagenes = _pdf_a_imagenes(ruta_pdf)
    textos = ocr_paginas_glm_sdk(imagenes)
    return "\n\n--- NUEVA PAGINA ---\n\n".join(textos.get(i, "") for i in sorted(textos))


def procesar_pdf_glm_sdk(ruta_pdf: str, tipo_documento: str = "DOCUMENTO_TRANSPORTE") -> dict:
    """Entrypoint: SDK (vía contenedor) transcribe con layout, Qwen3:14b extrae JSON."""
    texto = ocr_pdf_glm_sdk(ruta_pdf)
    logger.info(f"[GLM-SDK] Markdown extraído ({len(texto)} chars):\n{texto[:2000]}")
    return extraer_documento(texto, tipo_documento)
