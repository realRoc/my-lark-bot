"""Async Feishu API client for IM and Base operations."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from app.config import Settings
from app.domain.models import MessageResource


class FeishuAPIError(RuntimeError):
    """Raised when the Feishu API returns a non-zero code or bad status."""


class FeishuAPIClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.feishu_api_base,
            timeout=settings.feishu_request_timeout_seconds,
            trust_env=False,
        )
        self._tenant_access_token: str | None = None
        self._tenant_access_token_expires_at: float = 0.0
        self._base_field_names_by_id_cache: dict[tuple[str, str], dict[str, str]] = {}

    async def close(self) -> None:
        await self._client.aclose()

    async def create_issue_record(
        self,
        fields: dict[str, Any],
        *,
        app_token: str | None = None,
        table_id: str | None = None,
    ) -> str:
        target_app = app_token or self._settings.base_app_token
        target_table = table_id or self._settings.base_table_id
        payload = {"fields": await self._resolve_record_fields(fields, target_app, target_table)}
        data = await self._api_request(
            "POST",
            f"/open-apis/bitable/v1/apps/{target_app}/tables/{target_table}/records",
            json=payload,
        )
        record = data.get("record") or {}
        record_id = record.get("record_id") or record.get("id")
        if not record_id:
            raise FeishuAPIError("Missing record id in create record response")
        return str(record_id)

    async def upload_attachment(
        self,
        resource: MessageResource,
        content: bytes,
        filename: str,
        *,
        app_token: str | None = None,
    ) -> dict[str, str]:
        parent_type = "bitable_image" if resource.resource_type == "image" else "bitable_file"
        data = {
            "file_name": filename,
            "parent_type": parent_type,
            "parent_node": app_token or self._settings.base_app_token,
            "size": str(len(content)),
        }
        files = {
            "file": (filename, content, "application/octet-stream"),
        }
        response_data = await self._api_request(
            "POST",
            "/open-apis/drive/v1/medias/upload_all",
            data=data,
            files=files,
        )
        file_token = response_data.get("file_token")
        if not file_token:
            raise FeishuAPIError("Missing file_token in upload media response")
        return {"file_token": str(file_token), "name": filename}

    async def download_message_resource(self, message_id: str, resource: MessageResource) -> bytes:
        token = await self._get_tenant_access_token()
        response = await self._client.get(
            f"/open-apis/im/v1/messages/{message_id}/resources/{resource.key}",
            params={"type": resource.resource_type},
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        return response.content

    async def list_chat_messages(self, chat_id: str, page_size: int = 20) -> list[dict[str, Any]]:
        data = await self._api_request(
            "GET",
            "/open-apis/im/v1/messages",
            params={
                "card_msg_content_type": "raw_card_content",
                "container_id": chat_id,
                "container_id_type": "chat",
                "page_size": str(page_size),
                "sort_type": "ByCreateTimeDesc",
            },
        )
        items = data.get("items") or []
        return items if isinstance(items, list) else []

    async def reply_text(self, message_id: str, text: str) -> None:
        payload = {
            "content": json.dumps({"text": text}, ensure_ascii=False),
            "msg_type": "text",
        }
        await self._api_request(
            "POST",
            f"/open-apis/im/v1/messages/{message_id}/reply",
            json=payload,
        )

    async def _api_request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        token = await self._get_tenant_access_token()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        response = await self._client.request(method, path, headers=headers, **kwargs)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and data.get("code") not in (None, 0):
            raise FeishuAPIError(f"Feishu API error {data.get('code')}: {data.get('msg')}")
        return data.get("data", {}) if isinstance(data, dict) else {}

    async def _resolve_record_fields(
        self,
        fields: dict[str, Any],
        app_token: str,
        table_id: str,
    ) -> dict[str, Any]:
        field_names_by_id = await self._get_base_field_names_by_id(app_token, table_id)
        resolved_fields: dict[str, Any] = {}
        for field_id, value in fields.items():
            field_name = field_names_by_id.get(field_id)
            if not field_name:
                raise FeishuAPIError(f"Unknown Base field id: {field_id}")
            resolved_fields[field_name] = value
        return resolved_fields

    async def _get_base_field_names_by_id(
        self, app_token: str, table_id: str
    ) -> dict[str, str]:
        cache_key = (app_token, table_id)
        cached = self._base_field_names_by_id_cache.get(cache_key)
        if cached is not None:
            return cached

        data = await self._api_request(
            "GET",
            f"/open-apis/base/v3/bases/{app_token}/tables/{table_id}/fields",
            params={"page_size": "200"},
        )
        fields = data.get("fields") or []
        mapping: dict[str, str] = {}
        for field in fields:
            if not isinstance(field, dict):
                continue
            field_id = str(field.get("id") or "").strip()
            field_name = str(field.get("name") or "").strip()
            if field_id and field_name:
                mapping[field_id] = field_name
        if not mapping:
            raise FeishuAPIError("Failed to load Base field metadata")
        self._base_field_names_by_id_cache[cache_key] = mapping
        return mapping

    async def _get_tenant_access_token(self) -> str:
        now = time.time()
        if self._tenant_access_token and now < self._tenant_access_token_expires_at:
            return self._tenant_access_token

        response = await self._client.post(
            "/open-apis/auth/v3/tenant_access_token/internal",
            json={
                "app_id": self._settings.feishu_app_id,
                "app_secret": self._settings.feishu_app_secret,
            },
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise FeishuAPIError(
                f"Tenant access token error {payload.get('code')}: {payload.get('msg')}"
            )
        token = payload.get("tenant_access_token")
        expire = int(payload.get("expire", 7200))
        if not token:
            raise FeishuAPIError("Missing tenant_access_token")
        self._tenant_access_token = str(token)
        self._tenant_access_token_expires_at = now + expire - 60
        return self._tenant_access_token
