"""Mapa de productos individuales a su foto real (extraída del catálogo oficial con fotos,
Catalogo i'am.pdf) y búsqueda flexible por nombre/alias para que TEO pueda enviar la imagen real
de un producto específico, sin depender de que el cliente escriba el nombre exacto."""

import re
import unicodedata
from pathlib import Path

IMAGES_DIR = Path(__file__).parent / "product_images"

# nombre canónico -> archivo. Los alias se resuelven por normalización + substring, no hace
# falta listarlos aquí (ej. "sheyck" o "clorofila" ya hacen match con "Sheyck Clorofila").
PRODUCT_IMAGE_FILES = {
    "Je Suis La Vie": "je_suis_la_vie.jpg",
    "Bisxantone": "bisxantone.jpg",
    "Hecen Mangosteen": "hecen_mangosteen.jpg",
    "Sheyck Clorofila": "sheyck_clorofila.jpg",
    "Sheyck Clorofila Max": "sheyck_clorofila_max.jpg",
    "Cardio Slim": "cardio_slim.jpg",
    "Omega Plus": "omega_plus.jpg",
    "Biotic": "biotic.jpg",
    "Energy Max": "energy_max.jpg",
    "Control Fast": "control_fast.jpg",
    "Fiber Tabs": "fiber_tabs.jpg",
    "Star Bone": "star_bone.jpg",
    "Control One": "control_one.jpg",
    "Memory Kids": "memory_kids.jpg",
    "Sweet Free": "sweet_free.jpg",
    "Colon Light": "colon_light.jpg",
    "Rica Leche": "rica_leche.jpg",
    "Super Leche": "super_leche.jpg",
    "Fiber Balance": "fiber_balance.jpg",
    "Shape Maker": "shape_maker.jpg",
    "Star Coffe": "star_coffe.jpg",
    "Power Milk": "power_milk.jpg",
    "Power Shake": "power_shake.jpg",
    "Renover Shot": "renover_shot.jpg",
    "Breathe Strong": "breathe_strong.jpg",
    "Magic Gel": "magic_gel.jpg",
    "Energy Gel": "energy_gel.jpg",
    "Dulce Vida": "dulce_vida.jpg",
}


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9 ]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


_NORMALIZED_INDEX = {_normalize(name): (name, filename) for name, filename in PRODUCT_IMAGE_FILES.items()}


def find_product_image(nombre: str):
    """Busca la imagen real de un producto por nombre o alias parcial. Devuelve
    (nombre_canonico, Path) si encuentra una coincidencia razonable, o None si no."""
    if not nombre:
        return None
    query = _normalize(nombre)
    if not query:
        return None

    # Coincidencia exacta primero.
    if query in _NORMALIZED_INDEX:
        name, filename = _NORMALIZED_INDEX[query]
        return name, IMAGES_DIR / filename

    # Coincidencia parcial: el nombre del producto contiene la consulta o viceversa.
    best = None
    for normalized_name, (name, filename) in _NORMALIZED_INDEX.items():
        if query in normalized_name or normalized_name in query:
            if best is None or len(normalized_name) < len(best[0]):
                best = (normalized_name, name, filename)
    if best:
        _, name, filename = best
        return name, IMAGES_DIR / filename

    return None
