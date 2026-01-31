from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
import os

app = FastAPI(title="Kazi API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    if params.get("hub.verify_token") == os.getenv("WHATSAPP_VERIFY_TOKEN", "kazi_verify"):
        return int(params.get("hub.challenge", 0))
    raise HTTPException(403, "Invalid")

@app.post("/webhook")
async def handle_webhook(request: Request):
    body = await request.json()
    print(f"Webhook: {body}")
    return {"status": "ok"}

@app.post("/api/waitlist")
async def join_waitlist(signup: WaitlistSignup):
    if signup.email not in waitlist:
        waitlist.append(signup.email)
        print(f"Signup: {signup.email}")
    return {"status": "ok", "count": len(waitlist)}

@app.get("/api/waitlist/count")
async def get_waitlist_count():
    return {"count": len(waitlist) + 500}
