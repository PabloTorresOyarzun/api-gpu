"""
Motor de clasificación de documentos por patrones y segmentación de PDFs.
Standalone - sin dependencias de Azure ni framework web.

La parte de extracción de texto (Azure DI) se deja como responsabilidad
del proyecto que integre este toolkit. Aquí solo se incluye:
- Clasificación por patrones (local, sin red)
- Segmentación de PDFs multi-documento
- Recorte de headers para pre-procesamiento
"""
import fitz
import re
import logging
from typing import List, Dict, Tuple
from .patterns import PATRONES_INICIO, PATRONES_NEGATIVOS, PATRON_DEFAULT

logger = logging.getLogger(__name__)


# ===========================================================================
# Reparación de CropBox (necesaria antes de recortar)
# ===========================================================================

def _reparar_cropbox(pdf_doc: fitz.Document):
    """Repara CropBox corrupto en todas las páginas del documento."""
    for page in pdf_doc:
        try:
            xref = page.xref
            cropbox_type, _ = pdf_doc.xref_get_key(xref, "CropBox")
            if cropbox_type == "array":
                try:
                    _ = page.rect
                except ValueError:
                    logger.warning(f"recortar_header: Página {page.number + 1} CropBox corrupto, eliminando.")
                    pdf_doc.xref_set_key(xref, "CropBox", "null")
        except Exception as e:
            logger.warning(f"recortar_header: Error reparando página {page.number + 1}: {e}")
            try:
                pdf_doc.xref_set_key(page.xref, "CropBox", "null")
            except Exception:
                pass


# ===========================================================================
# Recorte de headers
# ===========================================================================

def recortar_header(pdf_bytes: bytes, porcentaje: float = 0.35) -> bytes:
    """Recorta el porcentaje superior de cada página del PDF."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    _reparar_cropbox(doc)

    new_doc = fitz.open()

    for page in doc:
        rect = page.rect
        crop_displayed = fitz.Rect(rect.x0, rect.y0, rect.x1, rect.y0 + rect.height * porcentaje)

        if page.rotation != 0:
            crop_page = crop_displayed * page.derotation_matrix
            crop_page.normalize()
        else:
            crop_page = crop_displayed

        page.set_cropbox(crop_page)
        new_doc.insert_pdf(doc, from_page=page.number, to_page=page.number)

    pdf_recortado = new_doc.tobytes()
    new_doc.close()
    doc.close()

    return pdf_recortado


# ===========================================================================
# Clasificación por patrones
# ===========================================================================

def _buscar_patrones(texto_upper: str) -> str:
    """Busca patrones en el texto y retorna el tipo del match más temprano."""
    matches = []

    for tipo_documento, patrones in PATRONES_INICIO.items():
        for patron in patrones:
            pattern = r'\b' + re.escape(patron.upper()) + r'\b'
            match = re.search(pattern, texto_upper)

            if match:
                matches.append({
                    "index": match.start(),
                    "tipo": tipo_documento
                })

    if not matches:
        return PATRON_DEFAULT

    matches.sort(key=lambda x: x["index"])
    return matches[0]["tipo"]


def clasificar_pagina(texto: str) -> str:
    """
    Clasifica una página con doble pasada:
    1. Primero aplica patrones negativos y busca (protege contra falsos positivos en BLs)
    2. Si el resultado es UNKNOWN, reintenta SIN negativos (rescata facturas legítimas)
    """
    texto_upper = texto.upper()

    # --- Pasada 1: con negativos ---
    texto_limpio = texto_upper
    for patron_neg in PATRONES_NEGATIVOS:
        texto_limpio = re.sub(
            r'\b' + re.escape(patron_neg.upper()) + r'\b',
            '',
            texto_limpio
        )

    resultado = _buscar_patrones(texto_limpio)
    if resultado != PATRON_DEFAULT:
        return resultado

    # --- Pasada 2: sin negativos (fallback) ---
    return _buscar_patrones(texto_upper)


def clasificar_paginas(textos_por_pagina: Dict[int, str]) -> List[Dict]:
    """
    Clasifica todas las páginas dado un diccionario {num_pagina: texto}.
    Retorna lista de {pagina, tipo}.
    """
    clasificaciones = []
    for num_pagina in sorted(textos_por_pagina.keys()):
        texto = textos_por_pagina[num_pagina]
        tipo = clasificar_pagina(texto)
        clasificaciones.append({"pagina": num_pagina, "tipo": tipo})
    return clasificaciones


# ===========================================================================
# Segmentación de PDFs
# ===========================================================================

def segmentar_pdf(pdf_bytes: bytes, clasificaciones: List[Dict]) -> List[Dict]:
    """
    Segmenta un PDF en múltiples documentos según las clasificaciones.
    Retorna lista de documentos segmentados con sus metadatos.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    documentos_segmentados = []

    paginas_por_tipo = {}
    tipo_actual = None
    inicio_segmento = 0

    for i, clasificacion in enumerate(clasificaciones):
        tipo = clasificacion["tipo"]

        if tipo != tipo_actual and tipo != PATRON_DEFAULT:
            if tipo_actual is not None:
                paginas_por_tipo[f"{tipo_actual}_{inicio_segmento}"] = {
                    "tipo": tipo_actual,
                    "inicio": inicio_segmento,
                    "fin": i - 1
                }
            tipo_actual = tipo
            inicio_segmento = i
        elif tipo == PATRON_DEFAULT and tipo_actual is not None:
            continue

    if tipo_actual is not None:
        paginas_por_tipo[f"{tipo_actual}_{inicio_segmento}"] = {
            "tipo": tipo_actual,
            "inicio": inicio_segmento,
            "fin": len(clasificaciones) - 1
        }

    if not paginas_por_tipo:
        nuevo_doc = fitz.open()
        nuevo_doc.insert_pdf(doc)
        documentos_segmentados.append({
            "tipo": PATRON_DEFAULT,
            "paginas": list(range(1, len(doc) + 1)),
            "pdf_bytes": nuevo_doc.tobytes()
        })
        nuevo_doc.close()
    else:
        for key, segmento in paginas_por_tipo.items():
            nuevo_doc = fitz.open()
            nuevo_doc.insert_pdf(doc, from_page=segmento["inicio"], to_page=segmento["fin"])

            documentos_segmentados.append({
                "tipo": segmento["tipo"],
                "paginas": list(range(segmento["inicio"] + 1, segmento["fin"] + 2)),
                "pdf_bytes": nuevo_doc.tobytes()
            })
            nuevo_doc.close()

    doc.close()
    return documentos_segmentados
