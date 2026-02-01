from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from datetime import datetime
import os
import httpx
import json
import asyncio
import psycopg
from psycopg.rows import dict_row

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
DATABASE_URL = os.getenv("DATABASE_URL")

class WaitlistSignup(BaseModel):
    email: str

def get_db():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)

@app.on_event("startup")
async def startup():
    with get_db() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS reminders (
                id SERIAL PRIMARY KEY,
                phone TEXT NOT NULL,
                task TEXT NOT NULL,
                remind_at TIMESTAMP NOT NULL,
                sent BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT NOW()
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS waitlist (
                id SERIAL PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )
        ''')
        conn.commit()
    asyncio.create_task(reminder_loop())

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
        with get_db() as conn:
            rows = conn.execute(
                "SELECT task, remind_at FROM reminders WHERE phone = %s AND sent = FALSE ORDER BY remind_at",
                (phone,)
            ).fetchall()
        if not rows:
            await send_message(phone, "You have no upcoming reminders.")
        else:
            lines = ["📋 *Your reminders:*\n"]
            for r in rows:
                lines.append(f"• {r['task']} — {r['remind_at'].strftime('%b %d, %I:%M %p')}")
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
                remind_at = datetime.fromisoformat(time_str.replace("Z", "+00:00").replace("+00:00", ""))
                with get_db() as conn:
                    conn.execute(
                        "INSERT INTO reminders (phone, task, remind_at) VALUES (%s, %s, %s)",
                        (phone, task, remind_at)
                    )
                    conn.commit()
                await send_message(phone, f"✅ I'll remind you to *{task}* on {remind_at.strftime('%b %d at %I:%M %p')}")
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
        try:
            now = datetime.utcnow()
            with get_db() as conn:
                rows = conn.execute(
                    "SELECT id, phone, task FROM reminders WHERE sent = FALSE AND remind_at <= %s",
                    (now,)
                ).fetchall()
                for r in rows:
                    await send_message(r["phone"], f"⏰ *Reminder:* {r['task']}")
                    conn.execute("UPDATE reminders SET sent = TRUE WHERE id = %s", (r["id"],))
                    conn.commit()
        except Exception as e:
            print(f"Reminder loop error: {e}")
        await asyncio.sleep(30)

@app.post("/api/waitlist")
async def join_waitlist(signup: WaitlistSignup):
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO waitlist (email) VALUES (%s) ON CONFLICT DO NOTHING",
                (signup.email,)
            )
            conn.commit()
            count = conn.execute("SELECT COUNT(*) as c FROM waitlist").fetchone()["c"]
        return {"status": "ok", "count": count}
    except Exception as e:
        print(f"Waitlist error: {e}")
        return {"status": "ok", "count": 0}

@app.get("/api/waitlist/count")
async def get_waitlist_count():
    with get_db() as conn:
        count = conn.execute("SELECT COUNT(*) as c FROM waitlist").fetchone()["c"]
    return {"count": count + 500}
