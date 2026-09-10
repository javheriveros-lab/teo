import os
from fastapi import FastAPI, Request
from groq import Groq
import requests

app = FastAPI()

# Inicializamos el cerebro de Groq usando la llave segura que guardamos en Railway
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))


@app.get("/webhook")
async def verify_webhook(request: Request):
  # Esto sirve para que WhatsApp confirme que tu enlace es seguro
  hub_mode = request.query_params.get("hub.mode")
  hub_challenge = request.query_params.get("hub.challenge")
  hub_verify_token = request.query_params.get("hub.verify_token")

  verify_token = os.environ.get("WEBHOOK_VERIFY_TOKEN")
  if hub_mode == "subscribe" and hub_verify_token == verify_token:
    return int(hub_challenge)
  return "Error de token", 403


@app.post("/webhook")
async def receive_message(request: Request):
  body = await request.json()
  try:
    # 1. Extraemos los datos del mensaje que te mandó el cliente por WhatsApp
    entry = body["entry"][0]
    changes = entry["changes"][0]
    value = changes["value"]
    messages = value.get("messages")

    if messages:
      message = messages[0]
      phone_number_id = value["metadata"]["phone_number_id"]
      from_number = message["from"]  # El número de teléfono del cliente
      msg_body = message["text"]["body"]  # Lo que el cliente te escribió

      # 2. Le mandamos el texto del cliente a Groq para que piense una respuesta
      chat_completion = client.chat.completions.create(
          messages=[
              {
                  "role": "user",
                  "content": msg_body,
              }
          ],
          model="llama-3.3-70b-versatile",  # El modelo rápido y gratuito de Groq
      )
      respuesta_ia = chat_completion.choices[0].message.content

      # 3. Enviamos la respuesta generada de regreso al WhatsApp del cliente
      whatsapp_token = os.environ.get("WHATSAPP_TOKEN")
      url = f"https://graph.facebook.com/v17.0/{phone_number_id}/messages"
      headers = {
          "Authorization": f"Bearer {whatsapp_token}",
          "Content-Type": "application/json",
      }
      payload = {
          "messaging_product": "whatsapp",
          "to": from_number,
          "text": {"body": respuesta_ia},
      }
      requests.post(url, json=payload, headers=headers)

  except Exception as e:
    print(f"Hubo un error procesando el mensaje: {e}")

  return {"status": "ok"}