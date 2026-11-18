import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import db
from app.auth import router as auth_router
from app.config import settings
from app.db import get_pool
from app.jobs import check_queue
from app.routers.insights import router as insights_router
from app.routers.meetings import router as meetings_router
from app.routers.tasks import router as tasks_router
from app.routers.webhooks import router as webhooks_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_: FastAPI):
    if len(settings.jwt_secret) < 32:
        raise RuntimeError("JWT_SECRET must be at least 32 characters (see .env.example)")
    if settings.llm_provider != "fake" and not settings.llm_api_key:
        raise RuntimeError("LLM_API_KEY is not set (or use LLM_PROVIDER=fake to run without a key)")
    await db.init_db()
    check_queue()
    log.info(
        "ActionPilot ready: llm=%s model=%s stt=%s queue=%s email=%s webhooks=%s",
        settings.llm_provider,
        settings.chat_model,
        "on" if settings.stt_api_key else "off",
        settings.queue_backend,
        settings.email_backend,
        "on" if settings.webhook_secret else "off",
    )
    yield
    await db.close_db()


app = FastAPI(
    title="ActionPilot",
    description="Meeting transcripts in; tasks, reminders and human-approved follow-up emails out.",
    version="0.1.0",
    lifespan=lifespan,
    root_path=settings.root_path,
)
for r in (auth_router, meetings_router, tasks_router, insights_router, webhooks_router):
    app.include_router(r)


@app.get("/health", tags=["meta"])
async def health():
    async with get_pool().connection() as conn:
        await conn.execute("SELECT 1")
    return {"status": "ok"}
