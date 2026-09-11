import os
from fastapi import FastAPI, Request
import requests
from groq import Groq

app = FastAPI()

# Inicializa Groq con tu variable de entorno
groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")

@app.post("/webhook")
async def receive_message(request: Request):
    body = await request.json()
    
    try:
        entry = body["entry"][0]
        changes = entry["changes"][0]
        value = changes["value"]
        
        if "messages" in value:
            message = value["messages"][0]
            sender_phone = message["from"]
            user_text = message["text"]["body"]
            print(f"Mensaje recibido de {sender_phone}: {user_text}")
            
            # 1. Preguntar a Groq (TEO)
            chat_completion = groq_client.chat.completions.create(
                messages=[
                    {
                        "role": "system",
                        "content": "Eres TEO, un asistente virtual experto en nutrición y ventas para la marca I'AM. Responde de forma amable, clara y directa.",
                    },
                    {
                        "role": "user",
                        "content": user_text,
                    }
                ],
                model="llama-3.1-8b-instant",
            )
            bot_reply = chat_completion.choices[0].message.content
            print(f"Respuesta generada por Groq: {bot_reply}")
            
            # 2. Enviar la respuesta de regreso a WhatsApp
            url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
            headers = {
                "Authorization": f"Bearer {WHATSAPP_TOKEN}",
                "Content-Type": "application/json",
            }
            payload = {
                "messaging_product": "whatsapp",
                "to": sender_phone,
                "type": "text",
                "text": {"body": bot_reply},
            }
            
            response = requests.post(url, headers=headers, json=payload)
            print(f"Respuesta de Meta WhatsApp API: {response.status_code} - {response.text}")
            
    except Exception as e:
        print(f"Error procesando mensaje: {e}")
        
    return {"status": "success"}