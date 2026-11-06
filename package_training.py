"""Entrenamiento de paquetes: TEO aprende medidas, peso y costo real de envío de cada combo.

Tras confirmarse el pago de un combo o protocolo (autorización final de Control), TEO le pide a Armando
los datos reales del paquete hasta juntar MAX_SAMPLES registros por combo. Los datos se guardan en
`package_profiles` y se usan solo como referencia para Armando en sus cotizaciones (el cálculo
automático del envío no cambia). Nada de esto interfiere con el flujo del cliente.
"""
import logging

import storage
from product_images import _normalize
from shipping import COMBO_PRODUCTS

logger = logging.getLogger("teo.paquetes")

MAX_SAMPLES = 5
LIMITS = {"lado_cm": (1, 200), "peso_kg": (0.05, 70), "costo": (0, 5000)}
_COMBOS = sorted(((_normalize(k), k) for k in COMBO_PRODUCTS), key=lambda item: -len(item[0]))

_cfg = {"send_text": None}   # async (http, to, text) -> None  (con retención de team_hours)
_logistics_phone = None


def configure(send_text, logistics_phone: str) -> None:
    global _logistics_phone
    _cfg["send_text"] = send_text
    _logistics_phone = logistics_phone


def combo_key(product: str):
    """Nombre canónico del combo/protocolo contenido en `product`, o None si es un producto suelto."""
    norm = _normalize(product or "")
    if not norm:
        return None
    for key_norm, key in _COMBOS:
        if key_norm in norm:
            return key
    return None


def _label(key: str) -> str:
    return key.title()


def should_ask(order: dict) -> bool:
    if (order.get("kind") or "pedido") != "pedido" or order.get("package_requested_at"):
        return False
    key = combo_key(order.get("product"))
    return bool(key) and storage.count_package_profiles(key) < MAX_SAMPLES


def request_text(order: dict) -> str:
    key = combo_key(order.get("product"))
    customer = storage.get_customer(order["phone"]) or {}
    who = f"{customer['name']} ({order['phone']})" if customer.get("name") else order["phone"]
    return (
        f"📦 *Entrenamiento de paquetes*\nPara el pedido de {who} — {order.get('product')}: cuando lo empaques, "
        f"mándame las medidas de la caja (largo×ancho×alto en cm), el peso (kg) y el costo aproximado del envío. "
        f"Ej.: \"30x20x15, 3.2 kg, $180\".\nLlevo {storage.count_package_profiles(key)}/{MAX_SAMPLES} registros de "
        f"{_label(key)}. ¡Con esto cotizamos cada vez más preciso! 🙌"
    )


async def ask_armando(http, order: dict) -> None:
    await _cfg["send_text"](http, _logistics_phone, request_text(order))
    storage.mark_package_requested(order["id"])
    logger.info("Datos de paquete solicitados a logística para la orden %s (%s).", order["id"], order.get("product"))


def _number(value):
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except ValueError:
        return None


async def register(http, telefono_cliente: str = None, producto: str = None, largo: float = None,
                   ancho: float = None, alto: float = None, peso_kg: float = None, costo_envio: float = None,
                   paqueteria: str = None, notas: str = None, recorded_by: str = None, **_ignored) -> str:
    dims = [_number(largo), _number(ancho), _number(alto)]
    weight, cost = _number(peso_kg), _number(costo_envio)
    missing = [name for name, v in (("largo", dims[0]), ("ancho", dims[1]), ("alto", dims[2]), ("peso", weight),
                                    ("costo del envío", cost)) if v is None]
    if missing:
        return f"ERROR: me falta {', '.join(missing)} del paquete; pídeselo a Armando (no lo inventes)."
    lo, hi = LIMITS["lado_cm"]
    if any(not lo <= d <= hi for d in dims):
        return f"ERROR: cada medida debe estar entre {lo} y {hi} cm; confírmalas con Armando."
    lo, hi = LIMITS["peso_kg"]
    if not lo <= weight <= hi:
        return f"ERROR: el peso debe estar entre {lo} y {hi} kg; confírmalo con Armando."
    lo, hi = LIMITS["costo"]
    if not lo <= cost <= hi:
        return f"ERROR: el costo del envío debe estar entre ${lo} y ${hi:,}; confírmalo con Armando."

    order = storage.get_package_pending_order(telefono_cliente) if telefono_cliente else None
    if telefono_cliente and not order:
        order = storage.get_latest_order_for_phone(telefono_cliente)
    if not order and not producto:
        order = storage.get_package_pending_order()
    product = producto or (order or {}).get("product")
    key = combo_key(product)
    if not key:
        return ("ERROR: no identifico de qué combo o protocolo es este paquete; pregúntale a Armando el combo o el "
                "teléfono del cliente.")
    storage.save_package_profile((order or {}).get("id") if order and combo_key(order.get("product")) == key else None,
                                 product, key, *dims, weight, cost, paqueteria, notas, recorded_by)
    n = storage.count_package_profiles(key)
    logger.info("Paquete registrado: %s %sx%sx%s cm, %s kg, $%s (%s registros).", key, *dims, weight, cost, n)
    progress = f"{n}/{MAX_SAMPLES} registros" if n <= MAX_SAMPLES else f"{n} registros"
    return (f"✅ Guardado: {_label(key)} {dims[0]:g}×{dims[1]:g}×{dims[2]:g} cm, {weight:g} kg, envío ~${cost:,.0f} "
            f"({progress}). ¡Gracias, Armando!")


def reference_line(product: str) -> str:
    key = combo_key(product)
    ref = storage.package_reference(key) if key else None
    if not ref:
        return ""
    return (f"📚 Referencia real de este combo ({ref['n']} paquete{'s' if ref['n'] != 1 else ''}): "
            f"~{ref['l']:.0f}×{ref['w']:.0f}×{ref['h']:.0f} cm, ~{ref['kg']:.2f} kg "
            f"(rango {ref['kg_min']:g}–{ref['kg_max']:g} kg), envío típico ~${ref['cost']:,.0f} "
            f"(rango ${ref['cost_min']:,.0f}–${ref['cost_max']:,.0f})")


async def view_references(http, producto: str = None, **_ignored) -> str:
    if producto:
        key = combo_key(producto)
        if not key:
            return f"No identifico el combo '{producto}'."
        line = reference_line(key)
        return line or f"Aún no hay paquetes registrados de {_label(key)}."
    rows = storage.list_package_profiles(limit=200)
    keys = sorted({r["combo_key"] for r in rows})
    if not keys:
        return "Aún no hay paquetes registrados."
    return "📚 Referencias de paquetes:\n" + "\n".join(f"• {_label(k)}: {reference_line(k).split(': ', 1)[1]}" for k in keys)
