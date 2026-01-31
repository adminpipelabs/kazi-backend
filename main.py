from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from datetime import datetime, timedelta
import os
import httpx
import json
import asyncio
import pytz

app = FastAPI(title="Kazi API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
WHATSAPP_PHONE_ID = os.getenv("WHATSAPP_PHONE_ID")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "kazi_verify")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

reminders = {}
waitlist = []

class WaitlistSignup(BaseModel):
    email: str

class Reminder(BaseModel):
    id: str
    phone: str
    task: str
    remind_at: datetime
    sent: bool = False

@app.get("/", response_class=HTMLResponse)
async def root():
    with open("index.html", "r") as f:
        return f.read()

@app.get("/webhook")
async def verify_webhook(request: Request):
    params = request.query_params
    if params.get("hub.verify_token") == WHATSAPP_VERIFY_TOKEN:
        return int(params.get("hub.challenge", 0))
    raise HTTPException(403, "Invalid")

@app.post("/webhook")
async def handle_webhook(request: Request, background_tasks: BackgroundTasks):
    body = await request.json()
    try:
        messages = body["entry"][0]["changes"][0]["value"].get("messages", [])
        if not messages:
            return {"status": "no_message"}
        msg = messages[0]
        phone = msg["from"]
        msg_type = msg["type"]
        if msg_type == "audio":
            media_id = msg["audio"]["id"]
            background_tasks.add_task(process_voice, phone, media_id)
        elif msg_type == "text":
            text = msg["text"]["body"]
            background_tasks.add_task(process_text, phone, text)
        return {"status": "ok"}
    except Exception as e:
        print(f"Webhook error: {e}")
        return {"status": "error"}

async def process_voice(phone: str, media_id: str):
    try:
        audio = await download_media(media_id)
        text = await transcribe(audio)
        print(f"Transcribed: {text}")
        await process_text(phone, text)
    except Exception as e:
        print(f"Voice error: {e}")
        await send_message(phone, "Sorry, I couldn't process that. Try again?")

async def download_media(media_id: str) -> bytes:
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"https://graph.facebook.com/v18.0/{media_id}",
            headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
        )
        url = r.json()["url"]
        r = await client.get(url, headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"})
        return r.content

async def transcribe(audio: bytes) -> str:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            files={"file": ("audio.ogg", audio, "audio/ogg")},
            data={"model": "whisper-1"},
            timeout=60.0
        )
        return r.json().get("text", "")

async def extract_intent(text: str) -> dict:
    now = datetime.now(pytz.timezone("UTC"))
    prompt = f"""Extract the reminder. Current time: {now.strftime('%Y-%m-%d %H:%M')} UTC

Message: "{text}"

Reply JSON only:
{{"task": "what to remind", "time": "ISO datetime or null", "confidence": 0.0-1.0}}

Examples:
"remind me to call mom at 5pm" -> {{"task": "call mom", "time": "{now.replace(hour=17, minute=0).isoformat()}", "confidence": 0.95}}
"buy milk tomorrow morning" -> {{"task": "buy milk", "time": "{(now + timedelta(days=1)).replace(hour=9, minute=0).isoformat()}", "confidence": 0.9}}"""

    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 200,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=30.0
        )
        content = r.json()["content"][0]["text"]
        content = content.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(content)

async def process_text(phone: str, text: str):
    text_lower = text.lower().strip()
    if text_lower in ["help", "hi", "hello", "hey"]:
        await send_help(phone)
        return
    if text_lower in ["list", "reminders", "my reminders"]:
        await send_reminder_list(phone)
        return
    try:
        intent = await extract_int
