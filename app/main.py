"""FastAPI app entry point for Feishu bug bot ingestion."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.webhook import router as webhook_router
from app.config import settings
from app.infra.dedupe import MessageDedupeStore
from app.infra.feishu import FeishuAPIClient

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    feishu_client = FeishuAPIClient(settings)
    dedupe_store = MessageDedupeStore(settings.feishu_dedupe_ttl_seconds)
    app.state.feishu_client = feishu_client
    app.state.dedupe_store = dedupe_store
    try:
        yield
    finally:
        await feishu_client.close()
        logger.info("Shutting down %s", settings.service_name)


app = FastAPI(
    title="Teamo Feishu Bug Bot",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(webhook_router, prefix="/api/v1/feishu", tags=["feishu"])


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.service_name}
