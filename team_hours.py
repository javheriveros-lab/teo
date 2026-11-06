"""Horario laboral, Modo Comida del equipo y lista de espera (turnos) de TEO.

- Horario (CDMX): lunes a viernes 9:00–20:00, sábado 9:00–15:00, domingo cerrado.
- Fuera de horario se retienen los avisos al equipo operativo (Control, Logística, Ventas y Altas) y
  se entregan al abrir, precedidos de un resumen ordenado de turnos.
- Modo Comida: cualquier persona del staff lo activa ("modo comida"); dura 90 minutos y retiene sus
  avisos. Al terminar, TEO le escribe con buena vibra y le entrega el resumen y sus pendientes.
- Turnos: si un trámite del cliente (pago, cotización, validación, alta) necesita a un área donde no
  hay nadie disponible, el cliente recibe su número en la lista de espera.
- Las respuestas a un mensaje que escribió la propia persona del staff nunca se retienen (main lo marca).

main.py inyecta las áreas, los nombres, los envíos "crudos" (sin retención) y, en pruebas, el reloj.
"""
import asyncio
import base64
import json
import logging
import re
import unicodedata
from datetime import datetime, timedelta

import storage
from scheduler import TIMEZONE

logger = logging.getLogger("teo.horario")

OPEN_HOUR = 9
CLOSE_HOUR = {0: 20, 1: 20, 2: 20, 3: 20, 4: 20, 5: 15}     # lunes=0 … sábado=5; domingo cerrado
LUNCH_MINUTES = 90
WINDOW_HOURS = 23.5
TEMPLATE_PENDIENTES = "teo_turnos_pendientes"   # {{1}} nombre, {{2}} número de pendientes
DAY_NAMES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
KIND_LABELS = {"pago": "Revisar pago", "cotizacion": "Cotizar envío", "validacion": "Validar ID de distribuidor",
               "alta": "Revisar alta de socio"}

LUNCH_START_COMMANDS = {"modo comida", "me voy a comer", "voy a comer", "hora de comida"}
LUNCH_END_COMMANDS = {"ya regrese", "regrese", "fin comida", "fin de comida", "ya volvi", "volvi"}
PENDING_COMMANDS = {"pendientes", "mis pendientes", "lista de espera"}

_cfg = {
    "areas": {},            # area -> tuple(phones): control, logistica, ventas, altas
    "names": {},            # last10 -> nombre corto
    "staff": {},            # last10 -> número completo de todo el staff (cualquiera usa Modo Comida)
    "send_raw": None,       # async (http, to, kind, payload: dict) -> bool   (sin retención)
    "clock": None,          # () -> datetime con zona horaria (pruebas)
}


def configure(**kwargs) -> None:
    unknown = set(kwargs) - set(_cfg)
    if unknown:
        raise ValueError(f"Opciones desconocidas: {unknown}")
    _cfg.update(kwargs)


def now() -> datetime:
    return _cfg["clock"]() if _cfg["clock"] else datetime.now(TIMEZONE)


def _iso(dt: datetime) -> str:
    return dt.astimezone(TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


def _parse(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TIMEZONE)


def _last10(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")[-10:]


def _name(phone: str) -> str:
    return _cfg["names"].get(_last10(phone), "")


def normalize_command(text: str) -> str:
    text = unicodedata.normalize("NFKD", (text or "").strip().lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z ]", "", text).strip()


# ---------------------------------------------------------------------------
# Horario y disponibilidad
# ---------------------------------------------------------------------------

def is_business_hours(moment: datetime = None) -> bool:
    moment = (moment or now()).astimezone(TIMEZONE)
    close = CLOSE_HOUR.get(moment.weekday())
    return close is not None and OPEN_HOUR <= moment.hour < close


def next_opening(moment: datetime = None) -> datetime:
    moment = (moment or now()).astimezone(TIMEZONE)
    candidate = moment.replace(hour=OPEN_HOUR, minute=0, second=0, microsecond=0)
    if candidate <= moment:
        candidate += timedelta(days=1)
    while candidate.weekday() not in CLOSE_HOUR:
        candidate += timedelta(days=1)
    return candidate


def opening_text(moment: datetime = None) -> str:
    """'hoy a las 9:00', 'mañana a las 9:00' o 'el lunes a las 9:00'."""
    moment = (moment or now()).astimezone(TIMEZONE)
    opening = next_opening(moment)
    days = (opening.date() - moment.date()).days
    when = "hoy" if days == 0 else "mañana" if days == 1 else f"el {DAY_NAMES[opening.weekday()]}"
    return f"{when} a las {OPEN_HOUR}:00"


def schedule_text() -> str:
    return "lunes a viernes de 9:00 a 20:00 y sábados de 9:00 a 15:00"


def _areas_of(phone: str) -> list:
    last10 = _last10(phone)
    return [area for area, phones in _cfg["areas"].items() if last10 in {_last10(p) for p in phones}]


def active_lunch(phone: str):
    brk = storage.get_active_break(phone)
    if brk and _parse(brk["ends_at"]) > now():
        return brk
    return None


def is_staff(phone: str) -> bool:
    return _last10(phone) in _cfg["staff"]


def is_available(phone: str, moment: datetime = None) -> bool:
    if active_lunch(phone):
        return False
    if _areas_of(phone) and not is_business_hours(moment):
        return False
    return True


def area_available(area: str) -> bool:
    return any(is_available(p) for p in _cfg["areas"].get(area, ()))


def should_hold(to: str, reply_to: str = None) -> bool:
    if not to or not is_staff(to):
        return False
    if reply_to and _last10(reply_to) == _last10(to):
        return False
    return not is_available(to)


def resume_text(area: str) -> str:
    """Cuándo vuelve a haber alguien del área: regreso de comida (en horario) o la siguiente apertura."""
    if is_business_hours():
        ends = [_parse(b["ends_at"]) for p in _cfg["areas"].get(area, ()) for b in [active_lunch(p)] if b]
        if ends:
            return f"en cuanto regrese de su comida, alrededor de las {min(ends).strftime('%H:%M')}"
    return opening_text()


# ---------------------------------------------------------------------------
# Lista de espera (turnos)
# ---------------------------------------------------------------------------

def take_turn(client_phone: str, area: str, kind: str, ref_id: int = None) -> int:
    ticket_id = storage.open_wait_ticket(client_phone, area, kind, ref_id, _iso(now()))
    position = storage.wait_position(ticket_id)
    logger.info("Turno #%s en %s para %s (%s).", position, area, client_phone, kind)
    return position


def turn_if_unavailable(client_phone: str, area: str, kind: str, ref_id: int = None):
    """Número de turno si nadie del área está disponible; None si alguien puede atender ya."""
    if area_available(area):
        return None
    return take_turn(client_phone, area, kind, ref_id)


def resolve(client_phone: str, area: str) -> None:
    storage.resolve_wait_tickets(client_phone, area, _iso(now()))


def current_turn(client_phone: str):
    ticket = storage.get_open_wait_ticket(client_phone)
    return (ticket, storage.wait_position(ticket["id"])) if ticket else (None, None)


def pending_summary(phone: str) -> str:
    tickets = storage.list_open_wait_tickets(_areas_of(phone))
    held = storage.list_held_messages(phone)
    if not tickets and not held:
        return "No tienes pendientes en la lista de espera. 🙌"
    lines = []
    for position, t in enumerate(tickets, start=1):
        customer = storage.get_customer(t["client_phone"]) or {}
        who = f"{customer.get('name') + ' ' if customer.get('name') else ''}({t['client_phone']})"
        since = _parse(t["created_at"]).strftime("%d/%m %H:%M")
        lines.append(f"{position}. {KIND_LABELS.get(t['kind'], t['kind'])} — {who}, desde {since}")
    text = "📋 *Tu lista de espera*\n" + ("\n".join(lines) if lines else "Sin turnos de clientes.")
    if held:
        text += f"\n\nTe guardé {len(held)} aviso(s); van enseguida, en orden."
    return text


# ---------------------------------------------------------------------------
# Avisos retenidos
# ---------------------------------------------------------------------------

def hold(to: str, kind: str, payload: dict) -> None:
    data = dict(payload)
    if isinstance(data.get("data"), (bytes, bytearray)):
        data["data"] = base64.b64encode(data["data"]).decode()
        data["_b64"] = True
    storage.hold_message(to, kind, json.dumps(data, ensure_ascii=False), _iso(now()))
    logger.info("Aviso %s para %s retenido (fuera de horario o en comida).", kind, to)


def _decode(row: dict) -> dict:
    data = json.loads(row["payload"])
    if data.pop("_b64", False):
        data["data"] = base64.b64decode(data["data"])
    return data


def _in_window(phone: str) -> bool:
    hours = storage.hours_since_inbound(phone)
    return hours is not None and hours <= WINDOW_HOURS


def _greeting(phone: str, reason: str) -> str:
    name = _name(phone)
    hi = f", {name}" if name else ""
    if reason == "regreso_comida":
        return (f"¡Bienvenid@ de vuelta{hi}! 😊🍽️ Espero que hayas disfrutado tu comida y recargado pilas. "
                f"Ya reactivé tus avisos; aquí va lo que te guardé para que lo retomes con calma y en orden ✨")
    return (f"¡Buenos días{hi}! ☀️ Que tengas una jornada increíble. Esto quedó en tu lista de espera mientras "
            f"descansabas:")


async def flush(http, phone: str, reason: str) -> int:
    """Entrega lo retenido para `phone`. Devuelve cuántos avisos se entregaron."""
    held = storage.list_held_messages(phone)
    if not held and reason != "regreso_comida":
        return 0
    send = _cfg["send_raw"]
    if not _in_window(phone) and reason != "respondio":
        if any(m["status"] == "retenido" for m in held):
            await send(http, phone, "template", {"name": TEMPLATE_PENDIENTES,
                                                 "params": [_name(phone) or "equipo", str(len(held))]})
            storage.set_held_status([m["id"] for m in held], "esperando_respuesta")
            logger.info("%s tiene %s aviso(s) retenidos; se le pidió responder (fuera de la ventana de 24 h).",
                        phone, len(held))
        return 0
    if not is_available(phone):
        if reason == "regreso_comida":
            await send(http, phone, "text", {"text": _greeting(phone, reason).split(" Ya reactivé")[0] +
                                             f" Tu horario ya terminó; te entrego tus pendientes {opening_text()}. 🌙"})
        return 0
    await send(http, phone, "text", {"text": _greeting(phone, reason) + "\n\n" + pending_summary(phone)})
    delivered = []
    for row in held:
        try:
            await send(http, phone, row["kind"], _decode(row))
            delivered.append(row["id"])
        except Exception:
            logger.exception("No se pudo entregar el aviso retenido %s a %s.", row["id"], phone)
    storage.set_held_status(delivered, "entregado", _iso(now()))
    logger.info("Entregados %s aviso(s) retenidos a %s (%s).", len(delivered), phone, reason)
    return len(delivered)


async def on_staff_inbound(http, phone: str) -> None:
    """La persona del staff escribió: si ya está disponible, recibe lo que esperaba su respuesta."""
    if not is_available(phone):
        return
    if storage.list_held_messages(phone):
        await flush(http, phone, "respondio")


# ---------------------------------------------------------------------------
# Modo Comida
# ---------------------------------------------------------------------------

def start_lunch(phone: str) -> str:
    start = now()
    ends = start + timedelta(minutes=LUNCH_MINUTES)
    storage.start_break(phone, _iso(start), _iso(ends))
    logger.info("Modo Comida activado por %s hasta %s.", phone, ends.strftime("%H:%M"))
    name = _name(phone)
    return (f"¡Buen provecho{', ' + name if name else ''}! 🍽️ Pauso tus avisos durante 1 hora y media, hasta las "
            f"{ends.strftime('%H:%M')}, y te guardo todo en orden. Si regresas antes, escríbeme \"ya regresé\".")


async def end_lunch(http, phone: str) -> bool:
    brk = storage.get_active_break(phone)
    if not brk:
        return False
    storage.end_break(brk["id"], _iso(now()))
    await flush(http, phone, "regreso_comida")
    return True


async def handle_command(http, phone: str, text: str):
    """Comandos del staff. Devuelve el texto de respuesta si era un comando, o None."""
    command = normalize_command(text)
    if command in LUNCH_START_COMMANDS:
        return start_lunch(phone)
    if command in LUNCH_END_COMMANDS:
        if await end_lunch(http, phone):
            return ""          # el mensaje de regreso ya salió con el resumen
        return "No tenías activo el Modo Comida; tus avisos están llegando normal. 🙌"
    if command in PENDING_COMMANDS:
        return pending_summary(phone)
    return None


# ---------------------------------------------------------------------------
# Loop
# ---------------------------------------------------------------------------

async def tick(http) -> None:
    current = now()
    for brk in storage.list_open_breaks():
        if _parse(brk["ends_at"]) <= current:
            storage.end_break(brk["id"], _iso(current))
            phone = _cfg["staff"].get(brk["phone_last10"])
            if phone:
                await flush(http, phone, "regreso_comida")
    for phone in storage.held_recipients("retenido"):
        if is_available(phone):
            await flush(http, phone, "apertura")


async def run_hours_loop(http, check_seconds: int = 60) -> None:
    logger.info("Loop de horario laboral y Modo Comida iniciado (%s).", schedule_text())
    while True:
        try:
            await tick(http)
        except Exception:
            logger.exception("Error en el loop de horario laboral.")
        await asyncio.sleep(check_seconds)
