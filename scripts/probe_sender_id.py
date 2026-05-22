"""一次性诊断: 看 bot 视角下消息的 sender open_id 真实是什么."""
import asyncio
import json
import sys

sys.path.insert(0, ".")
from app.config import settings
from app.infra.feishu import FeishuAPIClient


async def main():
    client = FeishuAPIClient(settings)
    try:
        # 列群里最近 3 条消息
        chat_id = next(iter(settings.allowed_chat_ids))
        messages = await client.list_chat_messages(chat_id, page_size=3)
        for m in messages:
            print(json.dumps({
                "message_id": m.get("message_id"),
                "msg_type": m.get("msg_type"),
                "sender": m.get("sender"),
                "mentions": m.get("mentions"),
                "body_preview": str((m.get("body") or {}).get("content", ""))[:120],
            }, ensure_ascii=False, indent=2))
            print("---")
    finally:
        await client.close()


asyncio.run(main())
