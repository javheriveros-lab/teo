import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

import storage
from exercises import get_exercises_for_focus
from plans import DAILY_TIPS, MICRO_METAS, REST_LABEL

logger = logging.getLogger("teo.scheduler")

TIMEZONE = ZoneInfo("America/Mexico_City")
TEMPLATE_LANGUAGE = "es_MX"

TEMPLATE_CHECKIN_MATUTINO = "teo_checkin_matutino"
TEMPLATE_RECORDATORIO_DOSIS = "teo_recordatorio_dosis"
TEMPLATE_EDUCACION_RECETA = "teo_educacion_receta"
TEMPLATE_CHECKIN_NOCTURNO = "teo_checkin_nocturno"
TEMPLATE_RESUMEN_SEMANAL = "teo_resumen_semanal"
TEMPLATE_EJERCICIO = "teo_plan_ejercicio_v1"

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


def _build_ejercicio_payload(phone: str, name: str, image_url: str, cue: str) -> dict:
    return {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "template",
        "template": {
            "name": TEMPLATE_EJERCICIO,
            "language": {"code": TEMPLATE_LANGUAGE},
            "components": [
                {"type": "header", "parameters": [{"type": "image", "image": {"link": image_url}}]},
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": name},
                        {"type": "text", "text": cue},
                    ],
                },
            ],
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
    sent = await _send(http_client, api_url, token, payload, phone, "checkin_matutino")

    if sent and not is_rest:
        exercises = get_exercises_for_focus(focus)
        for ex_name, image_url, cue in exercises:
            ex_payload = _build_ejercicio_payload(phone, ex_name, image_url, cue)
            await _send(http_client, api_url, token, ex_payload, phone, "ejercicio_foto")

    return sent


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
