import os
import logging
from typing import Optional
from fastapi import FastAPI, Query, HTTPException, Request, status, BackgroundTasks
from fastapi.responses import PlainTextResponse, JSONResponse
from dotenv import load_dotenv
import httpx

# Cargar variables de entorno desde .env
load_dotenv()

# Configuración de Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("TEO-WhatsApp-Bot")

# Variables de Configuración de WhatsApp / Meta
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID", "")
WHATSAPP_BUSINESS_ACCOUNT_ID = os.getenv("WHATSAPP_BUSINESS_ACCOUNT_ID", "")
WEBHOOK_VERIFY_TOKEN = os.getenv("WEBHOOK_VERIFY_TOKEN", "")
META_GRAPH_API_VERSION = os.getenv("META_GRAPH_API_VERSION", "v21.0")

# Inicialización de la aplicación FastAPI
app = FastAPI(
    title="TEO - Asistente de WhatsApp de I'AM",
    description="Backend con FastAPI para el chatbot TEO de I'AM utilizando Meta WhatsApp Cloud API.",
    version="1.0.0"
)


@app.get("/", tags=["General"])
async def root():
    """Ruta raíz para verificar que el servicio de TEO está activo."""
    return {
        "status": "online",
        "service": "TEO - Asistente de WhatsApp de I'AM",
        "version": "1.0.0",
        "docs_url": "/docs"
    }


@app.get("/health", tags=["General"])
async def health_check():
    """Endpoint de salud del servidor."""
    return {
        "status": "healthy",
        "whatsapp_configured": bool(WHATSAPP_TOKEN and PHONE_NUMBER_ID and WEBHOOK_VERIFY_TOKEN)
    }


@app.get("/webhook", tags=["Webhook Meta"])
async def verify_webhook(
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge")
):
    """
    Endpoint para la verificación del Webhook de WhatsApp por parte de Meta.

    Meta envía una solicitud GET con los siguientes parámetros de consulta:
    - hub.mode: Debe ser 'subscribe'
    - hub.verify_token: Token secreto configurado en el panel de desarrolladores de Meta
    - hub.challenge: Cadena aleatoria que Meta espera recibir de vuelta como respuesta en texto plano
    """
    logger.info("Recibida solicitud de verificación de webhook de Meta.")

    if not hub_mode or not hub_verify_token:
        logger.warning("Faltan parámetros en la solicitud de verificación.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Faltan parámetros de verificación obligatorios ('hub.mode', 'hub.verify_token')."
        )

    # Validar que el modo sea 'subscribe' y el token coincida con el configurado
    if hub_mode == "subscribe" and hub_verify_token == WEBHOOK_VERIFY_TOKEN:
        logger.info("¡Verificación de Webhook exitosa!")
        # Meta requiere retornar el challenge como texto plano y con status HTTP 200
        return PlainTextResponse(content=hub_challenge or "", status_code=status.HTTP_200_OK)

    logger.warning("Fallo en la verificación del Webhook: el token de verificación no coincide.")
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Token de verificación inválido o modo incorrecto."
    )


async def process_whatsapp_message(payload: dict):
    """
    Función en segundo plano para procesar los mensajes entrantes de WhatsApp
    sin bloquear la respuesta inmediata 200 OK a los servidores de Meta.
    """
    try:
        entry = payload.get("entry", [])
        if not entry:
            return

        for e in entry:
            changes = e.get("changes", [])
            for change in changes:
                value = change.get("value", {})

                # Verificar si es un evento de mensajes
                messages = value.get("messages", [])
                if not messages:
                    # Puede ser una actualización de estado (sent, delivered, read)
                    statuses = value.get("statuses", [])
                    if statuses:
                        for s in statuses:
                            logger.info(f"Estado de mensaje actualizado: {s.get('id')} -> {s.get('status')}")
                    continue

                contacts = value.get("contacts", [])
                contact_name = contacts[0].get("profile", {}).get("name", "Usuario") if contacts else "Usuario"

                for msg in messages:
                    msg_id = msg.get("id")
                    msg_from = msg.get("from")  # Número del usuario
                    msg_type = msg.get("type")

                    logger.info(f"Mensaje recibido de {contact_name} ({msg_from}) [ID: {msg_id}, Tipo: {msg_type}]")

                    if msg_type == "text":
                        text_body = msg.get("text", {}).get("body", "")
                        logger.info(f"Texto del mensaje: '{text_body}'")

                        # Aquí se conectará la lógica de negocio y el LLM/RAG para TEO
                        # Ejemplo de respuesta básica de bienvenida / eco de TEO:
                        # await send_whatsapp_message(to=msg_from, message=f"Hola {contact_name}, soy TEO de I'AM. Recibí tu mensaje: '{text_body}'")

    except Exception as exc:
        logger.error(f"Error procesando mensaje entrante: {exc}", exc_info=True)


@app.post("/webhook", tags=["Webhook Meta"])
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks
):
    """
    Endpoint para recibir notificaciones y mensajes en tiempo real de WhatsApp.

    Meta exige una respuesta HTTP 200 rápida para confirmar la recepción
    y evitar reintentos automáticos continuos.
    """
    try:
        body = await request.json()
    except Exception as exc:
        logger.error(f"Error parseando JSON del webhook: {exc}")
        return JSONResponse(content={"status": "invalid_json"}, status_code=status.HTTP_400_BAD_REQUEST)

    # Validar que provenga de una cuenta de WhatsApp Business
    if body.get("object") == "whatsapp_business_account":
        # Despachar procesamiento a segundo plano para responder de inmediato a Meta
        background_tasks.add_task(process_whatsapp_message, body)
        return JSONResponse(content={"status": "EVENT_RECEIVED"}, status_code=status.HTTP_200_OK)

    return JSONResponse(
        content={"status": "not_found"},
        status_code=status.HTTP_404_NOT_FOUND
    )


async def send_whatsapp_message(to: str, message: str) -> Optional[dict]:
    """
    Función utilitaria para enviar mensajes de texto a través de WhatsApp Cloud API.
    """
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        logger.error("No se han configurado WHATSAPP_TOKEN o PHONE_NUMBER_ID en las variables de entorno.")
        return None

    url = f"https://graph.facebook.com/{META_GRAPH_API_VERSION}/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": message
        }
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            logger.info(f"Mensaje enviado con éxito a {to}: {response.json()}")
            return response.json()
        except httpx.HTTPStatusError as exc:
            logger.error(f"Error enviando mensaje a WhatsApp API: {exc.response.status_code} - {exc.response.text}")
        except Exception as exc:
            logger.error(f"Error inesperado al conectar con WhatsApp API: {exc}")

    return None


if __name__ == "__main__":
    import uvicorn
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 8000))
    debug = os.getenv("DEBUG", "True").lower() in ("true", "1", "yes")

    logger.info(f"Iniciando servidor TEO en http://{host}:{port}")
    uvicorn.run("main.py:app", host=host, port=port, reload=debug)
