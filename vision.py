"""Análisis de técnica de ejercicio en video, vía Gemini (Google). Feedback general con
disclaimers fuertes — nunca certifica que una técnica es segura."""

import asyncio
import base64
import logging
import os

import httpx

logger = logging.getLogger("teo.vision")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-flash-latest"
GEMINI_BASE = "https://generativelanguage.googleapis.com"

ANALYSIS_PROMPT_TEMPLATE = """Eres un asistente de acondicionamiento físico que da observaciones GENERALES \
sobre un video de un cliente practicando un ejercicio. NO eres un entrenador certificado ni un profesional \
médico, y esto NO es una evaluación de seguridad.

{context}

Responde en español, en un mensaje breve para WhatsApp (máximo 6-7 líneas), siguiendo esta estructura:
1. Identifica qué ejercicio parece estar haciendo la persona.
2. Menciona 1-2 aspectos que se ven razonablemente bien.
3. Si notas algo mejorable (alineación, rango de movimiento, postura, velocidad), coméntalo con tono \
constructivo y alentador — nunca alarmista.
4. Cierra SIEMPRE con una frase que aclare que esto es una observación general de IA, no una evaluación \
profesional, y que recomiendas confirmar la técnica con un entrenador certificado antes de aumentar peso, \
repeticiones o intensidad — especialmente si sintió dolor o molestia.

Prohibido terminante: nunca digas que la técnica es "perfecta", "correcta al 100%", "segura" o cualquier \
afirmación que suene a garantía. Usa lenguaje como "se ve razonable", "en términos generales luce bien", \
"esto podría ajustarse".
"""


async def _start_upload(http_client: httpx.AsyncClient, size: int, mime_type: str) -> str:
    response = await http_client.post(
        f"{GEMINI_BASE}/upload/v1beta/files",
        params={"key": GEMINI_API_KEY},
        headers={
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(size),
            "X-Goog-Upload-Header-Content-Type": mime_type,
            "Content-Type": "application/json",
        },
        json={"file": {"display_name": "exercise_video"}},
    )
    response.raise_for_status()
    upload_url = response.headers.get("x-goog-upload-url")
    if not upload_url:
        raise RuntimeError("Gemini no devolvió una URL de subida.")
    return upload_url


async def _upload_bytes(http_client: httpx.AsyncClient, upload_url: str, data: bytes) -> dict:
    response = await http_client.post(
        upload_url,
        headers={
            "Content-Length": str(len(data)),
            "X-Goog-Upload-Offset": "0",
            "X-Goog-Upload-Command": "upload, finalize",
        },
        content=data,
    )
    response.raise_for_status()
    return response.json()["file"]


async def _wait_until_active(http_client: httpx.AsyncClient, file_name: str, timeout_seconds: int = 30) -> dict:
    elapsed = 0
    while elapsed < timeout_seconds:
        response = await http_client.get(f"{GEMINI_BASE}/v1beta/{file_name}", params={"key": GEMINI_API_KEY})
        response.raise_for_status()
        file_info = response.json()
        if file_info.get("state") == "ACTIVE":
            return file_info
        if file_info.get("state") == "FAILED":
            raise RuntimeError("Gemini falló al procesar el video.")
        await asyncio.sleep(2)
        elapsed += 2
    raise TimeoutError("Gemini tardó demasiado en procesar el video.")


async def analyze_exercise_video(http_client: httpx.AsyncClient, video_bytes: bytes, mime_type: str, context: str) -> str:
    """Sube el video a Gemini y devuelve una observación general de técnica, con disclaimers."""
    if not GEMINI_API_KEY:
        return (
            "Por ahora no puedo revisar videos de técnica (falta configurar el análisis de video). "
            "Te recomiendo confirmar tu técnica con un entrenador certificado."
        )

    upload_url = await _start_upload(http_client, len(video_bytes), mime_type)
    file_info = await _upload_bytes(http_client, upload_url, video_bytes)
    file_info = await _wait_until_active(http_client, file_info["name"])

    prompt = ANALYSIS_PROMPT_TEMPLATE.format(context=context or "No se especificó qué ejercicio debía practicar.")

    response = await http_client.post(
        f"{GEMINI_BASE}/v1beta/models/{GEMINI_MODEL}:generateContent",
        params={"key": GEMINI_API_KEY},
        json={
            "contents": [
                {
                    "parts": [
                        {"file_data": {"mime_type": file_info["mimeType"], "file_uri": file_info["uri"]}},
                        {"text": prompt},
                    ]
                }
            ]
        },
        timeout=60.0,
    )
    response.raise_for_status()
    data = response.json()
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()


PAYMENT_PROOF_PROMPT = """Estás viendo una captura de pantalla o foto de un comprobante de transferencia \
bancaria que un cliente mandó por WhatsApp. Tu única tarea es extraer, de lo que alcances a leer con \
claridad, estos datos si están visibles: monto transferido, banco emisor y/o receptor, número de referencia \
o folio, y fecha/hora de la operación.

Responde en español, en un mensaje breve (máximo 5 líneas), en este formato:
Monto: [lo que veas, o "no legible"]
Banco: [lo que veas, o "no legible"]
Referencia/folio: [lo que veas, o "no legible"]
Fecha: [lo que veas, o "no legible"]

IMPORTANTE: esto es solo un apoyo de lectura para que un humano revise el pago en la cuenta bancaria real. \
NUNCA digas que el pago "es válido", "está confirmado" o "se verificó" — no tienes forma de confirmar que el \
dinero realmente llegó a la cuenta, una imagen puede estar editada o ser falsa. Limítate a transcribir lo que \
ves."""


async def analyze_payment_proof(http_client: httpx.AsyncClient, image_bytes: bytes, mime_type: str) -> str:
    """Lee un comprobante de pago (imagen) con Gemini y devuelve un resumen de apoyo para que un
    humano lo revise contra la cuenta bancaria real. Nunca es un veredicto de validez."""
    if not GEMINI_API_KEY:
        return "No se pudo leer la imagen automáticamente (falta configurar el análisis de imagen)."

    try:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        response = await http_client.post(
            f"{GEMINI_BASE}/v1beta/models/{GEMINI_MODEL}:generateContent",
            params={"key": GEMINI_API_KEY},
            json={
                "contents": [
                    {
                        "parts": [
                            {"inline_data": {"mime_type": mime_type, "data": encoded}},
                            {"text": PAYMENT_PROOF_PROMPT},
                        ]
                    }
                ]
            },
            timeout=60.0,
        )
        response.raise_for_status()
        data = response.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception:
        logger.exception("Error al analizar el comprobante de pago con Gemini.")
        return "No se pudo leer la imagen automáticamente; un humano deberá revisarla directamente."
