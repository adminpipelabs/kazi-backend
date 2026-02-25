import os
import json
import httpx
import asyncio
import traceback
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

TZ_MAP = {
    "usa": "America/Chicago", "us": "America/Chicago", "america": "America/New_York",
    "new york": "America/New_York", "nyc": "America/New_York", "ny": "America/New_York",
    "los angeles": "America/Los_Angeles", "la": "America/Los_Angeles", "california": "America/Los_Angeles",
    "chicago": "America/Chicago", "texas": "America/Chicago", "houston": "America/Chicago", "dallas": "America/Chicago",
    "denver": "America/Denver", "phoenix": "America/Phoenix", "seattle": "America/Los_Angeles",
    "miami": "America/New_York", "boston": "America/New_York", "atlanta": "America/New_York",
    "cst": "America/Chicago", "central": "America/Chicago",
    "est": "America/New_York", "eastern": "America/New_York",
    "pst": "America/Los_Angeles", "pacific": "America/Los_Angeles",
    "mst": "America/Denver", "mountain": "America/Denver",
    "uk": "Europe/London", "england": "Europe/London", "london": "Europe/London", "britain": "Europe/London",
    "gmt": "Europe/London", "bst": "Europe/London",
    "germany": "Europe/Berlin", "berlin": "Europe/Berlin", "munich": "Europe/Berlin", "frankfurt": "Europe/Berlin",
    "france": "Europe/Paris", "paris": "Europe/Paris",
    "spain": "Europe/Madrid", "madrid": "Europe/Madrid", "barcelona": "Europe/Madrid",
    "italy": "Europe/Rome", "rome": "Europe/Rome", "milan": "Europe/Rome",
    "netherlands": "Europe/Amsterdam", "amsterdam": "Europe/Amsterdam", "holland": "Europe/Amsterdam",
    "belgium": "Europe/Brussels", "brussels": "Europe/Brussels",
    "sweden": "Europe/Stockholm", "stockholm": "Europe/Stockholm",
    "norway": "Europe/Oslo", "oslo": "Europe/Oslo",
    "denmark": "Europe/Copenhagen", "copenhagen": "Europe/Copenhagen",
    "finland": "Europe/Helsinki", "helsinki": "Europe/Helsinki",
    "poland": "Europe/Warsaw", "warsaw": "Europe/Warsaw",
    "austria": "Europe/Vienna", "vienna": "Europe/Vienna",
    "switzerland": "Europe/Zurich", "zurich": "Europe/Zurich", "geneva": "Europe/Zurich",
    "portugal": "Europe/Lisbon", "lisbon": "Europe/Lisbon",
    "ireland": "Europe/Dublin", "dublin": "Europe/Dublin",
    "greece": "Europe/Athens", "athens": "Europe/Athens",
    "cet": "Europe/Paris", "cest": "Europe/Paris",
    "japan": "Asia/Tokyo", "tokyo": "Asia/Tokyo", "jst": "Asia/Tokyo",
    "china": "Asia/Shanghai", "shanghai": "Asia/Shanghai", "beijing": "Asia/Shanghai",
    "hong kong": "Asia/Hong_Kong", "hongkong": "Asia/Hong_Kong",
    "singapore": "Asia/Singapore",
    "korea": "Asia/Seoul", "seoul": "Asia/Seoul", "south korea": "Asia/Seoul",
    "india": "Asia/Kolkata", "mumbai": "Asia/Kolkata", "delhi": "Asia/Kolkata", "bangalore": "Asia/Kolkata", "ist": "Asia/Kolkata",
    "thailand": "Asia/Bangkok", "bangkok": "Asia/Bangkok",
    "vietnam": "Asia/Ho_Chi_Minh", "hanoi": "Asia/Ho_Chi_Minh",
    "indonesia": "Asia/Jakarta", "jakarta": "Asia/Jakarta",
    "malaysia": "Asia/Kuala_Lumpur", "kuala lumpur": "Asia/Kuala_Lumpur",
    "philippines": "Asia/Manila", "manila": "Asia/Manila",
    "taiwan": "Asia/Taipei", "taipei": "Asia/Taipei",
    "uae": "Asia/Dubai", "dubai": "Asia/Dubai", "abu dhabi": "Asia/Dubai",
    "saudi": "Asia/Riyadh", "saudi arabia": "Asia/Riyadh", "riyadh": "Asia/Riyadh",
    "israel": "Asia/Jerusalem", "tel aviv": "Asia/Jerusalem", "jerusalem": "Asia/Jerusalem",
    "turkey": "Europe/Istanbul", "istanbul": "Europe/Istanbul",
    "russia": "Europe/Moscow", "moscow": "Europe/Moscow",
    "australia": "Australia/Sydney", "sydney": "Australia/Sydney", "melbourne": "Australia/Melbourne",
    "brisbane": "Australia/Brisbane", "perth": "Australia/Perth", "aest": "Australia/Sydney",
    "new zealand": "Pacific/Auckland", "auckland": "Pacific/Auckland", "nz": "Pacific/Auckland",
    "brazil": "America/Sao_Paulo", "sao paulo": "America/Sao_Paulo", "rio": "America/Sao_Paulo",
    "mexico": "America/Mexico_City", "mexico city": "America/Mexico_City",
    "canada": "America/Toronto", "toronto": "America/Toronto", "vancouver": "America/Vancouver",
    "argentina": "America/Buenos_Aires", "buenos aires": "America/Buenos_Aires",
    "south africa": "Africa/Johannesburg", "johannesburg": "Africa/Johannesburg",
    "nigeria": "Africa/Lagos", "lagos": "Africa/Lagos",
    "kenya": "Africa/Nairobi", "nairobi": "Africa/Nairobi",
    "egypt": "Africa/Cairo", "cairo": "Africa/Cairo",
    "utc": "UTC",
}

WELCOME_MSG = """Hi! I'm Kazi, your AI assistant on WhatsApp. I help you get things done with voice and text.

I can:
- Answer questions
- Set reminders
- Do calculations & translations
- Help with daily tasks

I'm designed to be quick and useful - like having a helpful assistant in your pocket!

What would you like help with? 😊"""

TIMEZONE_MSG = """One quick thing - what's your timezone?

Just tell me your city or country, like:
- "Stockholm"
- "New York"  
- "Germany"
- "Tokyo"

This helps me send reminders at the right time!"""

KAZI_SYSTEM = """You are Kazi, a helpful AI assistant via WhatsApp. Keep responses short and friendly.

YOU CAN SET REMINDERS! When user asks for a reminder, confirm it AND add this at the end:
REMINDER_JSON:{"task":"call Emma","hour":15,"minute":0}
Convert time to 24h format. Current time: {current_time} ({timezone})

INVITE FRIENDS: If user wants to invite or share Kazi, give them this message to forward:

"Hey! I've been using Kazi - an AI assistant on WhatsApp that sets reminders, answers questions, and helps with tasks. Try it!

Join here: https://wa.me/15734125273?text=Hi%20Kazi"

TIMEZONE: If user mentions their location or timezone, DO NOT handle it yourself. Just say "Let me update your timezone" - the system will handle it."""

async def init_db():
    global db_pool
    if DATABASE_URL:
        db_pool = await asyncpg.create_pool(DATABASE_URL)
        async with db_pool.acquire() as conn:
            await conn.execute("CREATE TABLE IF NOT EXISTS reminders (id SERIAL PRIMARY KEY, user_phone VARCHAR(50) NOT NULL, task TEXT NOT NULL, remind_at TIMESTAMP NOT NULL, sent BOOLEAN DEFAULT FALSE)")
            await conn.execute("CREATE TABLE IF NOT EXISTS users (phone VARCHAR(50) PRIMARY KEY, timezone VARCHAR(50) DEFAULT NULL, welcomed BOOLEAN DEFAULT FALSE)")
            try:
                await conn.execute("ALTER TABLE users ADD COLUMN welcomed BOOLEAN DEFAULT FALSE")
            except:
                pass
        print("DB ready")

async def close_db():
    if db_pool:
        await db_pool.close()

async def get_user(phone):
    if db_pool:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT timezone, welcomed FROM users WHERE phone = $1", phone)
            if row:
                return row["timezone"], row["welcomed"]
            await conn.execute("INSERT INTO users (phone, welcomed) VALUES ($1, FALSE) ON CONFLICT DO NOTHING", phone)
    return None, False

async def set_user_tz(phone, tz_name):
    if db_pool:
        async with db_pool.acquire() as conn:
            await conn.execute("UPDATE users SET timezone = $1, welcomed = TRUE WHERE phone = $2", tz_name, phone)

async def set_user_welcomed(phone):
    if db_pool:
        async with db_pool.acquire() as conn:
            await conn.execute("UPDATE users SET welcomed = TRUE WHERE phone = $1", phone)

def resolve_tz(text):
    text = text.lower().strip()
    if text in TZ_MAP:
        return TZ_MAP[text]
    for key, tz in TZ_MAP.items():
        if key in text or text in key:
            return tz
    try:
        ZoneInfo(text)
        return text
    except:
        pass
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
    print(f"Transcribing audio: {media_url}")
    async with httpx.AsyncClient(follow_redirects=True) as client:
        resp = await client.get(media_url, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN))
        print(f"Audio download status: {resp.status_code}, size: {len(resp.content)}")
    with open("/tmp/voice.ogg", "wb") as f:
        f.write(resp.content)
    with open("/tmp/voice.ogg", "rb") as f:
        transcript = openai_client.audio.transcriptions.create(model="whisper-1", file=f)
    print(f"Transcription: {transcript.text}")
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
        print(f"Saved: {task} at {remind_utc} UTC")

async def get_response(user_message, user_phone):
    user_tz, welcomed = await get_user(user_phone)
    msg_lower = user_message.lower().strip()
    
    if not welcomed:
        await set_user_welcomed(user_phone)
        return WELCOME_MSG + "\n\n" + TIMEZONE_MSG
    
    tz_triggers = ["timezone", "time zone", "change tz", "my time is", "i'm in", "im in", "i am in", "i live in", "living in", "based in", "my time", "set it to", "cst", "est", "pst", "gmt", "cet"]
    if user_tz is None or any(trigger in msg_lower for trigger in tz_triggers):
        words = msg_lower
        for remove in ["set", "change", "my", "timezone", "time zone", "to", "tz", "is", "i'm", "im", "i am", "i live", "living", "based", "in", "the", "please", "can you", "it"]:
            words = words.replace(remove, " ")
        words = " ".join(words.split()).strip()
        
        resolved = resolve_tz(words)
        if not resolved:
            resolved = resolve_tz(msg_lower)
        
        if resolved:
            await set_user_tz(user_phone, resolved)
            local = get_local_time(resolved)
            return f"✅ Got it! Timezone set to {resolved}.\nYour local time: {local.strftime('%H:%M')}\n\nHow can I help you?"
        elif user_tz is None:
            return f"Hmm, I didn't recognize that. Try a city like 'London', 'New York', 'Tokyo', or 'CST', 'EST', 'CET'."
    
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
        print(f"Webhook received: From={From}, Body={Body}, NumMedia={NumMedia}")
        if int(NumMedia) > 0 and MediaContentType0 and "audio" in MediaContentType0:
            print(f"Processing audio: {MediaUrl0}")
            user_message = await transcribe_audio(MediaUrl0)
        else:
            user_message = Body
        if not user_message.strip():
            return Response(content="", media_type="text/xml")
        response = await get_response(user_message, From)
        await send_whatsapp(From, response)
    except Exception as e:
        print(f"Error: {e}")
        print(traceback.format_exc())
        await send_whatsapp(From, "Sorry, something went wrong.")
    return Response(content="<Response></Response>", media_type="text/xml")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
