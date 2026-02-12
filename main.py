"""
Kazi - Voice-first AI Agent via WhatsApp
"Your voice. Your agent. Any language."
"""

import os
import httpx
from fastapi import FastAPI, Request, Form
from fastapi.responses import Response, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import anthropic
from openai import OpenAI

# Config
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Clients
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
openai_client = OpenAI(api_key=OPENAI_API_KEY)

app = FastAPI(title="Kazi", description="Voice-first AI Agent")

# System prompt for Kazi
KAZI_SYSTEM = """You are Kazi, a helpful AI assistant that works via WhatsApp voice and text.

Your name "Kazi" means "work" in Swahili. You help people get things done through simple voice commands.

Key traits:
- Concise: Keep responses short (WhatsApp-friendly, under 300 chars when possible)
- Helpful: Actually do things, don't just explain
- Multilingual: Respond in the same language the user speaks
- Action-oriented: Focus on execution, not theory

You can help with:
- Answering questions
- Web searches
- Reminders and tasks
- Calculations
- Translations
- General assistance

Keep it simple. Keep it useful. Get things done."""


async def transcribe_audio(media_url: str) -> str:
    """Download and transcribe audio using Whisper."""
    
    async with httpx.AsyncClient() as client:
        response = await client.get(
            media_url,
            auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        )
        audio_data = response.content
    
    temp_path = "/tmp/voice_message.ogg"
    with open(temp_path, "wb") as f:
        f.write(audio_data)
    
    with open(temp_path, "rb") as audio_file:
        transcript = openai_client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file
        )
    
    return transcript.text


async def get_kazi_response(user_message: str, user_phone: str) -> str:
    """Get response from Claude."""
    
    response = claude.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=500,
        system=KAZI_SYSTEM,
        messages=[
            {"role": "user", "content": user_message}
        ]
    )
    
    return response.content[0].text


async def send_whatsapp_message(to: str, body: str):
    """Send WhatsApp message via Twilio."""
    
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            url,
            auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            data={
                "From": "whatsapp:+15734125273",
                "To": to,
                "Body": body
            }
        )
    
    return response.json()


@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the landing page."""
    return FileResponse("index.html")


@app.get("/api")
async def api_status():
    return {
        "name": "Kazi",
        "tagline": "Your voice. Your agent. Any language.",
        "status": "running",
        "endpoints": {
            "webhook": "/webhook",
            "health": "/health"
        }
    }


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.post("/webhook")
async def whatsapp_webhook(
    request: Request,
    From: str = Form(...),
    Body: str = Form(default=""),
    NumMedia: str = Form(default="0"),
    MediaUrl0: str = Form(default=None),
    MediaContentType0: str = Form(default=None)
):
    """Handle incoming WhatsApp messages (text and voice)."""
    
    user_phone = From
    
    print(f"📱 Message from {user_phone}")
    print(f"   Text: {Body}")
    print(f"   Media: {NumMedia}")
    
    try:
        if int(NumMedia) > 0 and MediaContentType0 and "audio" in MediaContentType0:
            print(f"🎤 Voice message detected: {MediaContentType0}")
            user_message = await transcribe_audio(MediaUrl0)
            print(f"📝 Transcribed: {user_message}")
        else:
            user_message = Body
        
        if not user_message.strip():
            return Response(content="", media_type="text/xml")
        
        kazi_response = await get_kazi_response(user_message, user_phone)
        print(f"🤖 Kazi: {kazi_response}")
        
        await send_whatsapp_message(user_phone, kazi_response)
        
        return Response(content="<Response></Response>", media_type="text/xml")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        error_msg = "Sorry, something went wrong. Please try again."
        await send_whatsapp_message(user_phone, error_msg)
        return Response(content="<Response></Response>", media_type="text/xml")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
