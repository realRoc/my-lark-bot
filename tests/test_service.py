from app.config import Settings
from app.domain.models import ParsedIssue
from app.domain.service import IssueIngestionService
from app.infra.dedupe import MessageDedupeStore


class StubFeishuClient:
    def __init__(self):
        self.created_fields = None
        self.replies = []

    async def create_issue_record(self, fields, *, app_token=None, table_id=None):
        self.created_fields = fields
        return "rec_test"

    async def download_message_resource(self, message_id, resource):
        raise AssertionError("no resource downloads expected")

    async def upload_attachment(self, resource, content, filename, *, app_token=None):
        raise AssertionError("no uploads expected")

    async def reply_text(self, message_id, text):
        self.replies.append((message_id, text))


def build_settings(**overrides) -> Settings:
    base = dict(
        reply_enabled=False,
        default_assignee_id="ou_assignee",
        default_status="待分类",
        base_app_token="app_token",
        base_table_id="tbl_token",
        base_description_field_id="fld_description",
        base_status_field_id="fld_status",
        base_source_field_id="fld_source",
        base_priority_field_id="fld_priority",
        base_module_field_id="fld_module",
        base_reporter_field_id="fld_reporter",
        base_assignee_field_id="fld_assignee",
        base_attachment_field_id="fld_attachment",
        base_message_link_field_id="fld_message_link",
    )
    base.update(overrides)
    return Settings(**base)


def test_build_fields_with_module_sets_status_pending_sync():
    """识别到模块 → 状态置「待同步」（无需人工分类）。"""
    service = IssueIngestionService(
        settings=build_settings(),
        feishu_client=StubFeishuClient(),
        dedupe_store=MessageDedupeStore(600),
    )
    fields = service._build_fields(
        issue=ParsedIssue(
            description="P1 dashboard 登录跳错页",
            priority="P1",
            modules=["dashboard"],
            surfaces=[],
            resources=[],
        ),
        sender={"sender_id": {"open_id": "ou_reporter"}},
        attachments=[],
        message_link="https://applink.feishu.cn/client/chat/open?openChat=true&chatId=oc_abc&messageId=om_def",
    )

    assert fields["fld_description"] == "P1 dashboard 登录跳错页"
    assert fields["fld_status"] == "待同步"
    assert fields["fld_source"] == "内部反馈"
    assert fields["fld_priority"] == "P1"
    assert fields["fld_module"] == ["dashboard"]
    assert fields["fld_reporter"] == [{"id": "ou_reporter"}]
    assert fields["fld_assignee"] == [{"id": "ou_assignee"}]
    assert fields["fld_message_link"]["link"].startswith("https://applink.feishu.cn/")
    assert "fld_surface" not in fields  # 前后端字段已废弃
    # 前后端 / 群名 / open_id 字段 不再写入
    assert "fld_reporter_open_id" not in fields
    assert "fld_chat_name" not in fields


def test_build_fields_without_module_keeps_default_status():
    """识别不到模块 → 状态保持默认「待分类」，等人工选模块。"""
    service = IssueIngestionService(
        settings=build_settings(),
        feishu_client=StubFeishuClient(),
        dedupe_store=MessageDedupeStore(600),
    )
    fields = service._build_fields(
        issue=ParsedIssue(
            description="不知道哪个模块的问题",
            priority=None,
            modules=[],
            surfaces=[],
            resources=[],
        ),
        sender={"sender_id": {"open_id": "ou_reporter"}},
        attachments=[],
        message_link="",
    )

    assert fields["fld_status"] == "待分类"
    assert "fld_module" not in fields
    assert "fld_priority" not in fields
    assert "fld_message_link" not in fields  # 空 link 不写
