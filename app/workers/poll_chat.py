"""Poll allowed Feishu chats and ingest bot-mentioned bug reports."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from app.config import settings
from app.domain.service import IssueIngestionService
from app.infra.dedupe import MessageDedupeStore
from app.infra.feishu import FeishuAPIClient

logger = logging.getLogger(__name__)


class JsonSeenStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.message_ids = self._load()

    def _load(self) -> set[str]:
        if not self.path.exists():
            return set()
        try:
            payload = json.loads(self.path.read_text())
        except json.JSONDecodeError:
            return set()
        values = payload.get("message_ids", []) if isinstance(payload, dict) else []
        return {str(value) for value in values}

    def contains(self, message_id: str) -> bool:
        return message_id in self.message_ids

    def add(self, message_id: str) -> None:
        self.message_ids.add(message_id)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"message_ids": sorted(self.message_ids)}
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--page-size", type=int, default=20)
    parser.add_argument("--state-file", default=".state/poll_seen.json")
    parser.add_argument("--process-existing", action="store_true")
    parser.add_argument("--reset-seen-on-start", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    if not settings.allowed_chat_ids:
        raise SystemExit("TEAMO_FEISHU_ALLOWED_CHAT_IDS is required")

    client = FeishuAPIClient(settings)
    service = IssueIngestionService(
        settings=settings,
        feishu_client=client,
        dedupe_store=MessageDedupeStore(settings.feishu_dedupe_ttl_seconds),
    )
    seen = JsonSeenStore(Path(args.state_file))

    try:
        if args.reset_seen_on_start:
            seen.message_ids.clear()

        if not args.process_existing and not seen.message_ids:
            await seed_existing(client, seen, args.page_size)
            seen.save()
            logger.info("Seeded existing messages; waiting for new bug reports")

        while True:
            try:
                await poll_once(client, service, seen, args.page_size)
                seen.save()
            except Exception:
                logger.exception("Poll iteration failed; worker will continue")
            await asyncio.sleep(args.interval)
    finally:
        await client.close()


async def seed_existing(client: FeishuAPIClient, seen: JsonSeenStore, page_size: int) -> None:
    for chat_id in settings.allowed_chat_ids:
        for message in await client.list_chat_messages(chat_id, page_size=page_size):
            message_id = str(message.get("message_id", ""))
            if message_id:
                seen.add(message_id)


async def poll_once(
    client: FeishuAPIClient,
    service: IssueIngestionService,
    seen: JsonSeenStore,
    page_size: int,
) -> None:
    for chat_id in settings.allowed_chat_ids:
        messages = await client.list_chat_messages(chat_id, page_size=page_size)
        for message in reversed(messages):
            message_id = str(message.get("message_id", ""))
            if not message_id or seen.contains(message_id):
                continue
            seen.add(message_id)
            if not should_ingest(message):
                continue
            logger.info("Ingesting message %s", message_id)
            await service.process_event(to_event_payload(message))


def should_ingest(message: dict[str, Any]) -> bool:
    if message.get("deleted"):
        return False
    if (message.get("sender") or {}).get("sender_type") != "user":
        return False
    mentions = message.get("mentions") or []
    bot_names = settings.feishu_bot_names
    mentions_bot = any(str(mention.get("name", "")).strip() in bot_names for mention in mentions)
    if not mentions_bot:
        return False
    return True


def to_event_payload(message: dict[str, Any]) -> dict[str, Any]:
    sender = message.get("sender") or {}
    return {
        "header": {"event_type": "im.message.receive_v1"},
        "event": {
            "sender": {
                "sender_type": sender.get("sender_type"),
                "sender_id": {"open_id": sender.get("id")},
            },
            "message": {
                "message_id": message.get("message_id"),
                "chat_id": message.get("chat_id"),
                "message_type": message.get("msg_type"),
                "content": (message.get("body") or {}).get("content") or "",
                "mentions": message.get("mentions") or [],
            },
        },
    }


if __name__ == "__main__":
    asyncio.run(main())
