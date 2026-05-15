"""
standards — Vocabularios controlados de comercio exterior + validadores.

Listas de referencia que se inyectan en los prompts de extracción para que
el LLM mapee los valores observados en cualquier formato/idioma a un
vocabulario estándar. Modificar aquí actualiza todos los prompts.

También expone validadores/correctores aplicados como post-procesamiento
sobre la salida del LLM (ej. corregir comunas chilenas mal OCR'eadas).
"""
import re
import unicodedata

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


# Comunas de Chile (346) — fuente: SUBDERE / División Político-Administrativa.
# Se usan para detectar y corregir errores de OCR en direcciones (ej: "MAÑUL" → "MACUL").
COMUNAS_CHILE = [
    # XV Arica y Parinacota
    "ARICA", "CAMARONES", "PUTRE", "GENERAL LAGOS",
    # I Tarapacá
    "IQUIQUE", "ALTO HOSPICIO", "POZO ALMONTE", "CAMIÑA", "COLCHANE", "HUARA", "PICA",
    # II Antofagasta
    "ANTOFAGASTA", "MEJILLONES", "SIERRA GORDA", "TALTAL", "CALAMA", "OLLAGUE",
    "SAN PEDRO DE ATACAMA", "TOCOPILLA", "MARIA ELENA",
    # III Atacama
    "COPIAPO", "CALDERA", "TIERRA AMARILLA", "CHAÑARAL", "DIEGO DE ALMAGRO",
    "VALLENAR", "ALTO DEL CARMEN", "FREIRINA", "HUASCO",
    # IV Coquimbo
    "LA SERENA", "COQUIMBO", "ANDACOLLO", "LA HIGUERA", "PAIGUANO", "VICUÑA",
    "ILLAPEL", "CANELA", "LOS VILOS", "SALAMANCA", "OVALLE", "COMBARBALA",
    "MONTE PATRIA", "PUNITAQUI", "RIO HURTADO",
    # V Valparaíso
    "VALPARAISO", "CASABLANCA", "CONCON", "JUAN FERNANDEZ", "PUCHUNCAVI",
    "QUINTERO", "VIÑA DEL MAR", "ISLA DE PASCUA", "LOS ANDES", "CALLE LARGA",
    "RINCONADA", "SAN ESTEBAN", "LA LIGUA", "CABILDO", "PAPUDO", "PETORCA",
    "ZAPALLAR", "QUILLOTA", "CALERA", "HIJUELAS", "LA CRUZ", "NOGALES",
    "SAN ANTONIO", "ALGARROBO", "CARTAGENA", "EL QUISCO", "EL TABO",
    "SANTO DOMINGO", "SAN FELIPE", "CATEMU", "LLAILLAY", "PANQUEHUE",
    "PUTAENDO", "SANTA MARIA", "QUILPUE", "LIMACHE", "OLMUE", "VILLA ALEMANA",
    # XIII Metropolitana de Santiago
    "SANTIAGO", "CERRILLOS", "CERRO NAVIA", "CONCHALI", "EL BOSQUE",
    "ESTACION CENTRAL", "HUECHURABA", "INDEPENDENCIA", "LA CISTERNA",
    "LA FLORIDA", "LA GRANJA", "LA PINTANA", "LA REINA", "LAS CONDES",
    "LO BARNECHEA", "LO ESPEJO", "LO PRADO", "MACUL", "MAIPU",
    "ÑUÑOA", "PEDRO AGUIRRE CERDA", "PEÑALOLEN", "PROVIDENCIA", "PUDAHUEL",
    "QUILICURA", "QUINTA NORMAL", "RECOLETA", "RENCA", "SAN JOAQUIN",
    "SAN MIGUEL", "SAN RAMON", "VITACURA", "PUENTE ALTO", "PIRQUE",
    "SAN JOSE DE MAIPO", "COLINA", "LAMPA", "TILTIL", "SAN BERNARDO",
    "BUIN", "CALERA DE TANGO", "PAINE", "MELIPILLA", "ALHUE", "CURACAVI",
    "MARIA PINTO", "SAN PEDRO", "TALAGANTE", "EL MONTE", "ISLA DE MAIPO",
    "PADRE HURTADO", "PEÑAFLOR",
    # VI Libertador General Bernardo O'Higgins
    "RANCAGUA", "CODEGUA", "COINCO", "COLTAUCO", "DOÑIHUE", "GRANEROS",
    "LAS CABRAS", "MACHALI", "MALLOA", "MOSTAZAL", "OLIVAR", "PEUMO",
    "PICHIDEGUA", "QUINTA DE TILCOCO", "RENGO", "REQUINOA", "SAN VICENTE",
    "PICHILEMU", "LA ESTRELLA", "LITUECHE", "MARCHIHUE", "NAVIDAD", "PAREDONES",
    "SAN FERNANDO", "CHEPICA", "CHIMBARONGO", "LOLOL", "NANCAGUA",
    "PALMILLA", "PERALILLO", "PLACILLA", "PUMANQUE", "SANTA CRUZ",
    # VII Maule
    "TALCA", "CONSTITUCION", "CUREPTO", "EMPEDRADO", "MAULE", "PELARCO",
    "PENCAHUE", "RIO CLARO", "SAN CLEMENTE", "SAN RAFAEL", "CAUQUENES",
    "CHANCO", "PELLUHUE", "CURICO", "HUALAÑE", "LICANTEN", "MOLINA",
    "RAUCO", "ROMERAL", "SAGRADA FAMILIA", "TENO", "VICHUQUEN", "LINARES",
    "COLBUN", "LONGAVI", "PARRAL", "RETIRO", "SAN JAVIER", "VILLA ALEGRE", "YERBAS BUENAS",
    # XVI Ñuble
    "CHILLAN", "BULNES", "CHILLAN VIEJO", "EL CARMEN", "PEMUCO", "PINTO",
    "QUILLON", "SAN IGNACIO", "YUNGAY", "QUIRIHUE", "COBQUECURA", "COELEMU",
    "NINHUE", "PORTEZUELO", "RANQUIL", "TREHUACO", "SAN CARLOS", "COIHUECO",
    "ÑIQUEN", "SAN FABIAN", "SAN NICOLAS",
    # VIII Biobío
    "CONCEPCION", "CORONEL", "CHIGUAYANTE", "FLORIDA", "HUALPEN", "HUALQUI",
    "LOTA", "PENCO", "SAN PEDRO DE LA PAZ", "SANTA JUANA", "TALCAHUANO",
    "TOME", "LEBU", "ARAUCO", "CAÑETE", "CONTULMO", "CURANILAHUE", "LOS ALAMOS",
    "TIRUA", "LOS ANGELES", "ANTUCO", "CABRERO", "LAJA", "MULCHEN",
    "NACIMIENTO", "NEGRETE", "QUILACO", "QUILLECO", "SAN ROSENDO",
    "SANTA BARBARA", "TUCAPEL", "YUMBEL", "ALTO BIOBIO",
    # IX La Araucanía
    "TEMUCO", "CARAHUE", "CHOLCHOL", "CUNCO", "CURARREHUE", "FREIRE",
    "GALVARINO", "GORBEA", "LAUTARO", "LONCOCHE", "MELIPEUCO", "NUEVA IMPERIAL",
    "PADRE LAS CASAS", "PERQUENCO", "PITRUFQUEN", "PUCON", "SAAVEDRA",
    "TEODORO SCHMIDT", "TOLTEN", "VILCUN", "VILLARRICA", "ANGOL", "COLLIPULLI",
    "CURACAUTIN", "ERCILLA", "LONQUIMAY", "LOS SAUCES", "LUMACO", "PUREN",
    "RENAICO", "TRAIGUEN", "VICTORIA",
    # XIV Los Ríos
    "VALDIVIA", "CORRAL", "LANCO", "LOS LAGOS", "MAFIL", "MARIQUINA",
    "PAILLACO", "PANGUIPULLI", "LA UNION", "FUTRONO", "LAGO RANCO", "RIO BUENO",
    # X Los Lagos
    "PUERTO MONTT", "CALBUCO", "COCHAMO", "FRESIA", "FRUTILLAR", "LOS MUERMOS",
    "LLANQUIHUE", "MAULLIN", "PUERTO VARAS", "CASTRO", "ANCUD", "CHONCHI",
    "CURACO DE VELEZ", "DALCAHUE", "PUQUELDON", "QUEILEN", "QUELLON",
    "QUEMCHI", "QUINCHAO", "OSORNO", "PUERTO OCTAY", "PURRANQUE", "PUYEHUE",
    "RIO NEGRO", "SAN JUAN DE LA COSTA", "SAN PABLO", "CHAITEN", "FUTALEUFU",
    "HUALAIHUE", "PALENA",
    # XI Aysén del General Carlos Ibáñez del Campo
    "COYHAIQUE", "LAGO VERDE", "AYSEN", "CISNES", "GUAITECAS", "COCHRANE",
    "OHIGGINS", "TORTEL", "CHILE CHICO", "RIO IBAÑEZ",
    # XII Magallanes y de la Antártica Chilena
    "PUNTA ARENAS", "LAGUNA BLANCA", "RIO VERDE", "SAN GREGORIO", "CABO DE HORNOS",
    "ANTARTICA", "PORVENIR", "PRIMAVERA", "TIMAUKEL", "NATALES", "TORRES DEL PAINE",
]


def _normalizar_ascii(texto: str) -> str:
    """Quita tildes y ñ para comparar (MAÑUL ↔ MANUL, ÑUÑOA ↔ NUNOA)."""
    return "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    ).upper()


# Lookup precomputado: clave normalizada (sin tildes/ñ) → comuna canónica.
_COMUNAS_NORMALIZADAS = {_normalizar_ascii(c): c for c in COMUNAS_CHILE}

# Tokens cortos a ignorar como candidatos (evita matchear "DE", "LA", "EL").
_STOPWORDS = {"DE", "LA", "EL", "LOS", "LAS", "DEL", "Y", "EN", "A", "PARA"}


def _distancia_levenshtein(a: str, b: str) -> int:
    """Distancia de edición clásica (inserción/borrado/sustitución = 1)."""
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    fila_prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        fila = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            ins = fila[j - 1] + 1
            dele = fila_prev[j] + 1
            sub = fila_prev[j - 1] + (ca != cb)
            fila[j] = min(ins, dele, sub)
        fila_prev = fila
    return fila_prev[-1]


def corregir_comunas_chilenas(texto: str) -> str:
    """
    Corrige errores de OCR en nombres de comunas chilenas dentro de un texto.

    Estrategia conservadora: una palabra se corrige a una comuna conocida solo si
    cumple todo lo siguiente (comparando sin tildes/ñ y en mayúsculas):
      - longitud ≥ 4
      - distancia de Levenshtein ≤ 2 con la comuna candidata
      - diferencia de longitud ≤ 1
      - no es ya una comuna válida

    Caso de uso: "LOS INDUSTRIALES 2858 MAÑUL,SANTIAGO" → "...MACUL,SANTIAGO".
    Evita falsos positivos como MANUEL → MACUL (Levenshtein 2 pero |Δlen|=1, ratio bajo).
    """
    if not texto or not isinstance(texto, str):
        return texto

    def _intentar_corregir(palabra: str) -> str:
        if len(palabra) < 4 or palabra.upper() in _STOPWORDS:
            return palabra
        norm = _normalizar_ascii(palabra)
        if norm in _COMUNAS_NORMALIZADAS:
            return palabra  # ya es una comuna válida

        # Solo corregimos con distancia de edición = 1 sobre palabras de ≥5 chars.
        # Más permisivo genera falsos positivos (CALLE→OVALLE, MANUEL→MACUL, etc.).
        if len(norm) < 5:
            return palabra
        for clave_norm, canonico in _COMUNAS_NORMALIZADAS.items():
            if abs(len(clave_norm) - len(norm)) > 1:
                continue
            if _distancia_levenshtein(norm, clave_norm) == 1:
                return canonico
        return palabra

    return re.sub(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9]{4,}", lambda m: _intentar_corregir(m.group(0)), texto)


# Claves de los JSON extraídos cuyos valores son direcciones o ciudades chilenas.
_CLAVES_DIRECCION_CIUDAD = {"address", "city", "place_of_delivery", "place_of_issue", "place_of_receipt"}


def corregir_direcciones_chilenas(datos):
    """
    Recorre recursivamente un dict/list y aplica corregir_comunas_chilenas() sobre
    los valores de cualquier clave en _CLAVES_DIRECCION_CIUDAD. Modifica in-place
    y también retorna el objeto.
    """
    if isinstance(datos, dict):
        for k, v in datos.items():
            if k in _CLAVES_DIRECCION_CIUDAD and isinstance(v, str):
                datos[k] = corregir_comunas_chilenas(v)
            else:
                corregir_direcciones_chilenas(v)
    elif isinstance(datos, list):
        for item in datos:
            corregir_direcciones_chilenas(item)
    return datos
