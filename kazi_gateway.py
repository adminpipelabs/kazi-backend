"""
Kazi WhatsApp Gateway — routing layer between WhatsApp (Twilio) and Pipe Labs products.

Core principle: Kazi routes. Products think.
Kazi does not interpret messages. It looks up the sender's connection,
passes the raw message to the product's API, and returns the reply.

Tables (Postgres):
  - kazi_connections: whatsapp_number -> (client_id, product, api endpoint, api key)
  - kazi_scheduled:   cron-like push jobs per connection

Phase 2: Always On (ao.aifredoapp.com) is the only connected product.
"""

import os
import uuid
import asyncio
import httpx
from datetime import datetime, timezone

# ---------- Env ----------
ALWAYS_ON_API_ENDPOINT = os.getenv("ALWAYS_ON_API_ENDPOINT", "https://ao.aifredoapp.com")
ALWAYS_ON_API_KEY = os.getenv("ALWAYS_ON_API_KEY", "")

# Fallback messages (per spec)
MSG_NOT_CONNECTED = "To connect your account, log into ao.aifredoapp.com and scan the QR code in Admin."
MSG_TIMEOUT = "Fred is taking longer than usual. Try again or visit ao.aifredoapp.com."
MSG_DOWN = "Something went wrong. Try again or visit ao.aifredoapp.com."
MSG_UNKNOWN = "I ran into an issue. Please try again in a moment."
MSG_LINKED = (
    "You're connected to Always On ✓\n\n"
    "You can now message me here anytime. Try:\n"
    "- \"What are my new leads?\"\n"
    "- \"Did the blog get drafted?\"\n"
    "- \"What's my visibility score?\""
)
MSG_LINK_BAD_TOKEN = "❌ That link code didn't work — it may have expired. Go to ao.aifredoapp.com → Admin and scan a fresh QR code."

# Ack used to satisfy Twilio's 5s timeout while Fred thinks.
ACK_MESSAGE = "..."


# ---------- Schema ----------
async def init_gateway_schema(db_pool):
    """Create the two Kazi gateway tables if they don't exist (Postgres)."""
    if not db_pool:
        return
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS kazi_connections (
                id                   TEXT PRIMARY KEY,
                whatsapp_number      TEXT UNIQUE NOT NULL,
                client_id            TEXT NOT NULL,
                product              TEXT NOT NULL,
                product_api_endpoint TEXT NOT NULL,
                product_api_key      TEXT NOT NULL,
                linked_at            TIMESTAMPTZ DEFAULT NOW(),
                last_active          TIMESTAMPTZ
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS kazi_scheduled (
                id                   TEXT PRIMARY KEY,
                whatsapp_number      TEXT NOT NULL,
                product              TEXT NOT NULL,
                client_id            TEXT NOT NULL,
                schedule             TEXT NOT NULL,   -- 'HH:MM'
                days_of_week         TEXT DEFAULT '1,2,3,4,5', -- ISO weekday, 1=Mon..7=Sun
                message_type         TEXT NOT NULL,   -- e.g. 'daily_digest', 'weekly_report'
                active               BOOLEAN DEFAULT TRUE,
                last_sent            TIMESTAMPTZ
            )
            """
        )


# ---------- Connection lookup ----------
async def get_connection(db_pool, whatsapp_number: str):
    if not db_pool:
        return None
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM kazi_connections WHERE whatsapp_number = $1",
            whatsapp_number,
        )
        return dict(row) if row else None


async def upsert_connection(
    db_pool,
    whatsapp_number: str,
    client_id: str,
    product: str,
    product_api_endpoint: str,
    product_api_key: str,
):
    if not db_pool:
        return None
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO kazi_connections
                (id, whatsapp_number, client_id, product, product_api_endpoint, product_api_key, linked_at, last_active)
            VALUES ($1, $2, $3, $4, $5, $6, NOW(), NOW())
            ON CONFLICT (whatsapp_number)
            DO UPDATE SET
                client_id            = EXCLUDED.client_id,
                product              = EXCLUDED.product,
                product_api_endpoint = EXCLUDED.product_api_endpoint,
                product_api_key      = EXCLUDED.product_api_key,
                last_active          = NOW()
            """,
            str(uuid.uuid4()),
            whatsapp_number,
            client_id,
            product,
            product_api_endpoint,
            product_api_key,
        )


async def touch_connection(db_pool, whatsapp_number: str):
    if not db_pool:
        return
    async with db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE kazi_connections SET last_active = NOW() WHERE whatsapp_number = $1",
            whatsapp_number,
        )


async def delete_connection(db_pool, whatsapp_number: str):
    if not db_pool:
        return
    async with db_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM kazi_connections WHERE whatsapp_number = $1",
            whatsapp_number,
        )


# ---------- HTTP w/ retry ----------
async def _post_with_retry(url: str, headers: dict, json_body: dict,
                           timeout_seconds: float = 30.0, retries: int = 1):
    """POST once, retry once on non-2xx or exception, 5s between attempts."""
    last_error = None
    for attempt in range(retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                resp = await client.post(url, headers=headers, json=json_body)
            if resp.status_code < 400:
                return resp
            last_error = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        except httpx.TimeoutException as e:
            last_error = e
            # Timeout is fatal for this flow — don't retry, let caller surface MSG_TIMEOUT
            raise
        except Exception as e:
            last_error = e
        if attempt < retries:
            await asyncio.sleep(5)
    if last_error:
        raise last_error
    return None


# ---------- Linking: CONNECT-<token> ----------
CONNECT_PREFIX = "CONNECT-"


def extract_connect_token(message: str):
    """Return the token string if message is a CONNECT- linking message, else None."""
    s = (message or "").strip()
    if s.upper().startswith(CONNECT_PREFIX):
        return s[len(CONNECT_PREFIX):].strip()
    return None


async def verify_token_with_always_on(token: str, whatsapp_number: str):
    """
    Call Always On POST /api/kazi/verify-ao-token to exchange a QR token for a clientId.
    Returns dict like {"clientId": "..."} on success, or None.
    """
    if not ALWAYS_ON_API_KEY:
        print(f"[GATEWAY] ALWAYS_ON_API_KEY not set — cannot verify token")
        return None
    url = f"{ALWAYS_ON_API_ENDPOINT.rstrip('/')}/api/kazi/verify-ao-token"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {ALWAYS_ON_API_KEY}",
    }
    print(f"[GATEWAY] Calling Always On verify-token endpoint: {url}")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                url,
                headers=headers,
                json={"token": token, "whatsappNumber": whatsapp_number},
            )
        print(f"[GATEWAY] verify-ao-token response: HTTP {resp.status_code}")
        if resp.status_code == 200:
            return resp.json()
        print(f"[GATEWAY] verify-ao-token error body: {resp.text[:200]}")
    except Exception as e:
        print(f"[GATEWAY] verify-ao-token exception: {e}")
    return None


async def handle_connect_message(db_pool, whatsapp_number: str, token: str) -> str:
    """
    Called when an incoming message is `CONNECT-<token>`.
    Verifies with Always On, stores the connection, returns the reply to send.
    """
    print(f"[GATEWAY] CONNECT token received from {whatsapp_number}")
    result = await verify_token_with_always_on(token, whatsapp_number)
    if not result or not result.get("clientId"):
        print(f"[GATEWAY] Token verification failed for {whatsapp_number}")
        return MSG_LINK_BAD_TOKEN

    client_id = result["clientId"]
    print(f"[GATEWAY] Token verified — client_id: {client_id}, name: {result.get('name', '?')}")
    await upsert_connection(
        db_pool,
        whatsapp_number=whatsapp_number,
        client_id=client_id,
        product="Always On",
        product_api_endpoint=ALWAYS_ON_API_ENDPOINT,
        product_api_key=ALWAYS_ON_API_KEY,
    )
    print(f"[GATEWAY] Connection stored for {whatsapp_number}")
    return MSG_LINKED


# ---------- Routing: call product API, no LLM in Kazi ----------
async def call_product_message(connection: dict, message: str,
                               whatsapp_number: str, channel: str = "whatsapp"):
    """
    POST {endpoint}/api/kazi/ao/{client_id}/message with Bearer auth.
    Returns the reply string. Raises on failure.
    """
    endpoint = connection["product_api_endpoint"].rstrip("/")
    client_id = connection["client_id"]
    url = f"{endpoint}/api/kazi/ao/{client_id}/message"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {connection['product_api_key']}",
    }
    body = {
        "message": message,
        "channel": channel,
        "whatsappNumber": whatsapp_number,
    }
    resp = await _post_with_retry(url, headers, body, timeout_seconds=30.0, retries=1)
    data = resp.json()
    reply = data.get("reply")
    if not reply:
        raise RuntimeError("Product returned no reply")
    return reply


async def process_and_reply(db_pool, send_whatsapp, whatsapp_number: str,
                            message: str, connection: dict):
    """
    Async worker: calls product, then sends Fred's real reply as a second
    WhatsApp message. Sends a fallback on error.
    """
    print(f"[GATEWAY] Routing {whatsapp_number} to {connection.get('product', '?')} client {connection.get('client_id', '?')}")
    try:
        reply = await call_product_message(connection, message, whatsapp_number)
        print(f"[GATEWAY] Reply received, sending to WhatsApp")
        await send_whatsapp(whatsapp_number, reply)
        await touch_connection(db_pool, whatsapp_number)
    except httpx.TimeoutException:
        print(f"[GATEWAY] Timeout calling product for {whatsapp_number}")
        await send_whatsapp(whatsapp_number, MSG_TIMEOUT)
    except httpx.HTTPError as e:
        print(f"[GATEWAY] HTTP error calling product for {whatsapp_number}: {e}")
        await send_whatsapp(whatsapp_number, MSG_DOWN)
    except Exception as e:
        print(f"[GATEWAY] process_and_reply error for {whatsapp_number}: {e}")
        await send_whatsapp(whatsapp_number, MSG_UNKNOWN)


# ---------- Scheduled push messages ----------
async def run_scheduled_messages(db_pool, send_whatsapp):
    """
    Tick the scheduler once. Call product for each due job, push via WhatsApp.
    Uses Postgres clock; schedule column is 'HH:MM' in UTC.
    (If you need per-client timezones later, extend the table with a tz column.)
    """
    if not db_pool:
        return
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT s.*, c.product_api_endpoint, c.product_api_key
            FROM kazi_scheduled s
            JOIN kazi_connections c ON c.whatsapp_number = s.whatsapp_number
            WHERE s.active = TRUE
              AND to_char(NOW() AT TIME ZONE 'UTC', 'HH24:MI') = s.schedule
              AND position(to_char(NOW() AT TIME ZONE 'UTC', 'ID') IN s.days_of_week) > 0
              AND (s.last_sent IS NULL OR s.last_sent < NOW() - INTERVAL '23 hours')
            """
        )

    for job in rows:
        connection = {
            "product_api_endpoint": job["product_api_endpoint"],
            "product_api_key": job["product_api_key"],
            "client_id": job["client_id"],
        }
        try:
            reply = await call_product_message(
                connection,
                message=f"Generate {job['message_type']}",
                whatsapp_number=job["whatsapp_number"],
                channel="scheduled",
            )
            await send_whatsapp(job["whatsapp_number"], reply)
            async with db_pool.acquire() as conn:
                await conn.execute(
                    "UPDATE kazi_scheduled SET last_sent = NOW() WHERE id = $1",
                    job["id"],
                )
        except Exception as e:
            print(f"[Kazi] scheduled job {job['id']} failed: {e}")


async def scheduled_loop(db_pool, send_whatsapp, interval_seconds: int = 60):
    """Background task: runs scheduled messages every minute."""
    print("[Kazi] scheduled loop started")
    while True:
        try:
            await run_scheduled_messages(db_pool, send_whatsapp)
        except Exception as e:
            print(f"[Kazi] scheduled_loop tick error: {e}")
        await asyncio.sleep(interval_seconds)


# ---------- Default schedule helper ----------
async def install_default_schedules(db_pool, whatsapp_number: str, client_id: str):
    """
    On link, install the Always On default schedules for this connection:
      - Daily digest:   08:00 UTC, Mon-Fri
      - Weekly report:  09:00 UTC, Monday
    Idempotent per (whatsapp_number, message_type).
    """
    if not db_pool:
        return
    defaults = [
        ("daily_digest", "08:00", "1,2,3,4,5"),
        ("weekly_report", "09:00", "1"),
    ]
    async with db_pool.acquire() as conn:
        for mtype, schedule, dow in defaults:
            existing = await conn.fetchval(
                "SELECT id FROM kazi_scheduled WHERE whatsapp_number = $1 AND message_type = $2",
                whatsapp_number,
                mtype,
            )
            if existing:
                continue
            await conn.execute(
                """
                INSERT INTO kazi_scheduled
                    (id, whatsapp_number, product, client_id, schedule, days_of_week, message_type, active)
                VALUES ($1, $2, 'Always On', $3, $4, $5, $6, TRUE)
                """,
                str(uuid.uuid4()),
                whatsapp_number,
                client_id,
                schedule,
                dow,
                mtype,
            )
