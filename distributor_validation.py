"""Validación de ID de distribuidor con Ventas (Belén) antes de aplicar cualquier descuento.

Ningún descuento existe hasta que Belén lo confirma: TEO le manda una consulta estructurada (3
preguntas clave + historial del ID para detectar suplantaciones), le recuerda cada 15 minutos
mientras no conteste (8:00–21:00 CDMX) y, con su respuesta, le comunica al cliente el precio con
texto fijo (sin pasar por el modelo, para que las cifras no se alteren).

main.py llama `configure()` con la URL y el token de WhatsApp y el número de Belén.
"""
import asyncio
import logging
import re
from datetime import datetime

import storage
from scheduler import TIMEZONE, _send, _text_payload

logger = logging.getLogger("teo.descuentos")

TEMPLATE_VALIDACION = "teo_validacion_distribuidor"
REMINDER_MINUTES = 15
ACTIVE_HOURS = (8, 21)          # recordatorios solo de 8:00 a 20:59 hora CDMX
MAX_DISCOUNT_PCT = 60

_api_url = None
_token = None
_sales_phone = None
_history_key = None
# Disponibilidad de Belén (horario laboral / Modo Comida), inyectada por main vía team_hours.
_is_available = None
_hold_text = None


def set_availability(is_available, hold_text) -> None:
    """`is_available(phone) -> bool`; `hold_text(phone, text)` guarda un aviso para cuando regrese."""
    global _is_available, _hold_text
    _is_available, _hold_text = is_available, hold_text


def _sales_available(moment: datetime = None) -> bool:
    return _is_available is None or _is_available(_sales_phone, moment)


def configure(api_url: str, token: str, sales_phone: str, history_key=None) -> None:
    """`history_key(phone)`: clave del historial de Belén (sales_coach), para que la consulta
    quede en su conversación y una respuesta corta se entienda."""
    global _api_url, _token, _sales_phone, _history_key
    _api_url, _token, _sales_phone, _history_key = api_url, token, sales_phone, history_key


def parse_money(value):
    if value is None:
        return None
    match = re.search(r"\d[\d,]*(?:\.\d+)?", str(value))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def money(value: float) -> str:
    return f"${value:,.2f}".replace(".00", "")


def _one_line(text: str, limit: int = 300) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _history_text(history: dict) -> str:
    if not history["usos_previos"]:
        return "primera vez que se usa"
    parts = [f"usado antes {history['usos_previos']} vez/veces con {history['telefonos_distintos']} teléfono(s) distinto(s)"]
    last = history["validaciones"][-1]
    if last.get("estado"):
        pct = f" al {last['porcentaje']:g}%" if last.get("porcentaje") else ""
        parts.append(f"última validación: {last['estado']}{pct}")
    text = "; ".join(parts)
    if history["telefonos_distintos"] >= 2:
        text = "⚠️ " + text + " — revisa posible suplantación"
    return text


def request_summary(order: dict, customer: dict) -> dict:
    name = (customer or {}).get("name") or "sin nombre"
    history = storage.get_distributor_id_history(order["distributor_id"], exclude_order_id=order["id"])
    return {
        "cliente": f"{name} ({order['phone']})",
        "id": order["distributor_id"],
        "pedido": f"{order.get('product') or 'producto no especificado'}, {order.get('amount_public') or order.get('amount_expected') or 'monto no especificado'} (precio público, sin envío)",
        "historial": _history_text(history),
    }


def build_request_text(order: dict, customer: dict, reminder_number: int = 0) -> str:
    s = request_summary(order, customer)
    header = (f"⏰ Recordatorio #{reminder_number}: sigue pendiente esta validación, el cliente está esperando su precio.\n\n"
              if reminder_number else "")
    return (
        f"{header}🔐 *Validación de descuento de distribuidor*\n"
        f"Cliente: {s['cliente']}\n"
        f"ID de empresario: *{s['id']}*\n"
        f"Pedido: {s['pedido']}\n"
        f"Historial del ID: {s['historial']}\n\n"
        f"Por favor respóndeme:\n"
        f"1) ¿Este ID está *activo y verificado* en el sistema?\n"
        f"2) ¿Qué *porcentaje o nivel de descuento* le corresponde hoy?\n"
        f"3) ¿Aplica a *todo el pedido* o tiene restricciones en algún combo/producto? (si hay restricciones, "
        f"dime el monto final del producto)\n\n"
        f"Ejemplo: \"{order['phone']}: activo, 30%, aplica a todo\". Mientras no me respondas, el cliente no "
        f"recibe total ni datos de pago."
    )


async def notify_sales(http_client, order: dict, reminder_number: int = 0) -> bool:
    """Manda la consulta a Belén con la plantilla (para llegar fuera de la ventana de 24 h) y, si
    falla, como texto libre. La deja en el historial de Belén."""
    if not _sales_available():
        # Belén descansa o está comiendo: la consulta queda pendiente y el loop se la manda al volver.
        logger.info("Validación de la orden %s en espera: Ventas no está disponible.", order["id"])
        return False
    customer = storage.get_customer(order["phone"])
    s = request_summary(order, customer)
    params = [_one_line(s["cliente"], 120), _one_line(s["id"], 60), _one_line(s["pedido"], 300),
              _one_line(("Recordatorio #%d. " % reminder_number if reminder_number else "") + s["historial"], 300)]
    sent = await _send(http_client, _api_url, _token, _text_payload(_sales_phone, TEMPLATE_VALIDACION, params),
                       _sales_phone, "validacion_distribuidor")
    text = build_request_text(order, customer, reminder_number)
    if not sent:
        payload = {"messaging_product": "whatsapp", "to": _sales_phone, "type": "text", "text": {"body": text}}
        sent = await _send(http_client, _api_url, _token, payload, _sales_phone, "validacion_distribuidor_texto")
    if _history_key:
        storage.log_message(_history_key(_sales_phone), "assistant", text)
    return sent


async def notify_sales_text(http_client, text: str) -> None:
    if not _sales_available() and _hold_text:
        _hold_text(_sales_phone, text)
        return
    payload = {"messaging_product": "whatsapp", "to": _sales_phone, "type": "text", "text": {"body": text}}
    await _send(http_client, _api_url, _token, payload, _sales_phone, "aviso_descuento")
    if _history_key:
        storage.log_message(_history_key(_sales_phone), "assistant", text)


def compute_discount(public_amount: float, pct: float = None, explicit_amount: float = None):
    """Monto del producto con descuento. `explicit_amount` (cuando Belén da el monto final por
    restricciones) tiene prioridad; si no, público × (1 − pct)."""
    if explicit_amount is not None:
        return round(explicit_amount, 2)
    return round(public_amount * (1 - pct / 100), 2)


def client_price_message(order: dict, name: str = None) -> str:
    """Mensaje formal al cliente con el resultado de la validación (aprobada o no)."""
    saludo = f"{name}, " if name else ""
    public = parse_money(order.get("amount_public")) or parse_money(order.get("amount_expected")) or 0
    shipping = parse_money(order.get("shipping_cost")) if order.get("status") == "pendiente_pago" else None
    if order["discount_status"] == "aprobado":
        product = parse_money(order.get("amount_expected")) or public
        pct = order.get("discount_pct")
        pct_text = f" del *{pct:g}%*" if pct else ""
        lines = [
            f"✅ {saludo}¡listo! Nuestro equipo validó tu ID de empresario *{order['distributor_id']}* y tienes un "
            f"descuento{pct_text} en tu pedido.",
            "",
            f"Producto ({order.get('product') or 'tu pedido'}):",
            f"• Precio público: {money(public)}",
            f"• Con tu beneficio: *{money(product)}* (ahorras {money(public - product)})",
        ]
        if order.get("discount_scope") == "restringido" and order.get("discount_notes"):
            lines.append(f"• Nota: {order['discount_notes']}")
    else:
        product = public
        lines = [
            f"{saludo}revisamos tu ID de empresario *{order['distributor_id']}* y por ahora no pudimos validarlo, "
            f"así que tu pedido queda a precio público: *{money(public)}*.",
            "Si crees que es un error, con gusto lo revisamos con el equipo de Ventas.",
        ]
    if shipping is not None:
        lines += ["", f"Envío ({order.get('shipping_choice')}): {money(shipping)}",
                  f"*Total a pagar: {money(product + shipping)}*"]
    else:
        lines += ["", "El costo del envío se suma en cuanto elijas tu opción de envío."]
    return "\n".join(lines)


async def run_reminder_loop(http_client) -> None:
    """Cada minuto: a las validaciones con 15 min o más desde el último aviso les manda un
    recordatorio a Belén (sin tope, solo en horario 8:00–21:00 CDMX)."""
    logger.info("Loop de recordatorios de validación de descuentos iniciado (cada %d min).", REMINDER_MINUTES)
    while True:
        try:
            await send_due_reminders(http_client)
        except Exception:
            logger.exception("Error inesperado en el loop de validación de descuentos.")
        await asyncio.sleep(60)


async def send_due_reminders(http_client, now: datetime = None) -> int:
    if _is_available is not None:
        # Sin hora explícita, la disponibilidad usa el reloj de team_hours (horario + Modo Comida).
        if not _sales_available(now):
            return 0
    elif not ACTIVE_HOURS[0] <= (now or datetime.now(TIMEZONE)).hour < ACTIVE_HOURS[1]:
        return 0
    sent = 0
    for order in storage.get_due_discount_reminders(REMINDER_MINUTES):
        number = (order.get("discount_reminders") or 0) + 1
        await notify_sales(http_client, order, reminder_number=number)
        storage.mark_discount_reminded(order["id"])
        logger.info("Recordatorio #%d de validación de descuento enviado (orden %s).", number, order["id"])
        sent += 1
    return sent
