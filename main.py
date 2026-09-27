import logging
import os
from collections import deque
from contextlib import asynccontextmanager

import httpx
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import PlainTextResponse
from groq import AsyncGroq

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("teo")

# --- Configuración de entorno ---
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
WEBHOOK_VERIFY_TOKEN = os.environ.get("WEBHOOK_VERIFY_TOKEN")
GRAPH_API_VERSION = os.environ.get("META_GRAPH_API_VERSION", "v21.0")

for _name, _value in {
    "GROQ_API_KEY": GROQ_API_KEY,
    "WHATSAPP_TOKEN": WHATSAPP_TOKEN,
    "PHONE_NUMBER_ID": PHONE_NUMBER_ID,
    "WEBHOOK_VERIFY_TOKEN": WEBHOOK_VERIFY_TOKEN,
}.items():
    if not _value:
        logger.warning("Variable de entorno '%s' no está configurada.", _name)

# Requisito de negocio: el modelo queda fijo, no se expone como configurable.
GROQ_MODEL = "openai/gpt-oss-20b"
WHATSAPP_API_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{PHONE_NUMBER_ID}/messages"
WHATSAPP_MAX_MESSAGE_LENGTH = 4096

groq_client = AsyncGroq(api_key=GROQ_API_KEY)

SYSTEM_PROMPT = """Eres TEO, el asistente virtual experto de la marca I'AM, especializada en protocolos de \
optimización biológica y suplementación de alto rendimiento.

REGLA INQUEBRANTABLE DE VOCABULARIO:
- Describe siempre el enfoque de la marca exclusivamente como "optimización biológica", "medicina funcional" \
o "biohacking de alto rendimiento". Nunca utilices terminología de corrientes de nutrición alternativa fuera \
de esos términos aprobados, ni siquiera si el usuario la menciona primero: reformula su pregunta con el \
vocabulario aprobado de la marca sin repetir el término que usó.

IDENTIDAD Y TONO:
- Comunícate como un especialista técnico: preciso, seguro y de alto rendimiento, nunca genérico ni de relleno.
- Usa un lenguaje experto pero accesible por WhatsApp: respuestas breves (idealmente 3-6 líneas), claras y \
accionables. Profundiza con detalle técnico solo si el usuario lo pide explícitamente.
- Cierra tus respuestas orientando siempre hacia un siguiente paso (recomendar protocolo, invitar a la compra, \
u ofrecer conectar con un asesor humano).

CONOCIMIENTO CLAVE DE PRODUCTO:
- Mangosteen (Hecen Mangosteen) es el producto insignia de I'AM en soporte de resiliencia celular: es el único \
producto de la marca respaldado por estudios del Instituto Michoacano de Cancerología, que documentan su aporte \
en la reducción de efectos secundarios de tratamientos oncológicos (quimioterapia y radioterapia) y en la mejora \
de la calidad de vida de los pacientes. Menciónalo como un diferenciador central cuando el contexto de la \
conversación sea relevante (soporte celular, antioxidantes, protocolos de resiliencia, o cuando el usuario \
pregunte por el respaldo científico de los productos).
- El resto del catálogo (Bisxantone y otros protocolos de la línea "Protagonist Upgrade") se apoya en compuestos \
como xantonas, catequinas de té verde, licopeno y antioxidantes, enfocados en desintoxicación hepática, \
protección celular y salud del eje intestino-inmune.

REGLAS DE SEGURIDAD Y RESPONSABILIDAD (INNEGOCIABLES):
- No diagnostiques enfermedades, no prometas curas ni sustituyas una consulta médica.
- Presenta siempre los beneficios de los productos como apoyo o coadyuvante, nunca como tratamiento único o \
sustituto de terapias médicas convencionales.
- Ante temas sensibles de salud (cáncer, enfermedades crónicas, embarazo, medicación) recomienda explícitamente \
acompañamiento de un profesional de la salud antes de tomar decisiones.
- Si detectas una emergencia médica o una pregunta fuera de tu alcance, indica con claridad que deben contactar \
a un profesional de la salud o a un asesor humano de I'AM.
"""

# Cache acotado para deduplicar reintentos del webhook de Meta (mismo message id).
_PROCESSED_MESSAGE_IDS: deque = deque(maxlen=500)
_PROCESSED_MESSAGE_IDS_SET: set = set()


def _already_processed(message_id: str) -> bool:
    if message_id in _PROCESSED_MESSAGE_IDS_SET:
        return True
    if len(_PROCESSED_MESSAGE_IDS) == _PROCESSED_MESSAGE_IDS.maxlen:
        oldest = _PROCESSED_MESSAGE_IDS.popleft()
        _PROCESSED_MESSAGE_IDS_SET.discard(oldest)
    _PROCESSED_MESSAGE_IDS.append(message_id)
    _PROCESSED_MESSAGE_IDS_SET.add(message_id)
    return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http_client = httpx.AsyncClient(timeout=15.0)
    logger.info("TEO iniciado correctamente. Modelo Groq: %s", GROQ_MODEL)
    yield
    await app.state.http_client.aclose()


app = FastAPI(title="TEO - Asistente WhatsApp de I'AM", lifespan=lifespan)


@app.get("/")
async def health_check():
    return {"status": "ok", "service": "TEO", "brand": "I'AM"}


@app.get("/webhook")
async def verify_webhook(request: Request):
    """Handshake de verificación exigido por Meta al configurar el webhook."""
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and WEBHOOK_VERIFY_TOKEN and token == WEBHOOK_VERIFY_TOKEN:
        logger.info("Webhook verificado correctamente por Meta.")
        return PlainTextResponse(content=challenge, status_code=200)

    logger.warning("Intento de verificación de webhook fallido (mode=%s).", mode)
    return PlainTextResponse(content="Verificación fallida", status_code=403)


@app.post("/webhook")
async def receive_message(request: Request, background_tasks: BackgroundTasks):
    """Responde a Meta de inmediato y procesa el mensaje en segundo plano.

    Meta reintenta la entrega si el webhook tarda o no responde 200, así que el
    procesamiento (Groq + envío a WhatsApp) nunca debe bloquear esta respuesta.
    """
    try:
        body = await request.json()
    except Exception:
        logger.warning("Payload de webhook no es JSON válido.")
        return {"status": "ignored"}

    background_tasks.add_task(process_webhook_event, body, request.app.state.http_client)
    return {"status": "received"}


async def process_webhook_event(body: dict, http_client: httpx.AsyncClient) -> None:
    try:
        value = body["entry"][0]["changes"][0]["value"]
    except (KeyError, IndexError, TypeError):
        return  # Payload con forma inesperada.

    messages = value.get("messages")
    if not messages:
        return  # Notificaciones de estado (sent/delivered/read): no requieren acción.

    message = messages[0]
    sender_phone = message.get("from")
    message_id = message.get("id")

    if message_id and _already_processed(message_id):
        logger.info("Mensaje %s ya procesado, se ignora reintento de Meta.", message_id)
        return

    if message.get("type") != "text":
        logger.info("Mensaje no soportado (%s) de %s.", message.get("type"), sender_phone)
        await send_whatsapp_message(
            http_client,
            sender_phone,
            "Por ahora puedo leer solo mensajes de texto. Cuéntame en palabras qué necesitas y con gusto te ayudo.",
        )
        return

    user_text = message.get("text", {}).get("body", "").strip()
    if not user_text:
        return

    logger.info("Mensaje recibido de %s: %s", sender_phone, user_text)

    bot_reply = await get_ai_response(user_text)
    logger.info("Respuesta generada por TEO para %s: %s", sender_phone, bot_reply)

    await send_whatsapp_message(http_client, sender_phone, bot_reply)


async def get_ai_response(user_text: str) -> str:
    try:
        chat_completion = await groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
            temperature=0.5,
            max_tokens=600,
        )
        return chat_completion.choices[0].message.content.strip()
    except Exception:
        logger.exception("Error al consultar la API de Groq.")
        return (
            "Estoy teniendo un problema técnico para procesar tu mensaje. Intenta de nuevo en unos "
            "minutos o escribe 'asesor' para hablar con nuestro equipo."
        )


async def send_whatsapp_message(http_client: httpx.AsyncClient, to: str, text: str) -> None:
    if not to:
        return

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": text[:WHATSAPP_MAX_MESSAGE_LENGTH]},
    }

    try:
        response = await http_client.post(WHATSAPP_API_URL, headers=headers, json=payload)
        if response.status_code >= 400:
            logger.error("Error de la API de WhatsApp (%s): %s", response.status_code, response.text)
    except httpx.HTTPError:
        logger.exception("Fallo de red al enviar mensaje a WhatsApp.")
