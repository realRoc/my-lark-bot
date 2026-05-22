"""FastAPI routes for Feishu webhook callbacks."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from app.config import Settings, settings
from app.domain.service import IssueIngestionService

logger = logging.getLogger(__name__)

router = APIRouter()


def get_settings() -> Settings:
    return settings


def get_ingestion_service(request: Request) -> IssueIngestionService:
    return IssueIngestionService(
        settings=settings,
        feishu_client=request.app.state.feishu_client,
        dedupe_store=request.app.state.dedupe_store,
    )


@router.post("/events")
async def receive_event(
    request: Request,
    background_tasks: BackgroundTasks,
    ingestion_service: IssueIngestionService = Depends(get_ingestion_service),
    app_settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    payload = await request.json()
    decrypted_payload = _decrypt_if_needed(payload, app_settings)

    if (decrypted_payload.get("type") or "").strip() == "url_verification":
        _validate_token(decrypted_payload, app_settings)
        return {"challenge": decrypted_payload.get("challenge", "")}

    _validate_token(decrypted_payload, app_settings)
    header = decrypted_payload.get("header") or {}
    if header.get("event_type") != "im.message.receive_v1":
        return {"code": 0, "msg": "ignored"}

    background_tasks.add_task(ingestion_service.process_event, decrypted_payload)
    return {"code": 0, "msg": "ok"}


def _decrypt_if_needed(payload: dict[str, Any], app_settings: Settings) -> dict[str, Any]:
    if "encrypt" in payload:
        logger.error("Encrypt Key is configured, but encrypted webhook payloads are not implemented")
        raise HTTPException(status_code=501, detail="Encrypted Feishu callbacks are not supported yet")
    return payload


def _validate_token(payload: dict[str, Any], app_settings: Settings) -> None:
    expected = app_settings.feishu_verification_token.strip()
    if not expected:
        return
    header = payload.get("header") or {}
    token = str(payload.get("token") or header.get("token") or "").strip()
    if token != expected:
        raise HTTPException(status_code=401, detail="Invalid verification token")
