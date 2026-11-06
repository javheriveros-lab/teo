"""Precio público calculado en código (no por el modelo) para cotizaciones y descuentos.

Los precios son los de la lista PRECIOS PÚBLICO del SYSTEM_PROMPT (main.py), en la presentación
estándar; la composición de combos se reutiliza de shipping.COMBO_PRODUCTS. Si cambia la lista
de precios o el catálogo, hay que actualizar ambos lados.
"""
from product_images import _normalize
from shipping import COMBO_PRODUCTS

PUBLIC_PRICES = {
    "breathe strong": 235,
    "bisxantone": 545,                 # presentación estándar 1/2 litro
    "bisxantone pet": 1082,
    "bisxantone cristal": 1231,
    "bisxantone coffee latte": 882,
    "sheyck clorofila": 391,
    "sheyck clorofila max": 500,
    "cardio slim": 560,
    "dulce vida": 322,
    "je suis la vie": 1364,
    "hecen mangosteen": 1327,
    "mangosteen": 1327,
    "renover shot": 364,
    "colon light": 523,
    "control one": 618,
    "control fast": 429,
    "fiber tabs": 523,
    "memory kids": 551,
    "omega plus": 571,
    "sweet free": 523,
    "star bone": 718,
    "fiber balance": 569,
    "power milk": 382,
    "power shake": 438,
    "rica leche": 227,
    "star coffe": 420,
    "star coffee": 420,
    "super leche": 736,                # Super Leche Sin Lactosa
    "super leche sin lactosa": 736,
    "shape maker": 713,                # Shape Maker Café
    "shape maker cafe": 713,
    "energy gel": 291,
    "magic gel": 313,
    "redu line": 358,
    "redu gel": 289,
}
_PRICES = {_normalize(k): v for k, v in PUBLIC_PRICES.items()}
_COMBOS = {_normalize(k): v for k, v in COMBO_PRODUCTS.items()}


def _single_price(name: str):
    """Precio de un producto suelto: coincidencia exacta o la clave más larga contenida."""
    norm = _normalize(name)
    if norm in _PRICES:
        return _PRICES[norm]
    matches = [key for key in _PRICES if key and key in norm]
    return _PRICES[max(matches, key=len)] if matches else None


def quote_public_price(product_or_combo: str):
    """(total, [(producto, precio)]) del precio público, o None si algún componente no tiene
    precio en la lista (ej. Energy Max) o el nombre no se reconoce."""
    norm = _normalize(product_or_combo or "")
    if not norm:
        return None
    for combo, products in sorted(_COMBOS.items(), key=lambda item: -len(item[0])):
        if combo in norm:
            breakdown = [(p.title(), _single_price(p)) for p in products]
            if any(price is None for _, price in breakdown):
                return None
            return sum(price for _, price in breakdown), breakdown
    price = _single_price(norm)
    return (price, [(product_or_combo.strip(), price)]) if price is not None else None


# --- Membresía de socio (inscripción) ---------------------------------------
ENROLLMENT_FEE = 1720              # MXN, cuota única de inscripción
ENROLLMENT_GIFT = "Combo Detox"    # regalo de bienvenida incluido en la cuota
MEMBER_DISCOUNT_PCT = 30           # descuento de por vida en todo el catálogo


def enrollment_offer() -> dict:
    """Cifras de la membresía para cualquier texto que las mencione (nunca escritas a mano)."""
    gift = quote_public_price(ENROLLMENT_GIFT)
    return {
        "fee": ENROLLMENT_FEE,
        "gift": ENROLLMENT_GIFT,
        "gift_value": gift[0] if gift else None,
        "discount_pct": MEMBER_DISCOUNT_PCT,
    }


def member_price(public_amount: float, discount_pct: float = None) -> float:
    """Precio con el descuento de la ficha verificada (por defecto el estándar de socio)."""
    pct = MEMBER_DISCOUNT_PCT if discount_pct is None else discount_pct
    return round(public_amount * (1 - pct / 100), 2)


def commission_amount(volume: float, commission_pct: float) -> float:
    """Comisión del líder: volumen pagado por su red directa (con descuento, sin envío) × su %."""
    return round(volume * commission_pct / 100, 2)
