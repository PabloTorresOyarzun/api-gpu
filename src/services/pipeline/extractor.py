import os
import re
import json
import logging
import concurrent.futures
import requests
import json_repair
import cv2
import numpy as np
from PIL import Image, ImageFilter, ImageEnhance
from pdf2image import convert_from_path, pdfinfo_from_path
from surya.recognition import RecognitionPredictor
from surya.detection import DetectionPredictor
from surya.foundation import FoundationPredictor

from ..prompts import transporte, factura, lista_embalaje, certificado_origen
from ..schemas import SCHEMA_MAP
from ...utils.standards import corregir_direcciones_chilenas

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


class TipoDocumentoNoSoportado(Exception):
    """El tipo de documento no tiene prompt configurado para extracción."""


def cargar_modelos():
    global _recognition_predictor, _detection_predictor
    if _recognition_predictor is None:
        print("Cargando modelos de Surya en GPU...")
        _foundation_predictor = FoundationPredictor()
        _recognition_predictor = RecognitionPredictor(_foundation_predictor)
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


def _preprocess_crop_for_glm(crop: Image.Image, min_width: int = 900) -> Image.Image:
    """
    Preprocesamiento específico para imágenes enviadas a GLM-OCR.
    Distinto al de Surya: mantiene color, escala al mínimo y aplica sharpening suave.
    GLM-OCR funciona mejor con imágenes nítidas en color que con las grises + CLAHE de Surya.
    """
    w, h = crop.size
    if w < min_width:
        scale = min_width / w
        crop = crop.resize((min_width, max(1, int(h * scale))), Image.LANCZOS)

    crop = crop.filter(ImageFilter.UnsharpMask(radius=1.5, percent=140, threshold=3))
    crop = ImageEnhance.Contrast(crop).enhance(1.15)
    return crop


def _get_bbox(text_line):
    """Devuelve [x1, y1, x2, y2] del text_line o None si no está disponible."""
    bbox = getattr(text_line, "bbox", None)
    if bbox is not None and len(bbox) == 4:
        return bbox
    polygon = getattr(text_line, "polygon", None)
    if polygon and len(polygon) >= 2:
        xs = [p[0] for p in polygon]
        ys = [p[1] for p in polygon]
        return [min(xs), min(ys), max(xs), max(ys)]
    return None


def _group_text_lines(text_lines, v_gap_px: int = 50):
    """
    Agrupa líneas consecutivas que estén a menos de v_gap_px píxeles de distancia vertical.
    Líneas en columnas horizontalmente separadas (>100px sin overlap) inician un bloque nuevo.
    NO reordena — preserva el orden original de Surya en todo momento.

    v_gap_px=50: a 500 DPI un interlineado normal (~17px) queda dentro del bloque;
    un salto de párrafo (~80-100px) abre uno nuevo.
    """
    if not text_lines:
        return []

    groups = []
    current_group = [text_lines[0]]

    for line in text_lines[1:]:
        prev_bbox = _get_bbox(current_group[-1])
        curr_bbox = _get_bbox(line)

        if prev_bbox is None or curr_bbox is None:
            current_group.append(line)
            continue

        vertical_gap = curr_bbox[1] - prev_bbox[3]  # top_curr - bottom_prev
        h_overlap = min(prev_bbox[2], curr_bbox[2]) - max(prev_bbox[0], curr_bbox[0])
        different_column = h_overlap < -100  # separados >100px horizontalmente

        if vertical_gap <= v_gap_px and not different_column:
            current_group.append(line)
        else:
            groups.append(current_group)
            current_group = [line]

    groups.append(current_group)
    return groups


def _reconstruct_text(text_lines) -> str:
    """
    Agrupa líneas por proximidad espacial y las envuelve en [BLOQUE_N].
    Preserva el orden original de Surya — nunca reordena líneas.
    """
    groups = _group_text_lines(text_lines)
    parts = []
    n = 1
    for group in groups:
        lines = [getattr(tl, "text", "") or "" for tl in group]
        lines = [l for l in lines if l.strip()]
        if not lines:
            continue
        parts.append(f"[BLOQUE_{n}]\n" + "\n".join(lines) + f"\n[/BLOQUE_{n}]")
        n += 1
    return "\n\n".join(parts)


def _dump_surya_lines(text_lines, num_pagina: int, ruta_pdf: str) -> None:
    """
    Dump de debug: escribe bbox+texto de cada text_line a JSON.
    Activado por env var OCR_DEBUG_DUMP=/ruta/al/directorio.
    """
    dump_dir = os.getenv("OCR_DEBUG_DUMP")
    if not dump_dir:
        return
    try:
        os.makedirs(dump_dir, exist_ok=True)
        base = os.path.splitext(os.path.basename(ruta_pdf))[0]
        out_path = os.path.join(dump_dir, f"{base}_p{num_pagina}.json")
        lines_data = []
        for tl in text_lines:
            lines_data.append({
                "bbox": _get_bbox(tl),
                "text": getattr(tl, "text", "") or "",
                "confidence": getattr(tl, "confidence", None),
            })
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(lines_data, f, ensure_ascii=False, indent=2)
        logger.info(f"Surya dump escrito: {out_path} ({len(lines_data)} líneas)")
    except Exception as e:
        logger.warning(f"Falló dump Surya pág {num_pagina}: {e}")


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

        predicciones = _recognition_predictor(imagenes, det_predictor=_detection_predictor)
        for pagina in predicciones:
            _dump_surya_lines(pagina.text_lines, num_pagina, ruta_pdf)
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


# Límites de contexto/predicción por tipo de documento.
# BL y CO tienen JSONs simples → menor num_predict para terminar antes.
# LISTA_EMBALAJE produce JSONs muy grandes → mayor capacidad y timeout.
_LLM_LIMITS_DEFAULT = {"num_ctx": 24576, "num_predict": 12288, "timeout": 600}
_LLM_LIMITS_POR_TIPO = {
    "LISTA_EMBALAJE":       {"num_ctx": 32768, "num_predict": 24576, "timeout": 1200},
    "DOCUMENTO_TRANSPORTE": {"num_ctx": 12288, "num_predict": 4096,  "timeout": 240},
    "CERTIFICADO_ORIGEN":   {"num_ctx": 12288, "num_predict": 4096,  "timeout": 240},
}


def _llamar_qwen(
    prompt_sistema: str,
    prompt_usuario: str,
    num_ctx: int,
    num_predict: int,
    timeout: int = 600,
    seed: int = 42,
    keep_alive: int = 300,
    format_schema: dict | None = None,
) -> str:
    """
    Llama a Ollama. Si format_schema viene seteado, fuerza la salida al JSON Schema
    (constrained generation de Ollama ≥ 0.5). Si la versión de Ollama no lo soporta,
    cae automáticamente a format="json".
    """
    fmt = format_schema if format_schema is not None else "json"
    payload = {
        "model": MODELO,
        "system": prompt_sistema,
        "prompt": prompt_usuario,
        "format": fmt,
        "stream": False,
        "think": False,
        "keep_alive": keep_alive,
        "options": {
            "num_ctx": num_ctx,
            "num_predict": num_predict,
            "temperature": 0,
            "top_p": 0.9,
            "top_k": 20,
            "repeat_penalty": 1.0,
            "seed": seed,
        },
    }
    respuesta = requests.post(URL_OLLAMA, json=payload, timeout=timeout)

    # Fallback: si Ollama rechaza el schema (versión vieja u otro motivo), reintenta con format="json".
    if respuesta.status_code != 200 and format_schema is not None:
        logger.warning(
            f"Ollama rechazó format=schema ({respuesta.status_code}). Reintentando con format='json'."
        )
        payload["format"] = "json"
        respuesta = requests.post(URL_OLLAMA, json=payload, timeout=timeout)

    if respuesta.status_code != 200:
        raise RuntimeError(f"Error de Ollama: {respuesta.status_code} - {respuesta.text}")
    return respuesta.json().get("response", "")


def liberar_qwen():
    """Descarga Qwen de la GPU (keep_alive=0) al terminar el lote de extracción."""
    try:
        requests.post(URL_OLLAMA, json={"model": MODELO, "keep_alive": 0}, timeout=15)
        logger.info("Qwen descargado de VRAM.")
    except Exception as e:
        logger.warning(f"No se pudo descargar Qwen de VRAM: {e}")


# Nota OCR que encabeza el prompt de usuario en todas las llamadas normales.
_NOTA_OCR = (
    "NOTA DE FORMATO: El texto fue extraído por OCR y organizado en bloques espaciales "
    "[BLOQUE_N]...[/BLOQUE_N]. Las líneas dentro de un mismo bloque estaban físicamente "
    "próximas en el documento original. Usa esto como pista contextual: un teléfono, "
    "email o dirección que aparece dentro del mismo bloque que el nombre del SELLER o "
    "SHIPPER pertenece a esa entidad; si está en un bloque distinto (por ejemplo, en el "
    "pie de página o en el bloque del BUYER), NO lo asignes al vendedor ni viceversa."
)

# Campos por defecto de items para normalizar schema compacto (Qwen omite nulls).
_ITEM_DEFAULTS: dict[str, dict] = {
    "FACTURA_COMERCIAL": {
        "line_number": None, "reference_code": None, "description": None,
        "hs_code": None, "country_of_origin": None, "quantity": None,
        "unit_of_measure": None, "unit_price": None, "discount_amount": None,
        "discount_percent": None, "line_total": None, "currency": None,
        "purchase_order": None, "additional_attributes": {},
    },
    "LISTA_EMBALAJE": {
        "line_number": None, "reference_code": None, "description": None,
        "hs_code": None, "country_of_origin": None, "quantity": None,
        "unit_of_measure": None, "package_count": None, "package_type": None,
        "gross_weight_kg": None, "net_weight_kg": None, "length_cm": None,
        "width_cm": None, "height_cm": None, "volume_cbm": None,
        "marks": None, "additional_attributes": {},
    },
}

# Umbral y tipos que activan extracción por chunks.
# LISTA_EMBALAJE excluida: su contexto global (totales, marcas, cabecera) se fragmenta
# al dividir por páginas, degradando la calidad. Sus límites grandes (32768/24576) son
# suficientes para una sola llamada.
_UMBRAL_PALABRAS_GRANDE = 1200
_TIPOS_CHUNKEABLE = {"FACTURA_COMERCIAL"}


def _build_prompt_usuario(texto: str, es_continuacion: bool = False) -> str:
    if es_continuacion:
        return (
            "CONTINUACIÓN: Esta es una página adicional del mismo documento ya procesado. "
            "Extrae ÚNICAMENTE los ítems (campo items[]) visibles en esta página. "
            "Para todos los demás campos del esquema devuelve null o [].\n\n"
            f"Texto de esta página:\n\n{texto}"
        )
    return f"{_NOTA_OCR}\n\nDocumento a procesar:\n\n{texto}"


def _limpiar_item(item: dict, defaults: dict) -> dict:
    """
    Fusiona defaults + item y elimina claves contaminadas con caracteres no-ASCII
    (ej. 'additional属性' que Qwen a veces inyecta junto a 'additional_attributes').
    """
    merged = {**defaults, **item}
    return {k: v for k, v in merged.items() if k.isascii()}


def _rellenar_items(resultado: dict, tipo: str) -> dict:
    """Rellena con None los campos de items[] que Qwen omitió por schema compacto."""
    defaults = _ITEM_DEFAULTS.get(tipo)
    if defaults and "items" in resultado and isinstance(resultado["items"], list):
        resultado["items"] = [_limpiar_item(item, defaults) for item in resultado["items"]]
    return resultado


def _get_format_schema(tipo: str) -> dict | None:
    """Devuelve el JSON Schema generado por Pydantic para forzar el output de Ollama, o None."""
    model_cls = SCHEMA_MAP.get(tipo)
    if model_cls is None:
        return None
    return model_cls.model_json_schema()


def _validar_con_pydantic(resultado: dict, tipo: str) -> dict:
    """
    Valida y normaliza el resultado contra el schema Pydantic del tipo.
    - Mapea aliases (total → total_amount, incoterms → incoterm, etc.).
    - Descarta claves no declaradas.
    - Rellena con defaults los campos faltantes.
    Si el tipo no tiene schema registrado, devuelve el dict tal cual.
    Si la validación lanza error, loguea y devuelve el dict crudo (no rompe el flujo).
    """
    model_cls = SCHEMA_MAP.get(tipo)
    if model_cls is None:
        return resultado
    try:
        validado = model_cls.model_validate(resultado)
        return validado.model_dump(mode="json")
    except Exception as e:
        logger.warning(f"Validación Pydantic falló para {tipo} ({e}); devolviendo dict crudo.")
        return resultado


def _es_documento_grande(texto: str, tipo: str) -> bool:
    return tipo in _TIPOS_CHUNKEABLE and len(texto.split()) > _UMBRAL_PALABRAS_GRANDE


def _split_por_paginas(texto: str) -> list[str]:
    return [p.strip() for p in texto.split("--- NUEVA PAGINA ---") if p.strip()]


def _extraer_documento_simple(texto: str, tipo: str, prompt_sistema: str, limites: dict) -> dict:
    prompt_usuario = _build_prompt_usuario(texto)
    schema = _get_format_schema(tipo)
    res_json = _llamar_qwen(
        prompt_sistema, prompt_usuario,
        limites["num_ctx"], limites["num_predict"], limites["timeout"],
        format_schema=schema,
    )
    resultado = _parse_json_con_fallback(res_json, prompt_sistema, prompt_usuario, tipo, limites)
    resultado = _validar_con_pydantic(resultado, tipo)
    return _rellenar_items(resultado, tipo)


def _extraer_chunk(args: tuple) -> tuple[int, dict]:
    """Worker para ThreadPoolExecutor: extrae un chunk de página y devuelve (idx, resultado)."""
    idx, pagina, tipo, prompt_sistema, limites, es_continuacion = args
    prompt_usuario = _build_prompt_usuario(pagina, es_continuacion=es_continuacion)
    schema = _get_format_schema(tipo)
    res_json = _llamar_qwen(
        prompt_sistema, prompt_usuario,
        limites["num_ctx"], limites["num_predict"], limites["timeout"],
        format_schema=schema,
    )
    resultado = _parse_json_con_fallback(res_json, prompt_sistema, prompt_usuario, tipo, limites)
    resultado = _validar_con_pydantic(resultado, tipo)
    return idx, _rellenar_items(resultado, tipo)


def _extraer_documento_chunkeado(texto: str, tipo: str, prompt_sistema: str, limites: dict) -> dict:
    """
    Divide el documento en páginas y extrae cada una en paralelo.
    La página 0 extrae header + items; las demás solo items (prompt de continuación).
    Al terminar, los items de todas las páginas se fusionan en el resultado de la página 0.
    """
    paginas = _split_por_paginas(texto)
    if len(paginas) <= 1:
        return _extraer_documento_simple(texto, tipo, prompt_sistema, limites)

    logger.info(
        f"Documento grande {tipo} ({len(paginas)} págs, {len(texto.split())} palabras) "
        f"→ extracción en {len(paginas)} chunks paralelos"
    )

    args_list = [
        (0, paginas[0], tipo, prompt_sistema, limites, False),
        *((i + 1, p, tipo, prompt_sistema, limites, True) for i, p in enumerate(paginas[1:])),
    ]

    chunks: list[dict | None] = [None] * len(args_list)
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(args_list)) as executor:
        futures = {executor.submit(_extraer_chunk, args): args[0] for args in args_list}
        for future in concurrent.futures.as_completed(futures):
            idx, resultado = future.result()
            chunks[idx] = resultado

    base = chunks[0] or {}
    for chunk in chunks[1:]:
        if chunk and isinstance(chunk.get("items"), list) and chunk["items"]:
            base.setdefault("items", []).extend(chunk["items"])

    return base


def extraer_documento(texto_documento: str, tipo_documento: str) -> dict:
    prompt_sistema = _PROMPT_MAP.get(tipo_documento)
    if not prompt_sistema:
        raise TipoDocumentoNoSoportado(f"Sin prompt configurado para tipo de documento: {tipo_documento}")

    limites = _LLM_LIMITS_POR_TIPO.get(tipo_documento, _LLM_LIMITS_DEFAULT)

    if _es_documento_grande(texto_documento, tipo_documento):
        resultado = _extraer_documento_chunkeado(texto_documento, tipo_documento, prompt_sistema, limites)
    else:
        resultado = _extraer_documento_simple(texto_documento, tipo_documento, prompt_sistema, limites)

    # Post-procesamiento: corregir comunas chilenas mal OCR'eadas en direcciones.
    return corregir_direcciones_chilenas(resultado)


def _extraer_json_str(res_json: str) -> str | None:
    match = re.search(r"\{.*\}", res_json, re.DOTALL)
    return match.group(0) if match else None


def _parse_json_con_fallback(res_json: str, prompt_sistema: str, prompt_usuario: str, tipo_documento: str, limites: dict) -> dict:
    """
    Pipeline escalonado de parsing:
      1. json.loads estricto (gratis)
      2. json_repair (gratis, arregla truncamientos/comas/comillas)
      3. retry con misma config + seed distinta (segundos)
      4. retry con limits aumentados (caro, último recurso)
    """
    json_str = _extraer_json_str(res_json)

    # Etapa 1: parse estricto
    if json_str:
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.warning(
                f"JSON estricto falló para {tipo_documento} ({e}). "
                f"Tail respuesta: ...{res_json[-200:]!r}"
            )

    # Etapa 2: json_repair sobre la respuesta cruda (maneja JSON truncado mejor que el regex)
    try:
        reparado = json_repair.loads(res_json)
        if isinstance(reparado, dict) and reparado:
            logger.info(f"JSON reparado localmente para {tipo_documento} (sin reintento a Qwen).")
            return reparado
    except Exception as e:
        logger.warning(f"json_repair falló para {tipo_documento}: {e}")

    schema = _get_format_schema(tipo_documento)

    # Etapa 3: retry con misma config + seed distinta (descarta alucinación puntual)
    logger.warning(f"Reintentando {tipo_documento} con seed alternativa (misma config).")
    res_json_2 = _llamar_qwen(
        prompt_sistema, prompt_usuario,
        limites["num_ctx"], limites["num_predict"], limites["timeout"],
        seed=1337,
        format_schema=schema,
    )
    json_str_2 = _extraer_json_str(res_json_2)
    if json_str_2:
        try:
            return json.loads(json_str_2)
        except json.JSONDecodeError:
            pass
    try:
        reparado_2 = json_repair.loads(res_json_2)
        if isinstance(reparado_2, dict) and reparado_2:
            logger.info(f"JSON reparado localmente tras retry con seed para {tipo_documento}.")
            return reparado_2
    except Exception:
        pass

    # Etapa 4: retry con límites aumentados (último recurso, caro)
    retry_ctx = min(limites["num_ctx"] * 2, 65536)
    retry_predict = min(limites["num_predict"] * 2, 49152)
    retry_timeout = min(limites["timeout"] * 2, 3600)
    logger.warning(
        f"Reintentando {tipo_documento} con límites aumentados "
        f"(num_ctx={retry_ctx}, num_predict={retry_predict}, timeout={retry_timeout}s)."
    )
    res_json_3 = _llamar_qwen(
        prompt_sistema, prompt_usuario,
        num_ctx=retry_ctx, num_predict=retry_predict, timeout=retry_timeout,
        format_schema=schema,
    )
    json_str_3 = _extraer_json_str(res_json_3)
    if json_str_3:
        try:
            return json.loads(json_str_3)
        except json.JSONDecodeError:
            pass
    try:
        reparado_3 = json_repair.loads(res_json_3)
        if isinstance(reparado_3, dict) and reparado_3:
            return reparado_3
    except Exception:
        pass

    raise ValueError(
        f"La IA no devolvió JSON válido tras 3 reintentos para {tipo_documento}. "
        f"Última respuesta (tail): ...{res_json_3[-500:]}"
    )


def detectar_regiones(imagenes: list) -> list[list]:
    """Corre solo DetectionPredictor y devuelve [[x1,y1,x2,y2], ...] por página."""
    cargar_modelos()
    resultados = _detection_predictor(imagenes)
    paginas = []
    for pagina in resultados:
        bboxes = []
        for det in pagina.bboxes:
            b = getattr(det, "bbox", None)
            if b and len(b) == 4:
                bboxes.append(list(b))
        paginas.append(bboxes)
    return paginas


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
