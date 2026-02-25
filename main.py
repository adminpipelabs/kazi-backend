"""
Pipe Labs Dashboard - Main FastAPI Application
"""
import os
import json
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.database import engine, Base
from app.api.admin import router as admin_router
from app.api.agent import router as agent_router
from app.api.auth import router as auth_router


# Parse CORS origins from environment
def get_cors_origins():
    cors_env = os.getenv("CORS_ORIGINS", "")
    if not cors_env:
        return ["*"]
    
    # Try parsing as JSON array first
    try:
        origins = json.loads(cors_env)
        if isinstance(origins, list):
            return origins
    except json.JSONDecodeError:
        pass
    
    # Fall back to comma-separated
    return [origin.strip() for origin in cors_env.split(",") if origin.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # Auto-setup admin wallet on startup (one-time, safe to run multiple times)
    try:
        from app.core.database import AsyncSessionLocal
        from app.models.user import User
        from web3 import Web3
        from sqlalchemy import select
        
        ADMIN_WALLET = "0x61b6EF3769c88332629fA657508724a912b79101"
        async with AsyncSessionLocal() as db:
            wallet = Web3.to_checksum_address(ADMIN_WALLET)
            result = await db.execute(select(User).where(User.wallet_address == wallet))
            user = result.scalar_one_or_none()
            
            if not user or user.role != "admin":
                if user:
                    user.role = "admin"
                    user.is_active = True
                else:
                    user = User(wallet_address=wallet, role="admin", is_active=True)
                    db.add(user)
                await db.commit()
                print(f"✅ Admin wallet set: {wallet}")
            else:
                print(f"✅ Admin wallet already set: {wallet}")
    except Exception as e:
        print(f"⚠️ Admin setup warning: {e}")
    
    yield
    await engine.dispose()


app = FastAPI(
    title="Pipe Labs Dashboard API",
    version="0.1.0",
    lifespan=lifespan,
)

# Get CORS origins from environment
cors_origins = get_cors_origins()
print(f"🌐 CORS origins: {cors_origins}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(admin_router, prefix="/api/admin", tags=["Admin"])
app.include_router(agent_router, prefix="/api/agent", tags=["Agent"])
app.include_router(auth_router, prefix="/api/auth", tags=["Auth"])


@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": "Pipe Labs Dashboard API",
        "docs": "/docs",
        "health": "/health"
    }


@app.post("/force-admin-setup")
async def force_admin_setup():
    """Force admin setup - call this to set admin wallet"""
    try:
        from app.core.database import AsyncSessionLocal
        from app.models.user import User
        from web3 import Web3
        from sqlalchemy import select
        
        ADMIN_WALLET = "0x61b6EF3769c88332629fA657508724a912b79101"
        async with AsyncSessionLocal() as db:
            wallet = Web3.to_checksum_address(ADMIN_WALLET)
            result = await db.execute(select(User).where(User.wallet_address == wallet))
            user = result.scalar_one_or_none()
            
            if not user:
                user = User(wallet_address=wallet, role="admin", is_active=True)
                db.add(user)
                await db.commit()
                return {"message": "Admin created", "wallet": wallet, "role": "admin"}
            else:
                user.role = "admin"
                user.is_active = True
                await db.commit()
                return {"message": "Admin updated", "wallet": wallet, "role": user.role, "was": "updated"}
    except Exception as e:
        return {"error": str(e)}
