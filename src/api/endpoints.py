import os
import asyncio
import tempfile
import logging
from typing import Annotated

from litestar import post, get, Request
from litestar.params import Body
from litestar.enums import RequestEncodingType
from litestar.datastructures import UploadFile
from litestar.exceptions import HTTPException

from ..services.extractor import procesar_pdf, ocr_pdf
from ..services.sanitizer import sanitizar_pdf
from ..services.classifier import clasificar_paginas, segmentar_pdf

from .middleware import validar_api_key
from .helpers import convertir_a_pdf, parsear_textos_ocr

logger = logging.getLogger(__name__)


# ===========================================================================
# Extracción directa de BL
# ===========================================================================

@post("/extraer", tags=["Extracción"])
async def extraer_endpoint(
    request: Request,
    data: Annotated[UploadFile, Body(media_type=RequestEncodingType.MULTI_PART)],
) -> dict:
    """
    Sube un PDF de Bill of Lading y obtén los datos extraídos en JSON.

    El procesamiento incluye OCR con Surya en GPU y extracción estructurada con Qwen3:14b.
    Tiempo estimado: 15-30 segundos según número de páginas.
    """
    validar_api_key(request)

    if not data.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="El archivo debe ser un PDF")

    contenido = await data.read()

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(contenido)
        ruta_temporal = tmp.name

    try:
        resultado = await asyncio.to_thread(procesar_pdf, ruta_temporal)
        return resultado
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error procesando PDF: {str(e)}")
    finally:
        if os.path.exists(ruta_temporal):
            os.unlink(ruta_temporal)


# ===========================================================================
# Sanitizar PDF
# ===========================================================================

@post("/sanitizar", tags=["Tratamiento"])
async def sanitizar_endpoint(
    request: Request,
    data: Annotated[UploadFile, Body(media_type=RequestEncodingType.MULTI_PART)],
) -> dict:
    """
    Sanitiza un PDF: repara geometría, analiza calidad y corrige rotaciones.

    Acepta PDF, Excel o imagen. Si no es PDF, lo convierte primero.
    Retorna el análisis de calidad por página y las alertas encontradas.
    """
    validar_api_key(request)

    contenido = await data.read()
    nombre = data.filename or "documento"

    try:
        pdf_bytes = await convertir_a_pdf(contenido, nombre)
        pdf_sanitizado, alertas = await asyncio.to_thread(sanitizar_pdf, pdf_bytes)

        return {
            "filename": nombre,
            "paginas_analizadas": len(alertas),
            "alertas": alertas,
            "pdf_size_bytes": len(pdf_sanitizado),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"Error sanitizando {nombre}")
        raise HTTPException(status_code=500, detail=f"Error sanitizando documento: {str(e)}")


# ===========================================================================
# Clasificar y segmentar
# ===========================================================================

@post("/clasificar", tags=["Tratamiento"])
async def clasificar_endpoint(
    request: Request,
    data: Annotated[UploadFile, Body(media_type=RequestEncodingType.MULTI_PART)],
) -> dict:
    """
    Clasifica las páginas de un PDF por tipo de documento y lo segmenta.

    Pipeline: Convertir → Sanitizar → OCR (Surya) → Clasificar → Segmentar.
    Retorna la clasificación por página y los documentos segmentados con su tipo.
    """
    validar_api_key(request)

    contenido = await data.read()
    nombre = data.filename or "documento"

    try:
        pdf_bytes = await convertir_a_pdf(contenido, nombre)
        pdf_bytes, alertas = await asyncio.to_thread(sanitizar_pdf, pdf_bytes)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(pdf_bytes)
            ruta_temporal = tmp.name

        try:
            textos_raw = await asyncio.to_thread(ocr_pdf, ruta_temporal)
        finally:
            if os.path.exists(ruta_temporal):
                os.unlink(ruta_temporal)

        textos_por_pagina = parsear_textos_ocr(textos_raw)
        clasificaciones = clasificar_paginas(textos_por_pagina)
        documentos = await asyncio.to_thread(segmentar_pdf, pdf_bytes, clasificaciones)

        docs_respuesta = []
        for doc in documentos:
            docs_respuesta.append({
                "tipo": doc["tipo"],
                "paginas": doc["paginas"],
                "pdf_size_bytes": len(doc["pdf_bytes"]),
            })

        return {
            "filename": nombre,
            "total_paginas": len(textos_por_pagina),
            "clasificaciones": clasificaciones,
            "documentos_segmentados": docs_respuesta,
            "alertas_sanitizacion": alertas,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"Error clasificando {nombre}")
        raise HTTPException(status_code=500, detail=f"Error clasificando documento: {str(e)}")


# ===========================================================================
# Pipeline completo
# ===========================================================================

@post("/procesar", tags=["Tratamiento"])
async def procesar_endpoint(
    request: Request,
    data: Annotated[UploadFile, Body(media_type=RequestEncodingType.MULTI_PART)],
) -> dict:
    """
    Pipeline completo de procesamiento de documentos.

    1. Convierte a PDF (si es Excel o imagen)
    2. Sanitiza (repara, analiza calidad, corrige rotación)
    3. OCR con Surya en GPU
    4. Clasifica páginas por tipo de documento
    5. Segmenta en documentos individuales
    6. Extrae datos estructurados de los BL detectados

    Acepta: PDF, Excel (.xlsx/.xls/.xlsm), imágenes (.jpg/.png/.tiff/etc.)
    """
    validar_api_key(request)

    contenido = await data.read()
    nombre = data.filename or "documento"

    try:
        logger.info(f"[procesar] Paso 0: Convirtiendo {nombre}")
        pdf_bytes = await convertir_a_pdf(contenido, nombre)

        logger.info(f"[procesar] Paso 1: Sanitizando ({len(pdf_bytes)} bytes)")
        pdf_bytes, alertas = await asyncio.to_thread(sanitizar_pdf, pdf_bytes)

        logger.info("[procesar] Paso 2: OCR Surya")
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(pdf_bytes)
            ruta_temporal = tmp.name

        try:
            textos_raw = await asyncio.to_thread(ocr_pdf, ruta_temporal)
        finally:
            if os.path.exists(ruta_temporal):
                os.unlink(ruta_temporal)

        textos_por_pagina = parsear_textos_ocr(textos_raw)

        logger.info(f"[procesar] Paso 3: Clasificando {len(textos_por_pagina)} páginas")
        clasificaciones = clasificar_paginas(textos_por_pagina)
        documentos = await asyncio.to_thread(segmentar_pdf, pdf_bytes, clasificaciones)

        logger.info(f"[procesar] Paso 4: Extrayendo datos de {len(documentos)} documento(s)")
        resultados = []
        for doc in documentos:
            doc_resultado = {
                "tipo": doc["tipo"],
                "paginas": doc["paginas"],
            }

            if doc["tipo"] == "DOCUMENTO_TRANSPORTE":
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    tmp.write(doc["pdf_bytes"])
                    ruta_doc = tmp.name
                try:
                    datos_bl = await asyncio.to_thread(procesar_pdf, ruta_doc)
                    doc_resultado["datos_extraidos"] = datos_bl
                except Exception as e:
                    logger.warning(f"Error extrayendo BL de segmento {doc['paginas']}: {e}")
                    doc_resultado["datos_extraidos"] = None
                    doc_resultado["error_extraccion"] = str(e)
                finally:
                    if os.path.exists(ruta_doc):
                        os.unlink(ruta_doc)
            else:
                doc_resultado["datos_extraidos"] = None

            resultados.append(doc_resultado)

        return {
            "filename": nombre,
            "total_paginas": len(textos_por_pagina),
            "clasificaciones": clasificaciones,
            "documentos": resultados,
            "alertas_sanitizacion": alertas,
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"Error en pipeline completo para {nombre}")
        raise HTTPException(status_code=500, detail=f"Error procesando documento: {str(e)}")


# ===========================================================================
# Sistema
# ===========================================================================

@get("/health", tags=["Sistema"])
async def health() -> dict:
    """Verifica que la API esté operativa."""
    return {"status": "ok"}
