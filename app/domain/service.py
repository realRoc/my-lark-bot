"""Business orchestration for Feishu events to Base issue records."""

from __future__ import annotations

import logging
import mimetypes
from typing import Any

from app.config import Settings
from app.domain.models import MessageResource, ParsedIssue
from app.domain.parser import parse_issue_from_message
from app.infra.dedupe import MessageDedupeStore
from app.infra.feishu import FeishuAPIClient, FeishuAPIError

logger = logging.getLogger(__name__)


class IssueIngestionService:
    def __init__(
        self,
        *,
        settings: Settings,
        feishu_client: FeishuAPIClient,
        dedupe_store: MessageDedupeStore,
    ) -> None:
        self._settings = settings
        self._feishu_client = feishu_client
        self._dedupe_store = dedupe_store

    async def process_event(self, payload: dict[str, Any]) -> None:
        event = payload.get("event") or {}
        sender = event.get("sender") or {}
        message = event.get("message") or {}
        message_id = str(message.get("message_id", "")).strip()
        if not message_id:
            logger.warning("Skipping event without message_id")
            return

        if sender.get("sender_type") != "user":
            logger.info("Skipping non-user message: %s", message_id)
            return

        if not await self._dedupe_store.mark_if_new(message_id):
            logger.info("Skipping duplicate delivery for message %s", message_id)
            return

        chat_id = str(message.get("chat_id", "")).strip()
        if self._settings.allowed_chat_ids and chat_id not in self._settings.allowed_chat_ids:
            logger.info("Skipping chat %s because it is not allowlisted", chat_id)
            return

        issue = parse_issue_from_message(
            message_type=str(message.get("message_type", "")).strip(),
            raw_content=str(message.get("content", "") or ""),
            mentions=message.get("mentions") or [],
            module_aliases=self._settings.module_aliases,
            allowed_priorities=self._settings.allowed_priorities,
            allowed_surfaces=self._settings.resolve_surfaces(chat_id),
        )

        if not issue.description:
            await self._reply_if_enabled(
                message_id,
                "未识别到问题描述。请在 @机器人 后附上问题说明；优先级如有可直接写 P0/P1/P2/P3。",
            )
            return

        app_token, table_id = self._settings.resolve_base_target(chat_id)
        message_link = self._build_message_link(chat_id, message_id)

        try:
            attachments = await self._upload_resources(
                message_id, issue.resources, app_token=app_token
            )
            fields = self._build_fields(
                issue=issue,
                sender=sender,
                attachments=attachments,
                message_link=message_link,
            )
            record_id = await self._feishu_client.create_issue_record(
                fields, app_token=app_token, table_id=table_id
            )
        except FeishuAPIError:
            logger.exception("Feishu API call failed for message %s", message_id)
            await self._reply_if_enabled(message_id, "收到问题了，但落表失败，请稍后重试。")
            return
        except Exception:
            logger.exception("Unexpected failure while processing message %s", message_id)
            await self._reply_if_enabled(message_id, "收到问题了，但处理失败，请联系管理员排查。")
            return

        await self._reply_if_enabled(message_id, f"已记录到 Bug 工单表，记录 ID: {record_id}")

    def _build_fields(
        self,
        *,
        issue: ParsedIssue,
        sender: dict[str, Any],
        attachments: list[dict[str, str]],
        message_link: str,
    ) -> dict[str, Any]:
        sender_id = (((sender.get("sender_id") or {}).get("open_id")) or "").strip()
        # 识别到模块 → 状态置「待同步」（可直接交给 sync 脚本）
        # 没识别到 → 状态置「待分类」（等人工选模块后改为「待同步」）
        status = "待同步" if issue.modules else self._settings.feishu_default_status
        fields: dict[str, Any] = {
            self._settings.base_description_field_id: issue.description,
            self._settings.base_status_field_id: status,
            self._settings.base_source_field_id: self._settings.feishu_default_source,
        }
        if sender_id:
            fields[self._settings.base_reporter_field_id] = [{"id": sender_id}]
        if self._settings.feishu_default_assignee_id:
            fields[self._settings.base_assignee_field_id] = [
                {"id": self._settings.feishu_default_assignee_id}
            ]
        if issue.priority:
            fields[self._settings.base_priority_field_id] = issue.priority
        if issue.modules:
            fields[self._settings.base_module_field_id] = issue.modules
        if attachments:
            fields[self._settings.base_attachment_field_id] = attachments
        if message_link and self._settings.base_message_link_field_id:
            # URL style field 要求 {link, text} 对象,不接受纯字符串
            fields[self._settings.base_message_link_field_id] = {
                "link": message_link,
                "text": "查看原消息",
            }
        return fields

    @staticmethod
    def _build_message_link(chat_id: str, message_id: str) -> str:
        if not (chat_id and message_id):
            return ""
        # Lark applink: 在飞书客户端中打开对应群,messageId 用于尝试定位到消息
        return (
            f"https://applink.feishu.cn/client/chat/open?openChat=true"
            f"&chatId={chat_id}&messageId={message_id}"
        )

    async def _upload_resources(
        self,
        message_id: str,
        resources: list[MessageResource],
        *,
        app_token: str | None = None,
    ) -> list[dict[str, str]]:
        attachments: list[dict[str, str]] = []
        for index, resource in enumerate(resources, start=1):
            content = await self._feishu_client.download_message_resource(message_id, resource)
            filename = self._choose_filename(resource, content, index)
            uploaded = await self._feishu_client.upload_attachment(
                resource, content, filename, app_token=app_token
            )
            attachments.append(uploaded)
        return attachments

    async def _reply_if_enabled(self, message_id: str, text: str) -> None:
        if not self._settings.feishu_reply_enabled:
            return
        try:
            await self._feishu_client.reply_text(message_id, text)
        except Exception:
            logger.exception("Failed to reply to message %s", message_id)

    def _choose_filename(self, resource: MessageResource, content: bytes, index: int) -> str:
        original = resource.name.strip()
        if original and "." in original:
            return original
        extension = mimetypes.guess_extension("image/png" if resource.resource_type == "image" else "application/octet-stream") or ""
        prefix = "screenshot" if resource.resource_type == "image" else "attachment"
        return f"{prefix}-{index}{extension}"
