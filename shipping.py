"""Cálculo de envío: distancia (haversine) desde un origen oculto de la empresa, peso
estimado del pedido, y tarifa según una tabla de tiers por distancia/peso.

IMPORTANTE: ninguna función de este módulo debe usarse para construir un mensaje que se le
muestre al cliente con las coordenadas o el nombre del origen — el origen es un dato interno
de la empresa y nunca se revela.
"""

import math

# Origen oculto de la empresa (resuelto una sola vez desde el enlace de Google Maps que dio el
# negocio). NUNCA se debe incluir en un mensaje de cara al cliente ni en el reporte que se le
# manda al número de cotización — solo se usa aquí, internamente, para calcular distancia.
_HIDDEN_ORIGIN_LAT = 19.4898771
_HIDDEN_ORIGIN_LNG = -99.0481002

EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Distancia en línea recta (km) entre dos coordenadas."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lng2 - lng1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return EARTH_RADIUS_KM * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def distance_from_origin_km(dest_lat: float, dest_lng: float) -> float:
    return haversine_km(_HIDDEN_ORIGIN_LAT, _HIDDEN_ORIGIN_LNG, dest_lat, dest_lng)


# Estación Metro Río de los Remedios (Línea B, límite CDMX/Edomex) — coordenadas resueltas por
# consulta pública. Es el punto de referencia OCULTO para sugerir puntos de encuentro en el
# Metro. NUNCA se debe incluir en un mensaje de cara al cliente — solo la usan internamente
# TEO y el reporte que recibe Armando (Logística), para que él confirme la estación real más
# cercana al punto medio calculado.
_RIO_DE_LOS_REMEDIOS_LAT = 19.490908
_RIO_DE_LOS_REMEDIOS_LNG = -99.046597


def estimate_metro_meetup_point(dest_lat: float, dest_lng: float) -> dict:
    """Calcula un punto medio geográfico simple entre la estación Río de los Remedios y el
    destino del cliente, como sugerencia de dónde ubicar un punto de encuentro en el Metro.

    IMPORTANTE: esto es una aproximación geográfica (promedio de coordenadas), NO un cálculo
    real sobre la red del Metro — no tenemos una base de datos de estaciones/líneas para
    determinar la estación exacta más cercana a ese punto medio. Armando debe confirmar la
    estación real basándose en su conocimiento de la línea antes de coordinar la cita."""
    mid_lat = (_RIO_DE_LOS_REMEDIOS_LAT + dest_lat) / 2
    mid_lng = (_RIO_DE_LOS_REMEDIOS_LNG + dest_lng) / 2
    return {
        "mid_lat": round(mid_lat, 6),
        "mid_lng": round(mid_lng, 6),
        "maps_link": f"https://www.google.com/maps?q={mid_lat},{mid_lng}",
    }


# ---------------------------------------------------------------------------
# PESOS DE PRODUCTO — catálogo oficial de I'AM (kg, CON envase: cristal, PET, bolsa, etc.).
# Fuente: tabulador oficial proporcionado por el negocio.
# Únicas excepciones sin dato oficial (usan DEFAULT_WEIGHT_KG de respaldo): Bisxantone Coffee
# Latte, Redu Line, Redu Gel. "Bisxantone 1/2 Litro" tampoco viene en la tabla oficial (que solo
# da la presentación de 1 litro/cristal y PET) — se estima por proporción, marcado abajo.
# ---------------------------------------------------------------------------
DEFAULT_WEIGHT_KG = 0.5  # respaldo para cualquier producto no listado abajo

PRODUCT_WEIGHTS_KG = {
    "je suis la vie": 1.4,
    "bisxantone cristal": 1.4,
    "bisxantone pet": 1.1,
    "bisxantone 1/2 litro": 0.8,  # ESTIMADO por proporción, no está en la tabla oficial
    "bisxantone": 1.4,  # genérico (1L/cristal) si no se especifica presentación
    "hecen mangosteen": 1.05,
    "mangosteen": 1.05,
    "sheyck clorofila max": 0.54,
    "sheyck clorofila": 0.54,
    "cardio slim": 0.54,
    # Cápsulas y capletas: máximo 100 g por unidad para todos los productos de esta línea.
    "colon light": 0.1,
    "control one": 0.1,
    "control fast": 0.1,
    "fiber tabs": 0.1,
    "memory kids": 0.1,
    "omega plus": 0.1,
    "energy max": 0.1,
    "sweet free": 0.1,
    "star bone": 0.1,
    "super leche sin lactosa": 0.51,
    "super leche": 0.51,
    "fiber balance": 0.45,
    "rica leche": 0.43,
    "shape maker café": 0.13,
    "shape maker": 0.13,
    "star coffe": 0.26,
    "power milk": 0.26,
    "power shake": 0.515,
    "renover shot": 0.05,
    "breathe strong": 0.3,
    "magic gel": 0.3,
    "energy gel": 0.3,
    "dulce vida": 0.04,
}

# Composición de combos/protocolos (misma lista que el catálogo del SYSTEM_PROMPT en main.py —
# si el catálogo cambia ahí, hay que actualizar esto también para que el peso no quede desfasado).
COMBO_PRODUCTS = {
    "combo alergias": ["sheyck clorofila", "je suis la vie", "breathe strong", "memory kids"],
    "combo anemia": ["sheyck clorofila", "colon light", "hecen mangosteen", "memory kids"],
    "combo longevidad celular": ["je suis la vie", "hecen mangosteen", "memory kids"],
    "combo blindaje cardiovascular": ["cardio slim", "bisxantone", "omega plus"],
    "protocolo reconstrucción articular": ["omega plus", "bisxantone", "star bone", "super leche", "memory kids"],
    "combo detox": ["sheyck clorofila", "colon light", "fiber tabs", "bisxantone"],
    "combo respiratorio": ["breathe strong", "sheyck clorofila", "bisxantone", "memory kids"],
    "combo circulatorio muscular": ["omega plus", "bisxantone"],
    "combo buena circulación": ["sheyck clorofila", "bisxantone", "omega plus", "cardio slim"],
    "combo lípidos": ["omega plus", "cardio slim", "bisxantone"],
    "combo blindaje neuro-cognitivo": ["bisxantone", "memory kids"],
    "protocolo control glucémico": ["sheyck clorofila", "sweet free", "hecen mangosteen", "power milk"],
    "combo diverticulosis": ["fiber balance", "colon light", "bisxantone"],
    "protocolo fluidez digestiva": ["fiber balance", "sheyck clorofila", "bisxantone"],
    "vitality upgrade system": ["sheyck clorofila", "energy max", "je suis la vie", "power milk"],
    "protocolo desinflamación": ["omega plus", "bisxantone", "hecen mangosteen"],
    "combo vitalidad hormonal": ["hecen mangosteen", "omega plus"],
    "combo restauración digestiva": ["sheyck clorofila", "bisxantone", "fiber tabs"],
    "combo blindaje respiratorio": ["breathe strong", "hecen mangosteen", "sheyck clorofila"],
    "combo hemorroides": ["fiber tabs", "sheyck clorofila", "cardio slim", "bisxantone", "energy gel"],
    "combo purificación hepática": ["sheyck clorofila", "fiber balance", "je suis la vie"],
    "combo optimización cognitiva": ["omega plus", "memory kids", "sheyck clorofila"],
    "protocolo estabilización neuro-emocional": ["sheyck clorofila", "bisxantone"],
    "combo reset digestivo": ["fiber balance", "sheyck clorofila", "colon light", "bisxantone"],
    "protocolo blindaje cognitivo": ["omega plus", "je suis la vie", "memory kids"],
    "combo migraña": ["sheyck clorofila", "fiber balance", "je suis la vie", "memory kids"],
    "combo densidad ósea": ["super leche", "star bone", "je suis la vie", "memory kids"],
    "combo salud de colon": ["fiber balance", "sheyck clorofila", "bisxantone", "hecen mangosteen"],
    "combo desarrollo escolar": ["memory kids", "rica leche", "power milk"],
    "combo crecimiento infantil": ["memory kids", "power milk"],
    "combo soporte prostático": ["hecen mangosteen", "omega plus", "je suis la vie"],
    "combo armonización dérmica": ["omega plus", "hecen mangosteen"],
    "combo drenaje sistémico": ["fiber tabs", "colon light", "bisxantone"],
    "protocolo blindaje inmunológico": ["je suis la vie", "memory kids", "power milk"],
    "protocolo sobrepeso": ["bisxantone", "colon light", "shape maker", "power milk", "control fast"],
    "combo hipertrofia": ["power milk", "super leche", "memory kids", "bisxantone", "sweet free"],
    "combo restauración mucosa": ["bisxantone", "fiber tabs", "sheyck clorofila"],
    "combo varices": ["bisxantone", "omega plus", "cardio slim", "energy gel"],
    "combo úlcera varicosa": ["je suis la vie", "fiber balance", "colon light", "hecen mangosteen"],
    "protocolo estética estructural": ["colon light", "je suis la vie", "star bone"],
    "combo ligereza abdominal": ["fiber balance", "colon light", "energy gel", "control fast", "sheyck clorofila"],
    "combo agudeza visual": ["hecen mangosteen", "omega plus", "memory kids"],
    "combo asimilación de nutrientes": ["je suis la vie", "fiber balance", "colon light", "sheyck clorofila"],
    "combo cistitis": ["bisxantone", "colon light", "sheyck clorofila"],
    "combo colitis": ["bisxantone", "fiber tabs", "colon light", "sheyck clorofila max"],
    "combo fibrosis quística": ["bisxantone", "hecen mangosteen", "omega plus"],
}


def _weight_for_single_product(name_lower: str) -> float:
    if name_lower in PRODUCT_WEIGHTS_KG:
        return PRODUCT_WEIGHTS_KG[name_lower]
    for key, weight in PRODUCT_WEIGHTS_KG.items():
        if key in name_lower:
            return weight
    return DEFAULT_WEIGHT_KG


def estimate_weight_breakdown(product_or_combo_name: str) -> tuple:
    """Devuelve (lista_de_[producto, peso_kg]_por_separado, peso_total_kg) para poder mandarle
    al aprobador de cotización el desglose que pidió, no solo el total."""
    if not product_or_combo_name:
        return [], DEFAULT_WEIGHT_KG

    name_lower = product_or_combo_name.strip().lower()

    for combo_name, products in COMBO_PRODUCTS.items():
        if combo_name in name_lower:
            breakdown = [(p.title(), _weight_for_single_product(p)) for p in products]
            total = round(sum(w for _, w in breakdown), 3)
            return breakdown, total

    weight = _weight_for_single_product(name_lower)
    return [(product_or_combo_name, weight)], weight


def estimate_product_weight_kg(product_or_combo_name: str) -> float:
    """Estima el peso REAL total (kg) de lo que el cliente va a comprar, a partir del nombre
    del producto/protocolo. Si es un combo conocido, suma el peso de sus productos; si es un
    producto individual conocido, usa su peso; si no se reconoce nada, usa un respaldo
    genérico para no bloquear el cálculo. Este es el peso real — para el peso a facturar
    (real vs. volumétrico, el que sea mayor) usa `billable_weight_kg`."""
    _, total = estimate_weight_breakdown(product_or_combo_name)
    return total


# ---------------------------------------------------------------------------
# DIMENSIONES REALES DE PRODUCTO (ancho, alto en cm) — catálogo oficial de I'AM.
# La profundidad no se dio por separado: se usa una "profundidad estándar de base" fija
# (DEFAULT_DEPTH_CM, punto medio del rango 8-10cm que indicó el negocio) por cada producto,
# y se acumula (se suma) al armar la caja de un combo con varios productos.
# Únicas excepciones sin dato oficial (usan DEFAULT_DIMENSIONS_CM de respaldo): Bisxantone
# Coffee Latte, Redu Line, Redu Gel, Magic Gel, Energy Gel, Power Shake.
# ---------------------------------------------------------------------------
DEFAULT_DEPTH_CM = 9
DEFAULT_DIMENSIONS_CM = (12, 15)  # (ancho, alto) de respaldo para productos sin dato oficial

PRODUCT_DIMENSIONS_CM = {
    "je suis la vie": (15, 40),
    "bisxantone cristal": (15, 40),
    "bisxantone pet": (15, 30),  # ESTIMADO (más chico que la presentación de 1L), no está en la ficha oficial
    "bisxantone 1/2 litro": (15, 25),  # ESTIMADO, no está en la ficha oficial
    "bisxantone": (15, 40),  # genérico (1L/cristal) si no se especifica presentación
    "hecen mangosteen": (18, 30),
    "mangosteen": (18, 30),
    "sheyck clorofila max": (15, 20),
    "sheyck clorofila": (15, 20),
    "cardio slim": (15, 20),
    # Cápsulas y capletas: mismas dimensiones para todos los productos de esta línea.
    "colon light": (10, 13),
    "control one": (10, 13),
    "control fast": (10, 13),
    "fiber tabs": (10, 13),
    "memory kids": (10, 13),
    "omega plus": (10, 13),
    "energy max": (10, 13),
    "sweet free": (10, 13),
    "star bone": (10, 13),
    "super leche sin lactosa": (20, 25),
    "super leche": (20, 25),
    "fiber balance": (20, 25),
    "rica leche": (20, 30),
    "shape maker café": (10, 7),
    "shape maker": (10, 7),
    "star coffe": (15, 20),
    "power milk": (15, 20),
    "renover shot": (5, 10),
    "breathe strong": (7, 13),
    "dulce vida": (5, 8),
}


def _dimensions_for_single_product(name_lower: str) -> tuple:
    if name_lower in PRODUCT_DIMENSIONS_CM:
        return PRODUCT_DIMENSIONS_CM[name_lower]
    for key, dims in PRODUCT_DIMENSIONS_CM.items():
        if key in name_lower:
            return dims
    return DEFAULT_DIMENSIONS_CM


def estimate_box_dimensions_from_products(product_or_combo_name: str) -> tuple:
    """Devuelve (largo, ancho, alto) en cm de la caja necesaria para este producto/combo,
    calculada dinámicamente: ancho y alto son el MÁXIMO entre los productos incluidos (la caja
    debe ser al menos tan ancha/alta como el producto más grande), y el largo es la profundidad
    estándar acumulada (una por cada producto, ya que se acomodan uno tras otro)."""
    if not product_or_combo_name:
        return DEFAULT_DEPTH_CM, *DEFAULT_DIMENSIONS_CM

    name_lower = product_or_combo_name.strip().lower()

    for combo_name, products in COMBO_PRODUCTS.items():
        if combo_name in name_lower:
            dims = [_dimensions_for_single_product(p) for p in products]
            ancho = max(d[0] for d in dims)
            alto = max(d[1] for d in dims)
            largo = DEFAULT_DEPTH_CM * len(products)
            return largo, ancho, alto

    ancho, alto = _dimensions_for_single_product(name_lower)
    return DEFAULT_DEPTH_CM, ancho, alto


def volumetric_weight_kg(largo_cm: float, ancho_cm: float, alto_cm: float) -> float:
    """Peso volumétrico estándar en México: (Largo × Ancho × Alto en cm) / 5000."""
    return round((largo_cm * ancho_cm * alto_cm) / 5000, 3)


def billable_weight_kg(real_weight_kg: float, product_or_combo_name: str = None) -> dict:
    """Calcula peso real, peso volumétrico (con la caja real del producto/combo) y el peso a
    facturar (el mayor de los dos) — misma regla que usan Estafeta/FedEx."""
    largo, ancho, alto = estimate_box_dimensions_from_products(product_or_combo_name)
    vol_weight = volumetric_weight_kg(largo, ancho, alto)
    billable = max(real_weight_kg, vol_weight)
    return {
        "peso_real_kg": round(real_weight_kg, 3),
        "peso_volumetrico_kg": vol_weight,
        "peso_facturable_kg": round(billable, 3),
        "caja_cm": (largo, ancho, alto),
        "se_cobra_por": "volumétrico" if vol_weight > real_weight_kg else "real",
    }


# ---------------------------------------------------------------------------
# TARIFAS OFICIALES DE ESTAFETA Y FEDEX — confirmadas por el negocio, ya no son placeholder.
# No varían por peso (solo por zona) — ver MAX_BILLABLE_WEIGHT_KG para el límite a partir del
# cual se escala a un asesor humano en vez de cotizar automático.
# Zona se decide por distancia: <= ZONE_DISTANCE_THRESHOLD_KM = Local/Regional, más lejos =
# Nacional/General (umbral oficial confirmado por el negocio: 100 km).
# ---------------------------------------------------------------------------
ZONE_DISTANCE_THRESHOLD_KM = 100  # OFICIAL — hasta 100km = Local, más de 100km = Nacional
MAX_BILLABLE_WEIGHT_KG = 20  # ASUMIDO — límite de paquete estándar antes de escalar a un asesor

# (paqueteria, modalidad) -> {"local": precio, "nacional": precio, "eta": texto}
COURIER_RATES = {
    ("Estafeta", "Terrestre"): {"local": 130, "nacional": 180, "eta": "3-5 días hábiles"},
    ("Estafeta", "Express"): {"local": 200, "nacional": 280, "eta": "1-2 días hábiles"},
    ("FedEx", "Terrestre"): {"local": 140, "nacional": 190, "eta": "3-5 días hábiles"},
    ("FedEx", "Express"): {"local": 220, "nacional": 300, "eta": "1-2 días hábiles"},
}


def calculate_shipping_options(distance_km: float, weight_kg: float) -> list:
    """Devuelve las 4 opciones de envío (Estafeta/FedEx × Terrestre/Express) según la zona de
    la distancia dada. `weight_kg` debe ser el peso A FACTURAR (real vs. volumétrico, el mayor
    de los dos — ver `billable_weight_kg`). Si el peso a facturar excede el máximo estándar,
    devuelve una lista vacía — quien llame a esta función debe interpretarlo como 'hay que
    escalar a un asesor humano para cotizar este pedido', nunca inventar un precio."""
    if weight_kg > MAX_BILLABLE_WEIGHT_KG:
        return []

    zona = "local" if distance_km <= ZONE_DISTANCE_THRESHOLD_KM else "nacional"
    options = []
    for (paqueteria, modalidad), rates in COURIER_RATES.items():
        options.append({
            "nombre": f"{paqueteria} {modalidad}",
            "precio": rates[zona],
            "eta": rates["eta"],
        })
    return options
