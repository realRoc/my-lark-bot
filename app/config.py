"""Application settings for the Feishu bug bot service."""

from __future__ import annotations

import json
from functools import cached_property

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_MODULE_ALIASES = {
    "codex": ["codex"],
    "claude code": ["claude code"],
    "登陆注册": ["登陆注册", "登录注册"],
    "商业化": ["商业化"],
    "请求调用": ["请求调用"],
    "安装": ["安装"],
    "领券": ["领券"],
    "技术需求": ["技术需求"],
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TEAMO_FEISHU_",
        env_file=".env",
        extra="ignore",
    )

    service_name: str = "teamo-feishu-bugbot"
    debug: bool = False

    app_id: str = ""
    app_secret: str = ""
    verification_token: str = ""
    encrypt_key: str = ""
    api_base: str = "https://open.feishu.cn"
    allowed_chat_ids_raw: str = Field(default="", validation_alias="TEAMO_FEISHU_ALLOWED_CHAT_IDS")
    reply_enabled: bool = True
    default_assignee_id: str = ""
    default_source: str = "内部反馈"
    default_status: str = "待处理"
    bot_names: str = "teamo code 工单"
    issue_markers: str = "+bug,bug"
    priority_values: str = "P0,P1,P2,P3"
    surface_values: str = "web,cli,server"
    module_aliases_json: str = Field(
        default_factory=lambda: json.dumps(DEFAULT_MODULE_ALIASES, ensure_ascii=False)
    )
    request_timeout_seconds: float = 15.0
    dedupe_ttl_seconds: int = 600

    base_app_token: str = ""
    base_table_id: str = ""
    chat_routes_json: str = ""
    base_description_field_id: str = ""
    base_attachment_field_id: str = ""
    base_priority_field_id: str = ""
    base_status_field_id: str = ""
    base_module_field_id: str = ""
    base_assignee_field_id: str = ""
    base_reporter_field_id: str = ""
    base_source_field_id: str = ""
    base_message_link_field_id: str = ""
    base_github_issues_field_id: str = ""

    # 模块 → repo 映射（sync 脚本用; format: {"模块名": "owner/repo"}）
    module_repo_map_json: str = ""

    @computed_field
    @property
    def feishu_app_id(self) -> str:
        return self.app_id

    @computed_field
    @property
    def feishu_app_secret(self) -> str:
        return self.app_secret

    @computed_field
    @property
    def feishu_verification_token(self) -> str:
        return self.verification_token

    @computed_field
    @property
    def feishu_encrypt_key(self) -> str:
        return self.encrypt_key

    @computed_field
    @property
    def feishu_api_base(self) -> str:
        return self.api_base

    @computed_field
    @property
    def feishu_reply_enabled(self) -> bool:
        return self.reply_enabled

    @computed_field
    @property
    def feishu_default_assignee_id(self) -> str:
        return self.default_assignee_id

    @computed_field
    @property
    def feishu_default_source(self) -> str:
        return self.default_source

    @computed_field
    @property
    def feishu_default_status(self) -> str:
        return self.default_status

    @computed_field
    @property
    def feishu_bot_names(self) -> set[str]:
        return {item.strip() for item in self.bot_names.split(",") if item.strip()}

    @computed_field
    @property
    def feishu_issue_markers(self) -> tuple[str, ...]:
        return tuple(item.strip().lower() for item in self.issue_markers.split(",") if item.strip())

    @computed_field
    @property
    def feishu_request_timeout_seconds(self) -> float:
        return self.request_timeout_seconds

    @computed_field
    @property
    def feishu_dedupe_ttl_seconds(self) -> int:
        return self.dedupe_ttl_seconds

    @cached_property
    def allowed_chat_ids(self) -> set[str]:
        return {item.strip() for item in self.allowed_chat_ids_raw.split(",") if item.strip()}

    @cached_property
    def allowed_priorities(self) -> set[str]:
        return {item.strip().upper() for item in self.priority_values.split(",") if item.strip()}

    @cached_property
    def allowed_surfaces(self) -> tuple[str, ...]:
        return tuple(item.strip().lower() for item in self.surface_values.split(",") if item.strip())

    @cached_property
    def chat_routes(self) -> dict[str, dict]:
        if not self.chat_routes_json.strip():
            return {}
        parsed = json.loads(self.chat_routes_json)
        routes: dict[str, dict] = {}
        for chat_id, target in parsed.items():
            if isinstance(target, dict):
                routes[str(chat_id).strip()] = target
        return routes

    def resolve_base_target(self, chat_id: str) -> tuple[str, str]:
        target = self.chat_routes.get(chat_id) or {}
        app_token = str(target.get("app_token") or "").strip() or self.base_app_token
        table_id = str(target.get("table_id") or "").strip() or self.base_table_id
        return app_token, table_id

    def resolve_surfaces(self, chat_id: str) -> tuple[str, ...]:
        target = self.chat_routes.get(chat_id) or {}
        overrides = target.get("surfaces")
        if isinstance(overrides, list) and overrides:
            return tuple(str(v).strip().lower() for v in overrides if str(v).strip())
        return self.allowed_surfaces

    @cached_property
    def module_repo_map(self) -> dict[str, str]:
        if not self.module_repo_map_json.strip():
            return {}
        parsed = json.loads(self.module_repo_map_json)
        return {
            str(k).strip(): str(v).strip()
            for k, v in parsed.items()
            if str(k).strip() and str(v).strip()
        }

    @cached_property
    def module_aliases(self) -> dict[str, tuple[str, ...]]:
        if not self.module_aliases_json.strip():
            return {}
        parsed = json.loads(self.module_aliases_json)
        aliases: dict[str, tuple[str, ...]] = {}
        for module_name, values in parsed.items():
            normalized = [str(module_name).strip()]
            normalized.extend(str(value).strip() for value in values or [])
            aliases[str(module_name).strip()] = tuple(
                value.lower() for value in normalized if value.strip()
            )
        return aliases


settings = Settings()
