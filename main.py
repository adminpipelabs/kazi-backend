from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import os
import httpx

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

waitlist = []

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
        if msg_type == "text":
            text = msg["text"]["body"]
            background_tasks.add_task(process_text, phone, text)
        return {"status": "ok"}
    except Exception as e:
        print(f"Webhook error: {e}")
        return {"status": "error"}

async def process_text(phone: str, text: str):
    await send_message(phone, f"👋 Hi! You said: {text}\n\nI'm Kazi, your voice assistant. Full features coming soon!")

async def send_message(phone: str, text: str):
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"https://graph.facebook.com/v18.0/{WHATSAPP_PHONE_ID}/messages",
            headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"},
            json={"messaging_product": "whatsapp", "to": phone, "type": "text", "text": {"body": text}}
        )
        print(f"Send result: {r.status_code} {r.text}")

@app.post("/api/waitlist")
async def join_waitlist(signup: WaitlistSignup):
    if signup.email not in waitlist:
        waitlist.append(signup.email)
    return {"status": "ok", "count": len(waitlist)}

@app.get("/api/waitlist/count")
async def get_waitlist_count():
    return {"count": len(waitlist) + 500}
