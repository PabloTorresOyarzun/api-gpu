"""
Pipeline GLM-OCR vía SDK oficial (glmocr[selfhosted]).

Diferencia clave con extractor_glm.py:
- extractor_glm.py hace llamadas crudas a Ollama (un solo prompt por página).
- Aquí usamos el SDK oficial que hace layout analysis (PP-DocLayout-V3) +
  recognition en paralelo por región, devolviendo markdown estructurado.

El markdown resultante se pasa a Qwen3:14b para extraer el JSON final.
"""
import os
import io
import logging
import tempfile
from urllib.parse import urlparse

import yaml
from pdf2image import convert_from_path, pdfinfo_from_path

from .extractor import extraer_documento, TipoDocumentoNoSoportado

logger = logging.getLogger(__name__)

URL_OLLAMA = os.getenv("URL_OLLAMA", "http://localhost:11434/api/generate")
MODELO_OCR_VL = os.getenv("MODELO_OCR_VL", "glm-ocr:bf16")

_CONFIG_PATH = "/tmp/glmocr_config.yaml"
_GLM_PARSER = None


def _construir_config() -> str:
    """Genera el config.yaml del SDK apuntando al Ollama del env."""
    parsed = urlparse(URL_OLLAMA)
    host = parsed.hostname or "localhost"
    port = parsed.port or 11434
    path = parsed.path or "/api/generate"

    config = {
        "pipeline": {
            "maas": {"enabled": False},
            "ocr_api": {
                "api_host": host,
                "api_port": port,
                "api_path": path,
                "model": MODELO_OCR_VL,
                "api_mode": "ollama_generate",
            },
        },
    }

    with open(_CONFIG_PATH, "w") as f:
        yaml.safe_dump(config, f)

    logger.info(f"[GLM-SDK] Config generado en {_CONFIG_PATH}: host={host} port={port} model={MODELO_OCR_VL}")
    return _CONFIG_PATH


def _get_parser():
    """Lazy-init del parser SDK. Reutiliza la instancia entre páginas."""
    global _GLM_PARSER
    if _GLM_PARSER is None:
        from glmocr import GlmOcr
        config_path = _construir_config()
        _GLM_PARSER = GlmOcr(config_path=config_path).__enter__()
    return _GLM_PARSER


def _pdf_a_imagenes(ruta_pdf: str, dpi: int = 300) -> list:
    """Convierte PDF a imágenes página por página."""
    info = pdfinfo_from_path(ruta_pdf)
    total = info["Pages"]
    imagenes = []
    for n in range(1, total + 1):
        imgs = convert_from_path(ruta_pdf, dpi=dpi, first_page=n, last_page=n)
        imagenes.extend(imgs)
    return imagenes


def ocr_paginas_glm_sdk(imagenes: list) -> dict:
    """
    Parsea cada página con el SDK GLM-OCR (layout + recognition).
    Devuelve {n: markdown_estructurado}.
    """
    _KEYWORDS_LEGAL = ["LIABILITY", "INDEMNIFY", "WARRANT", "JURISDICTION", "ARBITRATION", "CLAUSE"]
    parser = _get_parser()
    textos = {}

    for i, img in enumerate(imagenes, 1):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            img.save(tmp.name, format="PNG")
            tmp_path = tmp.name

        try:
            result = parser.parse(tmp_path)
            markdown = result.markdown_result or ""
        except Exception as e:
            logger.warning(f"GLM-OCR SDK error página {i}: {e}")
            markdown = ""
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

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
    """PDF → imágenes → markdown concatenado vía SDK."""
    imagenes = _pdf_a_imagenes(ruta_pdf)
    textos = ocr_paginas_glm_sdk(imagenes)
    return "\n\n--- NUEVA PAGINA ---\n\n".join(textos.get(i, "") for i in sorted(textos))


def procesar_pdf_glm_sdk(ruta_pdf: str, tipo_documento: str = "DOCUMENTO_TRANSPORTE") -> dict:
    """Entrypoint: SDK transcribe con layout, Qwen3:14b extrae JSON."""
    texto = ocr_pdf_glm_sdk(ruta_pdf)
    logger.info(f"[GLM-SDK] Markdown extraído ({len(texto)} chars):\n{texto[:2000]}")
    return extraer_documento(texto, tipo_documento)
