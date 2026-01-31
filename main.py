from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from datetime import datetime, timedelta
import os
import httpx
import json
import asyncio

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

waitlist = []
reminders = {}

class WaitlistSignup(BaseModel):
    email: str

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
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"https://graph.facebook.com/v18.0/{media_id}",
                headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
            )
            url = r.json()["url"]
            r = await client.get(url, headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"})
            audio = r.content
            r = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                files={"file": ("audio.ogg", audio, "audio/ogg")},
                data={"model": "whisper-1"},
                timeout=60.0
            )
            text = r.json().get("text", "")
            print(f"Transcribed: {text}")
            await process_text(phone, text)
    except Exception as e:
        print(f"Voice error: {e}")
        await send_message(phone, "Sorry, couldn't process that voice message. Try again?")

async def process_text(phone: str, text: str):
    text_lower = text.lower().strip()
    if text_lower in ["help", "hi", "hello", "hey"]:
        await send_message(phone, "👋 *I'm Kazi!*\n\nSend me a voice or text to set reminders.\n\n*Try:*\n• \"Remind me to call mom at 5pm\"\n• \"Meeting tomorrow at 9am\"\n\n*Commands:*\n• \"list\" - see reminders\n\nI understand 99+ languages! 🌍")
        return
    if text_lower in ["list", "reminders", "my reminders"]:
        user_r = [r for r in reminders.values() if r["phone"] == phone and not r["sent"]]
        if not user_r:
            await send_message(phone, "You have no upcoming reminders.")
        else:
            lines = ["📋 *Your reminders:*\n"]
            for r in sorted(user_r, key=lambda x: x["remind_at"]):
                lines.append(f"• {r['task']} — {r['remind_at']}")
            await send_message(phone, "\n".join(lines))
        return
    try:
        now = datetime.utcnow()
        prompt = f'Extract reminder from: "{text}". Current time: {now.strftime("%Y-%m-%d %H:%M")} UTC. Reply JSON only: {{"task": "what", "time": "ISO datetime or null"}}'
        async with httpx.AsyncClient() as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": "claude-sonnet-4-20250514", "max_tokens": 150, "messages": [{"role": "user", "content": prompt}]},
                timeout=30.0
            )
            content = r.json()["content"][0]["text"]
            content = content.strip().replace("```json", "").replace("```", "").strip()
            intent = json.loads(content)
            task = intent.get("task")
            time_str = intent.get("time")
            if task and time_str:
                rid = f"{phone}_{int(now.timestamp())}"
                reminders[rid] = {"id": rid, "phone": phone, "task": task, "remind_at": time_str, "sent": False}
                await send_message(phone, f"✅ I'll remind you to *{task}* at {time_str}")
            elif task:
                await send_message(phone, f"Got it: *{task}*\n\nWhen should I remind you?")
            else:
                await send_message(phone, "I didn't catch that. Try: \"Remind me to call mom at 5pm\"")
    except Exception as e:
        print(f"Intent error: {e}")
        await send_message(phone, "I didn't catch that. Try: \"Remind me to call mom at 5pm\"")

async def send_message(phone: str, text: str):
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"https://graph.facebook.com/v18.0/{WHATSAPP_PHONE_ID}/messages",
            headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"},
            json={"messaging_product": "whatsapp", "to": phone, "type": "text", "text": {"body": text}}
        )
        print(f"Send: {r.status_code}")

async def reminder_loop():
    while True:
        now = datetime.utcnow()
        for rid, r in list(reminders.items()):
            if r["sent"]:
                continue
            try:
                remind_time = datetime.fromisoformat(r["remind_at"].replace("Z", "+00:00").replace("+00:00", ""))
                if now >= remind_time:
                    await send_message(r["phone"], f"⏰ *Reminder:* {r['task']}")
                    r["sent"] = True
            except:
                pass
        await asyncio.sleep(30)

@app.on_event("startup")
async def startup():
    asyncio.create_task(reminder_loop())

@app.post("/api/waitlist")
async def join_waitlist(signup: WaitlistSignup):
    if signup.email not in waitlist:
        waitlist.append(signup.email)
    return {"status": "ok", "count": len(waitlist)}

@app.get("/api/waitlist/count")
async def get_waitlist_count():
    return {"count": len(waitlist) + 500}
