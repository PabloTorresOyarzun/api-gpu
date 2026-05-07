import asyncio
import logging

from ..services.pipeline.converter import (
    validar_pdf,
    validar_tamano_archivo,
    es_archivo_excel,
    es_archivo_imagen,
    validar_excel,
    validar_imagen,
    convertir_excel_a_pdf_sync,
    convertir_imagen_a_pdf_sync,
)

logger = logging.getLogger(__name__)


async def convertir_a_pdf(contenido: bytes, nombre: str) -> bytes:
    """Convierte el archivo a PDF si es Excel o imagen. Valida el resultado."""
    validar_tamano_archivo(contenido)

    if es_archivo_excel(nombre):
        if not validar_excel(contenido, nombre):
            raise ValueError(f"Archivo Excel inválido: {nombre}")
        return await asyncio.to_thread(convertir_excel_a_pdf_sync, contenido, nombre)

    if es_archivo_imagen(nombre):
        if not validar_imagen(contenido, nombre):
            raise ValueError(f"Archivo de imagen inválido: {nombre}")
        return await asyncio.to_thread(convertir_imagen_a_pdf_sync, contenido, nombre)

    # Asumir PDF
    if not validar_pdf(contenido):
        raise ValueError(f"Archivo PDF inválido o corrupto: {nombre}")
    return contenido


def parsear_textos_ocr(textos_raw: str) -> dict[int, str]:
    """Convierte el string de OCR (separado por '--- NUEVA PAGINA ---') a dict {num_pagina: texto}."""
    paginas = textos_raw.split("\n\n--- NUEVA PAGINA ---\n\n")
    return {i + 1: texto for i, texto in enumerate(paginas)}
