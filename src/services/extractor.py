import os
import re
import json
import requests
from pdf2image import convert_from_path, pdfinfo_from_path
from surya.recognition import RecognitionPredictor
from surya.detection import DetectionPredictor

URL_OLLAMA = os.getenv("URL_OLLAMA")
MODELO = os.getenv("MODELO", "qwen3:14b")

_recognition_predictor = None
_detection_predictor = None


def cargar_modelos():
    global _recognition_predictor, _detection_predictor
    if _recognition_predictor is None:
        print("Cargando modelos de Surya en GPU...")
        _recognition_predictor = RecognitionPredictor()
        _detection_predictor = DetectionPredictor()
        print("Modelos cargados.")


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
        langs = [["es", "en"]] * len(imagenes)
        predicciones = _recognition_predictor(imagenes, langs, det_predictor=_detection_predictor)
        for pagina in predicciones:
            lineas = [linea.text for linea in pagina.text_lines]
            texto_pagina = "\n".join(lineas)
            texto_upper = texto_pagina.upper()
            palabras = len(texto_pagina.split())
            
            # Detectar si la página es "Terms & Conditions"
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


def extraer_bl(texto_documento: str) -> dict:
    prompt_sistema = f"""Eres un experto en documentos de comercio exterior y procesos aduaneros chilenos. Tu única tarea es extraer la información del texto proporcionado y mapearla estrictamente al esquema JSON dado. El texto OCR incluirá enormes bloques de "Terms & Conditions" y terminología legal marítima; IGNORA POR COMPLETO toda esa sección legal y enfócate únicamente en encontrar los datos del embarque (Shipper, Consignee, Contenedores, BL No, etc.). Si no encuentras información para un campo, usa null. NO te quejes de la complejidad del documento, NO des explicaciones, NO devuelvas errores ni sugerencias. Solo completa el JSON.

ESQUEMA EXACTO A DEVOLVER:
{{
  "bl_number": "Número del Bill of Lading (B/L No)",
  "ams_number": "AMS No, o null si no aparece",
  "carrier": "Nombre de la naviera/carrier emisora",
  "shipper": {{
    "name": "Razón social del exportador",
    "address": "Dirección completa en una sola línea"
  }},
  "consignee": {{
    "name": "Razón social del consignatario",
    "address": "Dirección completa",
    "tax_id": "RUT, NIT, EIN o equivalente, o null",
    "phone": "Teléfono o null",
    "contact": "Persona de contacto o null"
  }},
  "notify_party": {{
    "name": "Razón social del notify, o null si no aplica",
    "address": "Dirección o null",
    "tax_id": "Identificador fiscal o null",
    "phone": "Teléfono o null",
    "contact": "Persona de contacto o null"
  }},
  "delivery_agent": {{
    "name": "Nombre del agente de entrega en destino, o null",
    "address": "Dirección o null",
    "tax_id": "RUT/NIT o null",
    "phone": "Teléfono o null",
    "email": "Email o null"
  }},
  "vessel": "Nombre del buque",
  "voyage": "Número de viaje sin prefijos como 'V.' o 'Voy.' (ej: 0699-073E, no V.0699-073E)",
  "place_of_receipt": "Lugar de recepción",
  "port_of_loading": "Puerto de carga",
  "port_of_discharge": "Puerto de descarga",
  "place_of_delivery": "Lugar de entrega",
  "containers": [
    {{
      "container_no": "Número de contenedor (ej: EMCU8613665)",
      "seal_no": "Número de sello (ej: EMCULH6403)",
      "type": "Tipo y tamaño (ej: 40'HQ, 20'GP)",
      "packages_count": número entero de bultos,
      "packages_type": "Tipo de bulto (PACKAGES, CARTONS, PALLETS, etc.)",
      "gross_weight_kg": peso bruto en kg como número,
      "volume_cbm": volumen en m³ como número,
      "marks": "Marks and Numbers, o null",
      "description": "Descripción de la mercancía"
    }}
  ],
  "total_containers_text": "Texto literal del total (ej: 'ONE (1) X40HQ CONTAINER(S) ONLY')",
  "freight_terms": "PREPAID o COLLECT",
  "freight_amount": "Monto del flete con moneda. Si el campo en el bloque Freight and Charges está vacío pero el bloque aduanero tiene FLETE con valor (ej: USD CC 3500), usa ese mismo valor aquí. NUNCA dejes este campo en null si el bloque aduanero tiene flete.",
  "prepaid_at": "Lugar de prepago del flete (campo 'Prepaid at' del bloque Freight and Charges). Si la columna está vacía o el flete es COLLECT, devuelve null. NO uses el lugar de emisión del documento.",
  "payable_at": "Lugar de pago del flete tal como aparece literalmente en el campo 'Payable at' (ej: DESTINATION, ORIGIN, o un puerto específico). NO infieras este valor del puerto de descarga.",
  "number_of_originals": número entero de B/L originales emitidos,
  "place_of_issue": "Lugar de emisión",
  "date_of_issue": "Fecha de emisión en formato YYYY-MM-DD",
  "shipped_on_board_date": "Fecha shipped on board en YYYY-MM-DD",
  "datos_aduana_chile": {{
    "bl_master": "Número de BL Master",
    "bl_hijo": "Número de BL Hijo preservando paréntesis y caracteres tal como aparecen (ej: (H)SH249B0043)",
    "vapor": "Nombre del vapor",
    "viaje": "Número de viaje",
    "puerto_embarque": "PTO. EMB.",
    "puerto_transbordo": "PTO. TRANS. Si está vacío o solo contiene un guión, devuelve null",
    "puerto_descarga": "PTO. DESC.",
    "emisor_mbl": "EMISOR MBL",
    "fecha_mbl": "FECHA MBL en formato YYYY-MM-DD",
    "almacen": "Código de almacén (ej: STI)",
    "contenedor": "CTER",
    "sello": "SELLO",
    "flete": "FLETE tal como aparece literalmente (ej: USD CC 3500)",
    "flete_monto_usd": monto numérico del flete en USD, o null,
    "flete_modalidad": "PP (prepaid) o CC (collect), o null",
    "corregido_por": "Persona que corrigió el documento (campo CORREGIDO X del PDF)",
    "rut_corrector": "RUT del corrector (campo RUT del PDF)",
    "fecha_correccion": "Fecha de corrección en formato YYYY-MM-DD (campo FECHA del PDF)"
  }}
}}

REGLAS ESTRICTAS:
1. Valores que solo contengan guiones, rayas o variantes como "-", "—", "N/A", "NA" o cadena vacía deben convertirse a null. NO los devuelvas como string.
2. Si un campo no aparece en el documento, usa null. NO inventes datos.
3. Cada contenedor físico es UN objeto en el array containers. Si el documento dice "ONE (1) CONTAINER", el array tiene 1 elemento, no más.
4. container_no y seal_no son códigos distintos. El contenedor suele tener 4 letras + 7 dígitos (ej: EMCU8613665). El sello es el otro código alfanumérico.
5. Cuando veas formato "CODIGO1/CODIGO2/40'HQ/N PACKAGES/PESO KGS/VOLUMEN CBM", el primer código es container_no, el segundo es seal_no. NO los concatenes.
6. Pesos y volúmenes como números preservando los decimales tal como aparecen en el documento. Ejemplo: 14700.000 y 31.300, no 14700 ni "14,700 KGS".
7. Las fechas siempre en ISO YYYY-MM-DD. "12-Oct-2024" se convierte en "2024-10-12".
8. Si notify_party tiene los mismos datos que consignee, repite los datos igualmente, no uses null.
9. Direcciones en una sola línea, separando con comas, sin saltos de línea.
10. El bloque "CORRECCION APROBADA" / "INTEGRAL CHILE SA" debe extraerse completo en datos_aduana_chile. Es información crítica para el proceso aduanero chileno.
11. Si un campo del documento solo muestra su etiqueta (como "AMS No.") sin valor al lado, devuelve null. NUNCA copies la etiqueta como valor. Además, limpia caracteres residuales al inicio o final de los valores extraídos como "/", "-", ":", "," o espacios sobrantes que vengan de cortes del OCR.
12. En "marks", términos como "CY/CY", "FCL/FCL", "LCL/LCL", "DOOR/DOOR" son términos de servicio del contenedor, NO son marcas. Si la marca es "NM" o "N/M" significa "No Marks". En ambos casos devuelve null.
13. El campo FLETE del bloque aduanero sigue formato "MONEDA MODALIDAD MONTO" donde modalidad es PP (prepaid) o CC (collect). Ejemplo: "USD CC 3500" debe descomponerse en flete: "USD CC 3500", flete_monto_usd: 3500, flete_modalidad: "CC".
14. Las claves (keys) del JSON deben coincidir EXACTAMENTE con las del esquema proporcionado. NUNCA renombres claves basándote en las etiquetas del documento.
15. El bloque "datos_aduana_chile" SOLO debe llenarse si el documento contiene literalmente la sección "CORRECCION APROBADA" o "INTEGRAL CHILE SA". Si no aparece esa sección, devuelve TODOS los campos de datos_aduana_chile como null.
16. La estructura de containers puede aparecer como tabla con columnas "Cntr No / Seal No. / Sz/Ty / Qty / Pkg Type / Weight". En ese caso: container_no=Cntr No, seal_no=Seal No., type=Sz/Ty, packages_count=Qty, packages_type=Pkg Type, gross_weight_kg=Weight. NO uses los totales globales para campos individuales.
"""

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


def procesar_pdf(ruta_pdf: str) -> dict:
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

    return extraer_bl(texto)