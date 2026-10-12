"""Fichas técnicas extendidas por producto (ingrediente por ingrediente, tips de sinergia) —
separadas del SYSTEM_PROMPT a propósito, para no inflar el costo por token de cada mensaje.
TEO las consulta bajo demanda solo cuando el cliente pide el detalle completo de un producto."""

from pathlib import Path

from product_images import _normalize

DETAILS_DIR = Path(__file__).parent / "product_details"

# nombre canónico -> archivo .txt. Solo se agregan productos conforme se documentan a detalle;
# no es necesario tener uno para los 30 productos.
PRODUCT_DETAIL_FILES = {
    "Biotic": "biotic.txt",
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
