"""
standards — Vocabularios controlados de comercio exterior.

Listas de referencia que se inyectan en los prompts de extracción para que
el LLM mapee los valores observados en cualquier formato/idioma a un
vocabulario estándar. Modificar aquí actualiza todos los prompts.
"""

# Unidades de medida estándar (subset de UN/CEFACT Recomendación 20 +
# uso común en facturación y aduana chilena).
UNIDADES_MEDIDA = [
    "U",      # Unidad / pieza
    "PCS",    # Pieces (alternativa común a U)
    "PR",     # Par
    "DOZ",    # Docena
    "SET",    # Conjunto
    "KG",     # Kilogramo
    "G",      # Gramo
    "MG",     # Miligramo
    "TON",    # Tonelada métrica
    "LB",     # Libra
    "OZ",     # Onza
    "M",      # Metro
    "CM",     # Centímetro
    "MM",     # Milímetro
    "KM",     # Kilómetro
    "M2",     # Metro cuadrado
    "M3",     # Metro cúbico
    "L",      # Litro
    "ML",     # Mililitro
    "GAL",    # Galón
    "BOX",    # Caja
    "CTN",    # Cartón
    "PKG",    # Paquete
    "BAG",    # Bolsa / saco
    "PAL",    # Pallet
    "ROLL",   # Rollo
    "DRM",    # Tambor
    "BBL",    # Barril
    "BDL",    # Bundle / fardo
    "CT",     # Quilate
]

# ISO 4217 — monedas más usadas en comercio exterior.
MONEDAS = [
    "USD", "EUR", "CLP", "CNY", "JPY", "GBP", "AUD", "CAD",
    "CHF", "HKD", "KRW", "SGD", "INR", "BRL", "MXN", "ARS",
    "PEN", "COP", "UYU", "BOB", "PYG", "VES", "ZAR", "NZD",
    "SEK", "NOK", "DKK", "PLN", "TRY", "RUB", "AED", "SAR",
    "ILS", "TWD", "THB", "MYR", "IDR", "PHP", "VND",
]

# ISO 3166-1 alpha-2 — códigos de país (socios comerciales frecuentes).
PAISES_ISO_2 = {
    "AR": "ARGENTINA", "AU": "AUSTRALIA", "BE": "BELGIUM", "BO": "BOLIVIA",
    "BR": "BRAZIL", "CA": "CANADA", "CL": "CHILE", "CN": "CHINA",
    "CO": "COLOMBIA", "DE": "GERMANY", "EC": "ECUADOR", "ES": "SPAIN",
    "FR": "FRANCE", "GB": "UNITED KINGDOM", "HK": "HONG KONG", "ID": "INDONESIA",
    "IN": "INDIA", "IT": "ITALY", "JP": "JAPAN", "KR": "SOUTH KOREA",
    "MX": "MEXICO", "MY": "MALAYSIA", "NL": "NETHERLANDS", "PE": "PERU",
    "PH": "PHILIPPINES", "PT": "PORTUGAL", "PY": "PARAGUAY", "RU": "RUSSIA",
    "SG": "SINGAPORE", "TH": "THAILAND", "TR": "TURKEY", "TW": "TAIWAN",
    "US": "UNITED STATES", "UY": "URUGUAY", "VE": "VENEZUELA", "VN": "VIETNAM",
    "ZA": "SOUTH AFRICA",
}

# Incoterms 2020.
INCOTERMS = [
    # Cualquier modo de transporte
    "EXW", "FCA", "CPT", "CIP", "DAP", "DPU", "DDP",
    # Solo marítimo / fluvial
    "FAS", "FOB", "CFR", "CIF",
]

# UN/CEFACT Recommendation 21 — tipos de embalaje más comunes.
TIPOS_EMBALAJE = [
    "BAG", "BALE", "BARREL", "BOX", "BUNDLE", "CARTON", "CASE",
    "CONTAINER", "CRATE", "DRUM", "PACKAGE", "PALLET", "PIECE",
    "ROLL", "SACK", "SET", "SHEET", "TANK", "TUBE", "UNIT",
]

# Términos de pago habituales en facturación internacional.
TERMINOS_PAGO = [
    "T/T",            # Telegraphic Transfer (transferencia bancaria SWIFT)
    "L/C",            # Letter of Credit (carta de crédito)
    "D/P",            # Documents against Payment
    "D/A",            # Documents against Acceptance
    "CAD",            # Cash Against Documents
    "CASH",           # Pago contado
    "OPEN_ACCOUNT",   # Cuenta corriente / pago a plazo
    "PREPAID",        # Anticipo total
    "ADVANCE",        # Pago por adelantado
]

# Condiciones de flete en BL.
TERMINOS_FLETE = ["PREPAID", "COLLECT"]

# Modos de transporte.
MODOS_TRANSPORTE = ["SEA", "AIR", "ROAD", "RAIL", "MULTIMODAL", "COURIER"]

# Tipos de factura.
TIPOS_FACTURA = [
    "COMMERCIAL",   # Factura comercial
    "PROFORMA",     # Factura proforma
    "CREDIT_NOTE",  # Nota de crédito
    "DEBIT_NOTE",   # Nota de débito
    "TAX",          # Factura tributaria / DTE
]

# Tipos de BL.
TIPOS_BL = [
    "MASTER",   # Master Bill of Lading (MBL)
    "HOUSE",    # House Bill of Lading (HBL)
    "DIRECT",   # BL directo (sin master/house)
    "SEAWAY",   # Sea Waybill
    "SWITCH",   # Switch BL
]

# Tipos de servicio en contenedores.
TIPOS_SERVICIO = ["CY/CY", "CY/DOOR", "DOOR/CY", "DOOR/DOOR", "FCL/FCL", "LCL/LCL", "FCL/LCL", "LCL/FCL"]
