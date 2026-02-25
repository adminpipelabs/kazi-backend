import os
import json
import httpx
import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
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

TZ_ALIASES = {
    "cst": "America/Chicago", "central": "America/Chicago", "chicago": "America/Chicago",
    "est": "America/New_York", "eastern": "America/New_York", "new york": "America/New_York",
    "pst": "America/Los_Angeles", "pacific": "America/Los_Angeles", "la": "America/Los_Angeles",
    "mst": "America/Denver", "mountain": "America/Denver", "denver": "America/Denver",
    "gmt": "Europe/London", "uk": "Europe/London", "london": "Europe/London",
    "cet": "Europe/Paris", "paris": "Europe/Paris", "berlin": "Europe/Berlin",
    "stockholm": "Europe/Stockholm", "sweden": "Europe/Stockholm",
    "amsterdam": "Europe/Amsterdam", "netherlands": "Europe/Amsterdam",
    "tokyo": "Asia/Tokyo", "japan": "Asia/Tokyo", "jst": "Asia/Tokyo",
    "sydney": "Australia/Sydney", "australia": "Australia/Sydney", "aest": "Australia/Sydney",
    "dubai": "Asia/Dubai", "uae": "Asia/Dubai",
    "singapore": "Asia/Singapore", "sgt": "Asia/Singapore",
    "hong kong": "Asia/Hong_Kong", "hkt": "Asia/Hong_Kong",
    "mumbai": "Asia/Kolkata", "india": "Asia/Kolkata", "ist": "Asia/Kolkata",
    "beijing": "Asia/Shanghai", "china": "Asia/Shanghai",
    "utc": "UTC",
}

KAZI_SYSTEM = """You are Kazi, a helpful AI assistant via WhatsApp. Keep responses short.

YOU CAN SET REMINDERS! When user asks for a reminder, confirm it AND add this at the end:
REMINDER_JSON:{"task":"call Emma","hour":15,"minute":0}
Convert time to 24h format. Current time: {current_time} ({timezone})

INVITE FRIENDS: If user wants to invite or share Kazi, give them this message to forward:

"Hey! I've been using Kazi - an AI assistant on WhatsApp that sets reminders, answers questions, and helps with tasks. Try it!

Join here: https://wa.me/15734125273?text=Hi%20Kazi"

TIMEZONE: User can say "set timezone to CET" or "set timezone to Europe/Stockholm" etc."""

async def init_db():
    global db_pool
    if DATABASE_URL:
        db_pool = await asyncpg.create_pool(DATABASE_URL)
        async with db_pool.acquire() as conn:
            await conn.execute("CREATE TABLE IF NOT EXISTS reminders (id SERIAL PRIMARY KEY, user_phone VARCHAR(50) NOT NULL, task TEXT NOT NULL, remind_at TIMESTAMP NOT NULL, sent BOOLEAN DEFAULT FALSE)")
            await conn.execute("CREATE TABLE IF NOT EXISTS users (phone VARCHAR(50) PRIMARY KEY, timezone VARCHAR(50) DEFAULT NULL)")
        print("DB ready")

async def close_db():
    if db_pool:
        await db_pool.close()

async def get_user_tz(phone):
    if db_pool:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT timezone FROM users WHERE phone = $1", phone)
            if row and row["timezone"]:
                return row["timezone"]
            await conn.execute("INSERT INTO users (phone) VALUES ($1) ON CONFLICT DO NOTHING", phone)
    return None

async def set_user_tz(phone, tz_name):
    if db_pool:
        async with db_pool.acquire() as conn:
            await conn.execute("INSERT INTO users (phone, timezone) VALUES ($1, $2) ON CONFLICT (phone) DO UPDATE SET timezone = $2", phone, tz_name)

def resolve_tz(text):
    text = text.lower().strip()
    if text in TZ_ALIASES:
        return TZ_ALIASES[text]
    try:
        ZoneInfo(text)
        return text
    except:
        for alias, tz in TZ_ALIASES.items():
            if alias in text:
                return tz
    return None

def get_local_time(tz_name):
    try:
        tz = ZoneInfo(tz_name)
        return datetime.now(tz)
    except:
        return datetime.now(timezone.utc)

async def send_whatsapp(to, body):
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
    async with httpx.AsyncClient() as client:
        await client.post(url, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN), data={"From": "whatsapp:+15734125273", "To": to, "Body": body})

async def check_reminders():
    print("Reminder checker started")
    while True:
        try:
            if db_pool:
                async with db_pool.acquire() as conn:
                    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
                    rows = await conn.fetch("SELECT id, user_phone, task FROM reminders WHERE remind_at <= $1 AND sent = FALSE", now_utc)
                    for r in rows:
                        await send_whatsapp(r["user_phone"], f"⏰ REMINDER: {r['task']}")
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

async def save_reminder(user_phone, task, hour, minute, tz_name):
    if db_pool:
        try:
            tz = ZoneInfo(tz_name) if tz_name else timezone.utc
        except:
            tz = timezone.utc
        now_local = datetime.now(tz)
        remind_local = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if remind_local <= now_local:
            remind_local = remind_local.replace(day=remind_local.day + 1)
        remind_utc = remind_local.astimezone(timezone.utc).replace(tzinfo=None)
        async with db_pool.acquire() as conn:
            await conn.execute("INSERT INTO reminders (user_phone, task, remind_at) VALUES ($1, $2, $3)", user_phone, task, remind_utc)
        print(f"Saved: {task} at {remind_utc} UTC (local: {remind_local})")

async def get_response(user_message, user_phone):
    user_tz = await get_user_tz(user_phone)
    msg_lower = user_message.lower()
    
    if "timezone" in msg_lower or "time zone" in msg_lower or msg_lower.startswith("set tz"):
        words = msg_lower.replace("set", "").replace("timezone", "").replace("time zone", "").replace("to", "").replace("tz", "").replace("my", "").split()
        for word in words:
            resolved = resolve_tz(word)
            if resolved:
                await set_user_tz(user_phone, resolved)
                local = get_local_time(resolved)
                return f"✅ Timezone set to {resolved}!\nYour local time: {local.strftime('%H:%M')}"
        return "Couldn't find that timezone. Try: CST, EST, PST, GMT, CET, or Europe/Stockholm, Asia/Tokyo etc."
    
    if user_tz is None:
        await set_user_tz(user_phone, "UTC")
        return "Welcome to Kazi! 👋\n\nWhat's your timezone? Examples:\n• set timezone to CST\n• set timezone to CET\n• set timezone to Europe/Stockholm\n\nThis helps me send reminders at the right time!"
    
    now_local = get_local_time(user_tz)
    current_time = now_local.strftime("%Y-%m-%d %H:%M")
    
    system = KAZI_SYSTEM.replace("{current_time}", current_time).replace("{timezone}", user_tz)
    response = claude.messages.create(model="claude-sonnet-4-20250514", max_tokens=500, system=system, messages=[{"role": "user", "content": user_message}])
    text = response.content[0].text
    
    if "REMINDER_JSON:" in text:
        try:
            idx = text.find("REMINDER_JSON:")
            json_str = text[idx + 14:].strip()
            end = json_str.find("}") + 1
            data = json.loads(json_str[:end])
            await save_reminder(user_phone, data["task"], data["hour"], data["minute"], user_tz)
            text = text[:idx].strip()
        except Exception as e:
            print(f"Parse error: {e}")
    return text

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
