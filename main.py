"""
Kazi - Voice-first AI Agent via WhatsApp
"""

import os
import json
import httpx
import asyncio
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Form
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

KAZI_SYSTEM = """You are Kazi, a helpful AI assistant via WhatsApp. Keep responses short (under 300 chars). Respond in the user's language.

IMPORTANT - YOU CAN SET REMINDERS! When user asks for a reminder, respond with confirmation AND include this JSON:
```json
cat > requirements.txt << 'EOF'
fastapi==0.109.0
uvicorn==0.27.0
httpx==0.26.0
anthropic==0.43.0
openai>=1.50.0
python-multipart==0.0.6
asyncpg==0.29.0
