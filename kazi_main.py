import os
import json
import httpx
import asyncio
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, Form
from fastapi.responses import Response, HTMLResponse, FileResponse
import anthropic
from openai import OpenAI
import asyncpg

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")

claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
openai_client = OpenAI(api_key=OPENAI_API_KEY)
db_pool = None

KAZI_SYSTEM = """You are Kazi, a helpful AI assistant via WhatsApp. Keep responses short.

YOU CAN SET REMINDERS! When user asks for a reminder, confirm it AND add this JSON at the end:

REMINDER_JSON:{"task":"call Emma","hour":15,"minute":0}

Convert time to 24h format. Current time: {current_time}"""


async def init_db():
    global db_pool
    if DATABASE_URL:
        db_pool = await asyncpg.create_pool(DATABASE_URL)
        async with db_pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS reminders (
                    id SERIAL PRIMARY KEY,
                    user_phone VARCHAR(50) NOT NULL,
                    task TEXT NOT NULL,
                    remind_at TIMESTAMP NOT NULL,
                    sent BOOLEAN DEFAULT FALSE
                )
            """)
        print("DB ready")


async def close_db():
    if db_pool:
        await db_pool.close()


async def send_whatsapp(to, body):
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
    async with httpx.AsyncClient() as client:
        await client.post(url, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            data={"From": "whatsapp:+15734125273", "To": to, "Body": body})


async def check_reminders():
    print("Reminder checker started")
    while True:
        try:
            if db_pool:
                async with db_pool.acquire() as conn:
                    rows = await conn.fetch(
                        "SELECT id, user_phone, task FROM reminders WHERE remind_at <= NOW() AND sent = FALSE"
                    )
                    for r in rows:
                        await send_whatsapp(r["user_phone"], f"REMINDER: {r['task']}")
                        await conn.execute("UPDATE reminders SET sent = TRUE WHERE id = $1", r["id"])
                        print(f"Sent: {r['task']}")
        except Exception as e:
            print(f"Checker error: {e}")
        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    asyncio.create_task(check_reminders())
    yield
    await close_db()


app = FastAPI(title="Kazi", lifespan=lifespan)


async def transcribe_audio(media_url):
    async with httpx.AsyncClient() as client:
        resp = await client.get(media_url, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN))
    with open("/tmp/voice.ogg", "wb") as f:
        f.write(resp.content)
    with open("/tmp/voice.ogg", "rb") as f:
        transcript = openai_client.audio.transcriptions.create(model="whisper-1", file=f)
    return transcript.text


async def save_reminder(user_phone, task, hour, minute):
    if db_pool:
        now = datetime.now()
        remind_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if remind_at <= now:
            remind_at = remind_at.replace(day=now.day + 1)
        async with db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO reminders (user_phone, task, remind_at) VALUES ($1, $2, $3)",
                user_phone, task, remind_at
            )
        print(f"Saved: {task} at {remind_at}")


def parse_reminder(text, user_phone):
    if "REMINDER_JSON:" not in text:
        return text, None
    idx = text.find("REMINDER_JSON:")
    json_part = text[idx + 14:].strip()
    clean_text = text[:idx].strip()
    try:
        end = json_part.find("}") + 1
        data = json.loads(json_part[:end])
        return clean_text, data
    except Exception as e:
        print(f"Parse error: {e}")
        return text, None


async def get_response(user_message, user_phone):
    current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    response = claude.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=500,
        system=KAZI_SYSTEM.replace("{current_time}", current_time),
        messages=[{"role": "user", "content": user_message}]
    )
    text = response.content[0].text
    clean_text, reminder_data = parse_reminder(text, user_phone)
    if reminder_data:
        await save_reminder(user_phone, reminder_data["task"], reminder_data["hour"], reminder_data["minute"])
    return clean_text


@app.get("/", response_class=HTMLResponse)
async def root():
    return FileResponse("index.html")


@app.get("/health")
async def health():
    return {"status": "healthy", "db": "connected" if db_pool else "none"}


@app.post("/webhook")
async def webhook(From: str = Form(...), Body: str = Form(default=""), NumMedia: str = Form(default="0"), MediaUrl0: str = Form(default=None), MediaContentType0: str = Form(default=None)):
    try:
        if int(NumMedia) > 0 and MediaContentType0 and "audio" in MediaContentType0:
            user_message = await transcribe_audio(MediaUrl0)
        else:
            user_message = Body
        if not user_message.strip():
            return Response(content="", media_type="text/xml")
        response = await get_response(user_message, From)
        await send_whatsapp(From, response)
    except Exception as e:
        print(f"Error: {e}")
        await send_whatsapp(From, "Sorry, something went wrong.")
    return Response(content="<Response></Response>", media_type="text/xml")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
