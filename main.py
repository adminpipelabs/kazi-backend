import os
import json
import httpx
import asyncio
import traceback
from datetime import datetime, timezone, date, timedelta
from zoneinfo import ZoneInfo
from contextlib import asynccontextmanager
from fastapi import FastAPI, Form, Request
from fastapi.responses import Response, HTMLResponse, FileResponse, JSONResponse
import anthropic
from openai import OpenAI
import asyncpg

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

STRIPE_PAYMENT_LINK = "https://buy.stripe.com/eVq3cwbT71Cs67T63U4ZG01"
FREE_DAILY_MESSAGES = 10

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
• Answer questions
• Set reminders
• Do calculations & translations
• Help with daily tasks

📱 You get 10 free messages per day — ask anything, set reminders, chat. Resets daily!

What would you like help with? 😊"""

TIMEZONE_MSG = """One quick thing - what's your timezone?

Just tell me your city or country, like:
• "Stockholm"
• "New York"  
• "Germany"
• "Tokyo"

This helps me send reminders at the right time!"""

LOW_MESSAGES_WARNING = f"""💡 2 free messages left today!

Tomorrow you get 10 more — or upgrade now:

Kazi Pro — $5/month
✓ Unlimited messages & reminders
✓ Powered by Claude AI + OpenAI voice
✓ Just like a Starbucks coffee ☕

Upgrade → {STRIPE_PAYMENT_LINK}"""

LIMIT_REACHED_MSG = f"""You've used your 10 free messages for today! ☕

Come back tomorrow for 10 more — or upgrade:

Kazi Pro — $5/month
✓ Unlimited messages & reminders
✓ Powered by Claude AI + OpenAI voice
✓ Just like a Starbucks coffee ☕

Upgrade → {STRIPE_PAYMENT_LINK}"""

KAZI_SYSTEM = """You are Kazi, a helpful AI assistant via WhatsApp. Keep responses short and friendly.

CURRENT TIME: {current_time} (User's local timezone: {timezone})

REMINDERS - VERY IMPORTANT:
When user asks for a reminder, you MUST:
1. Confirm the reminder
2. Add this EXACT format at the end: REMINDER_JSON:{{"task":"description","hour":HH,"minute":MM}}

TIME RULES:
- Use 24-hour format in the JSON (e.g., 8 AM = 8, 8 PM = 20)
- If the requested time is LATER than current time TODAY, set it for TODAY (not tomorrow)
- Only set for tomorrow if the time has ALREADY PASSED today
- "this morning" = 08:00
- "this afternoon" = 14:00  
- "this evening" / "tonight" = 19:00
- "in X minutes/hours" = current time + X

Example: If current time is 08:17 and user asks for "8:45 AM", that's TODAY (28 minutes from now), NOT tomorrow.

TIMEZONE: If user mentions their location or timezone, just say "Let me update your timezone" - the system handles it.

TIME QUERIES: If user asks "what time is it", tell them: {current_time}

INVITE: If user wants to share Kazi:
"Hey! Try Kazi - an AI assistant on WhatsApp: https://wa.me/15734125273?text=Hi%20Kazi"
"""

async def init_db():
    global db_pool
    if DATABASE_URL:
        db_pool = await asyncpg.create_pool(DATABASE_URL)
        async with db_pool.acquire() as conn:
            await conn.execute("CREATE TABLE IF NOT EXISTS reminders (id SERIAL PRIMARY KEY, user_phone VARCHAR(50) NOT NULL, task TEXT NOT NULL, remind_at TIMESTAMP NOT NULL, sent BOOLEAN DEFAULT FALSE)")
            await conn.execute("CREATE TABLE IF NOT EXISTS users (phone VARCHAR(50) PRIMARY KEY, timezone VARCHAR(50) DEFAULT NULL, welcomed BOOLEAN DEFAULT FALSE, plan VARCHAR(20) DEFAULT 'free', messages_today INT DEFAULT 0, last_message_date DATE DEFAULT CURRENT_DATE, stripe_customer_id VARCHAR(100) DEFAULT NULL)")
            try:
                await conn.execute("ALTER TABLE users ADD COLUMN welcomed BOOLEAN DEFAULT FALSE")
            except:
                pass
            try:
                await conn.execute("ALTER TABLE users ADD COLUMN plan VARCHAR(20) DEFAULT 'free'")
            except:
                pass
            try:
                await conn.execute("ALTER TABLE users ADD COLUMN messages_today INT DEFAULT 0")
            except:
                pass
            try:
                await conn.execute("ALTER TABLE users ADD COLUMN last_message_date DATE DEFAULT CURRENT_DATE")
            except:
                pass
            try:
                await conn.execute("ALTER TABLE users ADD COLUMN stripe_customer_id VARCHAR(100) DEFAULT NULL")
            except:
                pass
        print("DB ready")

async def close_db():
    if db_pool:
        await db_pool.close()

async def get_user(phone):
    if db_pool:
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("SELECT timezone, welcomed, plan, messages_today, last_message_date FROM users WHERE phone = $1", phone)
            if row:
                return dict(row)
            await conn.execute("INSERT INTO users (phone, welcomed, plan, messages_today, last_message_date) VALUES ($1, FALSE, 'free', 0, CURRENT_DATE) ON CONFLICT DO NOTHING", phone)
            return {"timezone": None, "welcomed": False, "plan": "free", "messages_today": 0, "last_message_date": date.today()}
    return {"timezone": None, "welcomed": False, "plan": "free", "messages_today": 0, "last_message_date": date.today()}

async def increment_message_count(phone):
    if db_pool:
        async with db_pool.acquire() as conn:
            result = await conn.fetchrow("""
                UPDATE users 
                SET messages_today = CASE 
                    WHEN last_message_date = CURRENT_DATE THEN messages_today + 1 
                    ELSE 1 
                END,
                last_message_date = CURRENT_DATE
                WHERE phone = $1
                RETURNING messages_today
            """, phone)
            return result["messages_today"] if result else 1
    return 1

async def set_user_tz(phone, tz_name):
    if db_pool:
        async with db_pool.acquire() as conn:
            await conn.execute("UPDATE users SET timezone = $1, welcomed = TRUE WHERE phone = $2", tz_name, phone)

async def set_user_welcomed(phone):
    if db_pool:
        async with db_pool.acquire() as conn:
            await conn.execute("UPDATE users SET welcomed = TRUE WHERE phone = $1", phone)

async def upgrade_user(phone):
    if db_pool:
        async with db_pool.acquire() as conn:
            await conn.execute("UPDATE users SET plan = 'pro' WHERE phone = $1", phone)

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
            remind_local = remind_local + timedelta(days=1)
        remind_utc = remind_local.astimezone(timezone.utc).replace(tzinfo=None)
        async with db_pool.acquire() as conn:
            await conn.execute("INSERT INTO reminders (user_phone, task, remind_at) VALUES ($1, $2, $3)", user_phone, task, remind_utc)
        print(f"Saved: {task} at {remind_utc} UTC (local: {remind_local})")
        return True
    return False

async def get_response(user_message, user_phone):
    user = await get_user(user_phone)
    user_tz = user.get("timezone")
    welcomed = user.get("welcomed", False)
    plan = user.get("plan", "free")
    messages_today = user.get("messages_today", 0)
    last_date = user.get("last_message_date")
    
    if last_date and last_date != date.today():
        messages_today = 0
    
    if plan == "free" and messages_today >= FREE_DAILY_MESSAGES:
        return LIMIT_REACHED_MSG
    
    new_count = await increment_message_count(user_phone)
    
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
    
    if "upgrade" in msg_lower or "subscribe" in msg_lower:
        return f"Upgrade to Kazi Pro for unlimited messages and reminders!\n\nOnly $5/month → {STRIPE_PAYMENT_LINK}"
    
    now_local = get_local_time(user_tz) if user_tz else datetime.now(timezone.utc)
    current_time = now_local.strftime("%Y-%m-%d %H:%M")
    tz_display = user_tz if user_tz else "UTC"
    
    system = KAZI_SYSTEM.replace("{current_time}", current_time).replace("{timezone}", tz_display)
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
    
    if plan == "free":
        remaining = FREE_DAILY_MESSAGES - new_count
        if remaining == 2:
            text += "\n\n" + LOW_MESSAGES_WARNING
    
    return text

@app.get("/", response_class=HTMLResponse)
async def root():
    return FileResponse("index.html")

@app.get("/favicon.png")
async def favicon():
    return FileResponse("favicon.png")

@app.get("/privacy", response_class=HTMLResponse)
async def privacy():
    return FileResponse("privacy.html")

@app.get("/terms", response_class=HTMLResponse)
async def terms():
    return FileResponse("terms.html")

@app.get("/cookies", response_class=HTMLResponse)
async def cookies():
    return FileResponse("cookies.html")

@app.get("/health")
async def health():
    return {"status": "healthy", "db": "connected" if db_pool else "none"}

@app.get("/stats")
async def stats():
    if db_pool:
        async with db_pool.acquire() as conn:
            total = await conn.fetchval("SELECT COUNT(*) FROM users")
            pro = await conn.fetchval("SELECT COUNT(*) FROM users WHERE plan = 'pro'")
            today = await conn.fetchval("SELECT COUNT(*) FROM users WHERE last_message_date = CURRENT_DATE")
            reminders = await conn.fetchval("SELECT COUNT(*) FROM reminders WHERE sent = FALSE")
            return {
                "total_users": total,
                "pro_users": pro,
                "active_today": today,
                "pending_reminders": reminders
            }
    return {"error": "no database"}

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

@app.post("/stripe-webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    try:
        event = json.loads(payload)
        if event["type"] == "checkout.session.completed":
            session = event["data"]["object"]
            customer_email = session.get("customer_details", {}).get("email", "")
            customer_phone = session.get("customer_details", {}).get("phone", "")
            print(f"Payment received: {customer_email} / {customer_phone}")
            
            if customer_phone and db_pool:
                digits = ''.join(filter(str.isdigit, customer_phone))
                async with db_pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE users SET plan = 'pro' WHERE phone LIKE '%' || $1 || '%'", 
                        digits[-10:]
                    )
                    print(f"Upgraded user with phone: {digits[-10:]}")
                    
        return JSONResponse({"status": "ok"})
    except Exception as e:
        print(f"Stripe webhook error: {e}")
        return JSONResponse({"error": str(e)}, status_code=400)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
