"""
Kazi - Voice-first AI Agent via WhatsApp
"""

import os
import httpx
from fastapi import FastAPI, Form
from fastapi.responses import Response, HTMLResponse, FileResponse
import anthropic
from openai import OpenAI

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
openai_client = OpenAI(api_key=OPENAI_API_KEY)

app = FastAPI(title="Kazi")

KAZI_SYSTEM = "You are Kazi, a helpful AI assistant via WhatsApp. Keep responses short (under 300 chars). Be friendly and helpful. Respond in the user's language."

async def transcribe_audio(media_url: str) -> str:
    async with httpx.AsyncClient() as client:
        response = await client.get(media_url, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN))
    with open("/tmp/voice.ogg", "wb") as f:
        f.write(response.content)
    with open("/tmp/voice.ogg", "rb") as f:
        transcript = openai_client.audio.transcriptions.create(model="whisper-1", file=f)
    return transcript.text

async def send_whatsapp_message(to: str, body: str):
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
    async with httpx.AsyncClient() as client:
        await client.post(url, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            data={"From": "whatsapp:+15734125273", "To": to, "Body": body})

async def get_kazi_response(user_message: str) -> str:
    response = claude.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=500,
        system=KAZI_SYSTEM,
        messages=[{"role": "user", "content": user_message}]
    )
    return response.content[0].text

@app.get("/", response_class=HTMLResponse)
async def root():
    return FileResponse("index.html")

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.post("/webhook")
async def webhook(From: str = Form(...), Body: str = Form(default=""), NumMedia: str = Form(default="0"), MediaUrl0: str = Form(default=None), MediaContentType0: str = Form(default=None)):
    try:
        if int(NumMedia) > 0 and MediaContentType0 and "audio" in MediaContentType0:
            user_message = await transcribe_audio(MediaUrl0)
        else:
            user_message = Body
        if not user_message.strip():
            return Response(content="", media_type="text/xml")
        response = await get_kazi_response(user_message)
        await send_whatsapp_message(From, response)
    except Exception as e:
        print(f"Error: {e}")
        await send_whatsapp_message(From, "Sorry, something went wrong.")
    return Response(content="<Response></Response>", media_type="text/xml")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
