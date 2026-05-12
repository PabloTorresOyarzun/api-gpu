import os
import asyncio
import tempfile
import logging
import time
from typing import Annotated

from litestar import post, get, Request
from litestar.params import Body
from litestar.enums import RequestEncodingType
from litestar.datastructures import UploadFile
from litestar.exceptions import HTTPException

from ..services.pipeline.extractor import procesar_pdf, ocr_pdf, TipoDocumentoNoSoportado
from ..services.pipeline.extractor_vl import procesar_pdf_vl, pdf_a_imagenes, ocr_paginas_vl, clasificar_paginas_vl
from ..services.pipeline.extractor_glm import procesar_pdf_glm, ocr_pdf_glm
from ..services.pipeline.sanitizer import sanitizar_pdf
from ..services.pipeline.classifier import clasificar_paginas, segmentar_pdf

from .middleware import validar_api_key
from .helpers import convertir_a_pdf, parsear_textos_ocr

logger = logging.getLogger(__name__)


@get("/health", tags=["Sistema"])
async def health() -> dict:
    """Verifica que la API esté operativa."""
    return {"status": "ok"}


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
        t_inicio = time.perf_counter()
        tiempos = {}

        logger.info(f"[procesar] Paso 0: Convirtiendo {nombre}")
        t0 = time.perf_counter()
        pdf_bytes = await convertir_a_pdf(contenido, nombre)
        tiempos["paso_0_conversion"] = round(time.perf_counter() - t0, 3)

        logger.info(f"[procesar] Paso 1: Sanitizando ({len(pdf_bytes)} bytes)")
        t0 = time.perf_counter()
        pdf_bytes, alertas = await asyncio.to_thread(sanitizar_pdf, pdf_bytes)
        tiempos["paso_1_sanitizacion"] = round(time.perf_counter() - t0, 3)

        logger.info("[procesar] Paso 2: OCR Surya")
        t0 = time.perf_counter()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(pdf_bytes)
            ruta_temporal = tmp.name

        try:
            textos_raw = await asyncio.to_thread(ocr_pdf, ruta_temporal)
        finally:
            if os.path.exists(ruta_temporal):
                os.unlink(ruta_temporal)

        textos_por_pagina = parsear_textos_ocr(textos_raw)
        tiempos["paso_2_ocr"] = round(time.perf_counter() - t0, 3)

        logger.info(f"[procesar] Paso 3: Clasificando {len(textos_por_pagina)} páginas")
        t0 = time.perf_counter()
        clasificaciones = clasificar_paginas(textos_por_pagina)
        documentos = await asyncio.to_thread(segmentar_pdf, pdf_bytes, clasificaciones)
        tiempos["paso_3_clasificacion"] = round(time.perf_counter() - t0, 3)

        logger.info(f"[procesar] Paso 4: Extrayendo datos de {len(documentos)} documento(s)")
        t0 = time.perf_counter()
        resultados = []
        for doc in documentos:
            doc_resultado = {
                "tipo": doc["tipo"],
                "paginas": doc["paginas"],
            }

            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(doc["pdf_bytes"])
                ruta_doc = tmp.name
            try:
                datos = await asyncio.to_thread(procesar_pdf, ruta_doc, doc["tipo"])
                doc_resultado["datos_extraidos"] = datos
            except TipoDocumentoNoSoportado:
                # Tipo sin prompt configurado — extracción no soportada todavía
                doc_resultado["datos_extraidos"] = None
            except Exception as e:
                logger.warning(f"Error extrayendo segmento {doc['tipo']} págs {doc['paginas']}: {e}")
                doc_resultado["datos_extraidos"] = None
                doc_resultado["error_extraccion"] = str(e)
            finally:
                if os.path.exists(ruta_doc):
                    os.unlink(ruta_doc)

            resultados.append(doc_resultado)

        tiempos["paso_4_extraccion"] = round(time.perf_counter() - t0, 3)
        tiempos["total"] = round(time.perf_counter() - t_inicio, 3)

        return {
            "filename": nombre,
            "total_paginas": len(textos_por_pagina),
            "clasificaciones": clasificaciones,
            "documentos": resultados,
            "alertas_sanitizacion": alertas,
            "tiempos_segundos": tiempos,
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"Error en pipeline completo para {nombre}")
        raise HTTPException(status_code=500, detail=f"Error procesando documento: {str(e)}")


@post("/procesar-vl", tags=["Tratamiento"])
async def procesar_endpoint_vl(
    request: Request,
    data: Annotated[UploadFile, Body(media_type=RequestEncodingType.MULTI_PART)],
) -> dict:
    """
    Pipeline alternativo basado en Qwen3-VL (visión multimodal).
    El modelo ve las imágenes del documento directamente — sin OCR intermedio.

    1. Convierte a PDF (si es Excel o imagen)
    2. Sanitiza (repara, analiza calidad, corrige rotación)
    3. Convierte páginas a imágenes y extrae texto por página vía qwen3-vl
    4. Clasifica páginas por tipo de documento
    5. Segmenta en documentos individuales
    6. Extrae datos estructurados pasando las imágenes a qwen3-vl

    Acepta: PDF, Excel (.xlsx/.xls/.xlsm), imágenes (.jpg/.png/.tiff/etc.)
    """
    validar_api_key(request)

    contenido = await data.read()
    nombre = data.filename or "documento"

    try:
        t_inicio = time.perf_counter()
        tiempos = {}

        logger.info(f"[procesar-vl] Paso 0: Convirtiendo {nombre}")
        t0 = time.perf_counter()
        pdf_bytes = await convertir_a_pdf(contenido, nombre)
        tiempos["paso_0_conversion"] = round(time.perf_counter() - t0, 3)

        logger.info(f"[procesar-vl] Paso 1: Sanitizando ({len(pdf_bytes)} bytes)")
        t0 = time.perf_counter()
        pdf_bytes, alertas = await asyncio.to_thread(sanitizar_pdf, pdf_bytes)
        tiempos["paso_1_sanitizacion"] = round(time.perf_counter() - t0, 3)

        logger.info("[procesar-vl] Paso 2: Convirtiendo páginas a imágenes")
        t0 = time.perf_counter()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(pdf_bytes)
            ruta_temporal = tmp.name

        try:
            imagenes_doc = await asyncio.to_thread(pdf_a_imagenes, ruta_temporal)
        finally:
            if os.path.exists(ruta_temporal):
                os.unlink(ruta_temporal)
        tiempos["paso_2_imagenes"] = round(time.perf_counter() - t0, 3)

        logger.info(f"[procesar-vl] Paso 3: Clasificando {len(imagenes_doc)} páginas por visión")
        t0 = time.perf_counter()
        clasificaciones = await asyncio.to_thread(clasificar_paginas_vl, imagenes_doc)
        documentos = await asyncio.to_thread(segmentar_pdf, pdf_bytes, clasificaciones)
        tiempos["paso_3_clasificacion"] = round(time.perf_counter() - t0, 3)

        logger.info(f"[procesar-vl] Paso 4: Extrayendo datos de {len(documentos)} documento(s)")
        t0 = time.perf_counter()
        resultados = []
        for doc in documentos:
            doc_resultado = {
                "tipo": doc["tipo"],
                "paginas": doc["paginas"],
            }

            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(doc["pdf_bytes"])
                ruta_doc = tmp.name
            try:
                datos = await asyncio.to_thread(procesar_pdf_vl, ruta_doc, doc["tipo"])
                doc_resultado["datos_extraidos"] = datos
            except TipoDocumentoNoSoportado:
                doc_resultado["datos_extraidos"] = None
            except Exception as e:
                logger.warning(f"Error VL extrayendo segmento {doc['tipo']} págs {doc['paginas']}: {e}")
                doc_resultado["datos_extraidos"] = None
                doc_resultado["error_extraccion"] = str(e)
            finally:
                if os.path.exists(ruta_doc):
                    os.unlink(ruta_doc)

            resultados.append(doc_resultado)

        tiempos["paso_4_extraccion"] = round(time.perf_counter() - t0, 3)
        tiempos["total"] = round(time.perf_counter() - t_inicio, 3)

        return {
            "filename": nombre,
            "total_paginas": len(imagenes_doc),
            "clasificaciones": clasificaciones,
            "documentos": resultados,
            "alertas_sanitizacion": alertas,
            "tiempos_segundos": tiempos,
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"Error en pipeline VL para {nombre}")
        raise HTTPException(status_code=500, detail=f"Error procesando documento: {str(e)}")


@post("/procesar-glm", tags=["Tratamiento"])
async def procesar_endpoint_glm(
    request: Request,
    data: Annotated[UploadFile, Body(media_type=RequestEncodingType.MULTI_PART)],
) -> dict:
    """
    Pipeline híbrido: GLM-OCR (visión) transcribe + Qwen3:14b extrae JSON.

    Mismo flujo que /procesar pero reemplaza Surya por GLM-OCR. GLM-OCR
    es un modelo VL especializado en OCR de documentos — lee mejor números
    en tablas y mantiene el layout.

    1. Convierte a PDF (si es Excel o imagen)
    2. Sanitiza (repara, analiza calidad, corrige rotación)
    3. OCR con GLM-OCR (vía Ollama)
    4. Clasifica páginas por tipo de documento (texto)
    5. Segmenta en documentos individuales
    6. Extrae datos estructurados con Qwen3:14b

    Acepta: PDF, Excel (.xlsx/.xls/.xlsm), imágenes (.jpg/.png/.tiff/etc.)
    """
    validar_api_key(request)

    contenido = await data.read()
    nombre = data.filename or "documento"

    try:
        t_inicio = time.perf_counter()
        tiempos = {}

        logger.info(f"[procesar-glm] Paso 0: Convirtiendo {nombre}")
        t0 = time.perf_counter()
        pdf_bytes = await convertir_a_pdf(contenido, nombre)
        tiempos["paso_0_conversion"] = round(time.perf_counter() - t0, 3)

        logger.info(f"[procesar-glm] Paso 1: Sanitizando ({len(pdf_bytes)} bytes)")
        t0 = time.perf_counter()
        pdf_bytes, alertas = await asyncio.to_thread(sanitizar_pdf, pdf_bytes)
        tiempos["paso_1_sanitizacion"] = round(time.perf_counter() - t0, 3)

        logger.info("[procesar-glm] Paso 2: OCR GLM-OCR")
        t0 = time.perf_counter()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(pdf_bytes)
            ruta_temporal = tmp.name

        try:
            textos_raw = await asyncio.to_thread(ocr_pdf_glm, ruta_temporal)
        finally:
            if os.path.exists(ruta_temporal):
                os.unlink(ruta_temporal)

        textos_por_pagina = parsear_textos_ocr(textos_raw)
        tiempos["paso_2_ocr"] = round(time.perf_counter() - t0, 3)

        logger.info(f"[procesar-glm] Paso 3: Clasificando {len(textos_por_pagina)} páginas")
        t0 = time.perf_counter()
        clasificaciones = clasificar_paginas(textos_por_pagina)
        documentos = await asyncio.to_thread(segmentar_pdf, pdf_bytes, clasificaciones)
        tiempos["paso_3_clasificacion"] = round(time.perf_counter() - t0, 3)

        logger.info(f"[procesar-glm] Paso 4: Extrayendo datos de {len(documentos)} documento(s)")
        t0 = time.perf_counter()
        resultados = []
        for doc in documentos:
            doc_resultado = {
                "tipo": doc["tipo"],
                "paginas": doc["paginas"],
            }

            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                tmp.write(doc["pdf_bytes"])
                ruta_doc = tmp.name
            try:
                datos = await asyncio.to_thread(procesar_pdf_glm, ruta_doc, doc["tipo"])
                doc_resultado["datos_extraidos"] = datos
            except TipoDocumentoNoSoportado:
                doc_resultado["datos_extraidos"] = None
            except Exception as e:
                logger.warning(f"Error GLM-OCR extrayendo segmento {doc['tipo']} págs {doc['paginas']}: {e}")
                doc_resultado["datos_extraidos"] = None
                doc_resultado["error_extraccion"] = str(e)
            finally:
                if os.path.exists(ruta_doc):
                    os.unlink(ruta_doc)

            resultados.append(doc_resultado)

        tiempos["paso_4_extraccion"] = round(time.perf_counter() - t0, 3)
        tiempos["total"] = round(time.perf_counter() - t_inicio, 3)

        return {
            "filename": nombre,
            "total_paginas": len(textos_por_pagina),
            "clasificaciones": clasificaciones,
            "documentos": resultados,
            "alertas_sanitizacion": alertas,
            "tiempos_segundos": tiempos,
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"Error en pipeline GLM-OCR para {nombre}")
        raise HTTPException(status_code=500, detail=f"Error procesando documento: {str(e)}")
