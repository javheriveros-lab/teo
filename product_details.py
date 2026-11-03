"""Fichas técnicas extendidas por producto (ingrediente por ingrediente, tips de sinergia) —
separadas del SYSTEM_PROMPT a propósito, para no inflar el costo por token de cada mensaje.
TEO las consulta bajo demanda cada vez que el cliente pregunta por un producto."""

from pathlib import Path

from product_images import _normalize

DETAILS_DIR = Path(__file__).parent / "product_details"

# nombre canónico -> archivo .txt. Las fichas salen de "Nuevas fichas de productos.txt"; las de
# Biotic y Fiber Balance son curadas a mano (la de Fiber Balance tiene la dosis corregida).
PRODUCT_DETAIL_FILES = {
    "Bisxantone": "bisxantone.txt",
    "Biotic": "biotic.txt",
    "Breathe Strong": "breathe_strong.txt",
    "Cardio Slim": "cardio_slim.txt",
    "Colon Light": "colon_light.txt",
    "Control Fast": "control_fast.txt",
    "Control One": "control_one.txt",
    "Dulce Vida": "dulce_vida.txt",
    "Energy Gel": "energy_gel.txt",
    "Energy Max": "energy_max.txt",
    "Fiber Balance": "fiber_balance.txt",
    "Fiber Tabs": "fiber_tabs.txt",
    "Hecen Mangosteen": "hecen_mangosteen.txt",
    "Je Suis La Vie": "je_suis_la_vie.txt",
    "Magic Gel": "magic_gel.txt",
    "Memory Kids": "memory_kids.txt",
    "Omega Plus": "omega_plus.txt",
    "Power Milk": "power_milk.txt",
    "Power Shake": "power_shake.txt",
    "Renover Shot": "renover_shot.txt",
    "Rica Leche": "rica_leche.txt",
    "Shape Maker": "shape_maker.txt",
    "Sheyck Clorofila": "sheyck_clorofila.txt",
    "Star Bone": "star_bone.txt",
    "Star Coffe": "star_coffe.txt",
    "Super Leche": "super_leche.txt",
    "Sweet Free": "sweet_free.txt",
}

_NORMALIZED_DETAIL_INDEX = {_normalize(name): (name, filename) for name, filename in PRODUCT_DETAIL_FILES.items()}


def find_product_detail(nombre: str):
    """Busca la ficha técnica extendida de un producto por nombre/alias parcial. Devuelve
    (nombre_canonico, texto_completo) si existe, o None si no hay ficha detallada para ese
    producto todavía (no todos los productos la tienen)."""
    if not nombre:
        return None
    query = _normalize(nombre)
    if not query:
        return None

    match = None
    if query in _NORMALIZED_DETAIL_INDEX:
        match = _NORMALIZED_DETAIL_INDEX[query]
    else:
        best = None
        for normalized_name, pair in _NORMALIZED_DETAIL_INDEX.items():
            if query in normalized_name or normalized_name in query:
                if best is None or len(normalized_name) < len(best[0]):
                    best = (normalized_name, pair)
        if best:
            match = best[1]

    if not match:
        return None

    name, filename = match
    try:
        text = (DETAILS_DIR / filename).read_text(encoding="utf-8")
    except OSError:
        return None
    return name, text
