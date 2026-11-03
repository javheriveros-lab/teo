import asyncio
import logging
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

import storage
from plans import DAILY_TIPS, MICRO_METAS, REST_LABEL

logger = logging.getLogger("teo.scheduler")

TIMEZONE = ZoneInfo("America/Mexico_City")
TEMPLATE_LANGUAGE = "es_MX"

TEMPLATE_CHECKIN_MATUTINO = "teo_checkin_matutino"
TEMPLATE_RECORDATORIO_DOSIS = "teo_recordatorio_dosis"
TEMPLATE_EDUCACION_RECETA = "teo_educacion_receta"
TEMPLATE_CHECKIN_NOCTURNO = "teo_checkin_nocturno"
TEMPLATE_RESUMEN_SEMANAL = "teo_resumen_semanal"
TEMPLATE_HABITO_DIARIO = "teo_habito_diario"

HABIT_HOUR = 9  # 9am hora de CDMX — envío diario del hábito a todos los clientes con plan activo
HABIT_MINUTE = 0

CHECK_INTERVAL_SECONDS = 30


def _display_name(customer: dict) -> str:
    if customer and customer.get("name"):
        return customer["name"]
    return "hola"


def _text_payload(phone: str, template_name: str, params: list) -> dict:
    return {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": TEMPLATE_LANGUAGE},
            "components": [{"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}],
        },
    }


async def _send(http_client: httpx.AsyncClient, api_url: str, token: str, payload: dict, phone: str, kind: str) -> bool:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        response = await http_client.post(api_url, headers=headers, json=payload)
        if response.status_code >= 400:
            logger.error(
                "No se pudo enviar '%s' a %s (%s): %s", kind, phone, response.status_code, response.text
            )
            return False
        return True
    except httpx.HTTPError:
        logger.exception("Fallo de red enviando '%s' a %s.", kind, phone)
        return False


async def _handle_checkin_matutino(http_client, api_url, token, phone, content) -> bool:
    customer = storage.get_customer(phone)
    name = _display_name(customer)
    meal_label = "tu comida"
    # meal_label real viene de la fila de reminders, se pasa por separado desde el loop principal.
    food = content["food_suggestion"] if content else "tu alimentación habitual"
    focus = content["training_focus"] if content else None
    is_rest = not focus or focus == REST_LABEL
    focus_text = "día de descanso, no toca entrenar" if is_rest else focus

    payload = _text_payload(phone, TEMPLATE_CHECKIN_MATUTINO, [name, meal_label, food, focus_text])
    return await _send(http_client, api_url, token, payload, phone, "checkin_matutino")


async def _handle_recordatorio_dosis(http_client, api_url, token, phone) -> bool:
    name = _display_name(storage.get_customer(phone))
    payload = _text_payload(phone, TEMPLATE_RECORDATORIO_DOSIS, [name])
    return await _send(http_client, api_url, token, payload, phone, "recordatorio_dosis")


async def _handle_educacion_receta(http_client, api_url, token, phone, day_of_week) -> bool:
    name = _display_name(storage.get_customer(phone))
    tip = DAILY_TIPS[day_of_week]
    payload = _text_payload(phone, TEMPLATE_EDUCACION_RECETA, [name, tip])
    return await _send(http_client, api_url, token, payload, phone, "educacion_receta")


async def _handle_checkin_nocturno(http_client, api_url, token, phone) -> bool:
    name = _display_name(storage.get_customer(phone))
    payload = _text_payload(phone, TEMPLATE_CHECKIN_NOCTURNO, [name])
    return await _send(http_client, api_url, token, payload, phone, "checkin_nocturno")


async def _handle_resumen_semanal(http_client, api_url, token, phone, now) -> bool:
    name = _display_name(storage.get_customer(phone))
    start = (now - timedelta(days=6)).date().isoformat()
    end = now.date().isoformat()
    summary = storage.get_weekly_summary(phone, start, end)
    days = str(summary["days_completed"])
    energy = str(summary["avg_energy"]) if summary["avg_energy"] is not None else "sin datos"
    meta = MICRO_METAS[now.isocalendar()[1] % len(MICRO_METAS)]
    payload = _text_payload(phone, TEMPLATE_RESUMEN_SEMANAL, [name, days, energy, meta])
    return await _send(http_client, api_url, token, payload, phone, "resumen_semanal")


async def run_reminder_loop(http_client: httpx.AsyncClient, api_url: str, token: str) -> None:
    logger.info("Scheduler de recordatorios iniciado (zona horaria %s).", TIMEZONE)
    while True:
        try:
            now = datetime.now(TIMEZONE)
            today_str = now.date().isoformat()
            due = storage.get_due_reminders(now.hour, now.minute, today_str)
            for reminder in due:
                phone, kind = reminder["phone"], reminder["kind"]
                content = storage.get_daily_content(phone, now.weekday())
                sent = False

                if kind == "checkin_matutino":
                    sent = await _handle_checkin_matutino(http_client, api_url, token, phone, content)
                elif kind == "recordatorio_dosis":
                    sent = await _handle_recordatorio_dosis(http_client, api_url, token, phone)
                elif kind == "educacion_receta":
                    sent = await _handle_educacion_receta(http_client, api_url, token, phone, now.weekday())
                elif kind == "checkin_nocturno":
                    sent = await _handle_checkin_nocturno(http_client, api_url, token, phone)
                elif kind == "resumen_semanal":
                    if now.weekday() != 6:  # Domingo=6; solo se envía ese día.
                        continue
                    sent = await _handle_resumen_semanal(http_client, api_url, token, phone, now)

                if sent:
                    storage.mark_reminder_sent(reminder["id"], today_str)
                    logger.info("'%s' enviado a %s.", kind, phone)
        except Exception:
            logger.exception("Error inesperado en el ciclo del scheduler de recordatorios.")

        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


def _template_param(text: str) -> str:
    """Meta rechaza parámetros de plantilla con saltos de línea, tabs o 4+ espacios seguidos."""
    return re.sub(r"\s+", " ", text or "").strip()


async def broadcast_habit(http_client: httpx.AsyncClient, api_url: str, token: str, habit: dict, today_str: str) -> int:
    """Manda un hábito (nombre, descripción y video) a todos los clientes con plan activo, con
    la plantilla aprobada para que llegue aunque el cliente no haya escrito en las últimas 24h.
    Marca el hábito como enviado hoy para que el loop diario no lo repita. Devuelve cuántos
    envíos tuvieron éxito."""
    params_tail = [_template_param(habit["name"]), _template_param(habit["description"]), habit["video_url"]]
    sent_count = 0
    for phone in storage.get_habit_recipients():
        name = _display_name(storage.get_customer(phone))
        payload = _text_payload(phone, TEMPLATE_HABITO_DIARIO, [name, *params_tail])
        if await _send(http_client, api_url, token, payload, phone, "habito_diario"):
            sent_count += 1
    storage.mark_habit_sent(habit["id"], today_str)
    logger.info("Hábito #%s '%s' enviado a %s cliente(s).", habit["id"], habit["name"], sent_count)
    return sent_count


async def run_habit_loop(http_client: httpx.AsyncClient, api_url: str, token: str) -> None:
    logger.info("Loop de hábitos diarios iniciado (hora configurada: %02d:%02d).", HABIT_HOUR, HABIT_MINUTE)
    while True:
        try:
            now = datetime.now(TIMEZONE)
            today_str = now.date().isoformat()
            # Ventana de una hora (no el minuto exacto) para que un redeploy justo a esa hora no
            # haga perder el envío del día; habit_sent_on evita duplicarlo dentro de la ventana.
            if (
                now.hour == HABIT_HOUR
                and now.minute >= HABIT_MINUTE
                and not storage.habit_sent_on(today_str)
            ):
                habit = storage.get_next_habit()
                if habit:
                    await broadcast_habit(http_client, api_url, token, habit, today_str)
        except Exception:
            logger.exception("Error inesperado en el loop de hábitos diarios.")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


# --- Seguimiento comercial (prospectos que aceptaron tips/promociones e interesados en el
# negocio). Loop separado del de hábitos: no toca su rotación ni su marca de envío diario.
TEMPLATE_SEGUIMIENTO = "teo_seguimiento"      # MARKETING: nombre, título, descripción, enlace/cierre
TEMPLATE_VIDEO_NEGOCIO = "teo_video_negocio"  # MARKETING: nombre, título, descripción, enlace

FOLLOWUP_WEEKDAYS = (1, 4)  # martes y viernes: máximo 2 mensajes por semana a cada prospecto
FOLLOWUP_HOUR = 11
BUSINESS_WEEKDAY = 3        # jueves: un video de negocio por semana
BUSINESS_HOUR = 12
FOLLOWUP_NO_LINK_TEXT = "Responde este mensaje si quieres saber más."


def _followup_payload(phone: str, template_name: str, item: dict) -> dict:
    name = _display_name(storage.get_customer(phone))
    params = [
        name,
        _template_param(item["title"]),
        _template_param(item["description"]),
        item["url"] or FOLLOWUP_NO_LINK_TEXT,
    ]
    return _text_payload(phone, template_name, params)


async def _send_followups(http_client, api_url, token, phones: list, sources: tuple, template_name: str) -> int:
    sent_count = 0
    for phone in phones:
        item = storage.next_followup_item(phone, sources)
        if not item:
            continue
        payload = _followup_payload(phone, template_name, item)
        if await _send(http_client, api_url, token, payload, phone, f"seguimiento_{item['source']}"):
            storage.log_followup(phone, item["source"], item["id"])
            sent_count += 1
    return sent_count


async def run_followup_loop(http_client: httpx.AsyncClient, api_url: str, token: str) -> None:
    logger.info("Loop de seguimiento comercial iniciado.")
    done = set()  # (fecha, tipo) ya procesados en este proceso; la BD evita duplicar tras un redeploy
    while True:
        try:
            now = datetime.now(TIMEZONE)
            today_str = now.date().isoformat()
            if now.weekday() in FOLLOWUP_WEEKDAYS and now.hour == FOLLOWUP_HOUR and (today_str, "prospectos") not in done:
                done.add((today_str, "prospectos"))
                count = await _send_followups(
                    http_client, api_url, token, storage.get_followup_prospects(),
                    ("habito", "promocion"), TEMPLATE_SEGUIMIENTO,
                )
                logger.info("Seguimiento a prospectos: %s mensaje(s) enviados.", count)
            if now.weekday() == BUSINESS_WEEKDAY and now.hour == BUSINESS_HOUR and (today_str, "negocio") not in done:
                done.add((today_str, "negocio"))
                count = await _send_followups(
                    http_client, api_url, token, storage.get_business_followup_list(),
                    ("video_negocio",), TEMPLATE_VIDEO_NEGOCIO,
                )
                logger.info("Videos de negocio: %s mensaje(s) enviados.", count)
        except Exception:
            logger.exception("Error inesperado en el loop de seguimiento comercial.")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
