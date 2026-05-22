import asyncio
import json

import httpx

from app.config import Settings
from app.infra.feishu import FeishuAPIClient


def build_settings() -> Settings:
    return Settings(
        app_id="cli_xxx",
        app_secret="secret_xxx",
        base_app_token="app_token",
        base_table_id="tbl_token",
        base_description_field_id="fld_description",
        base_status_field_id="fld_status",
        base_source_field_id="fld_source",
        base_surface_field_id="fld_surface",
    )


def test_create_issue_record_resolves_field_ids_to_names():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/open-apis/auth/v3/tenant_access_token/internal":
            return httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": "tenant_token", "expire": 7200},
            )
        if request.url.path == "/open-apis/base/v3/bases/app_token/tables/tbl_token/fields":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "fields": [
                            {"id": "fld_description", "name": "问题描述"},
                            {"id": "fld_status", "name": "状态"},
                            {"id": "fld_source", "name": "问题来源"},
                        ]
                    },
                },
            )
        if request.url.path == "/open-apis/bitable/v1/apps/app_token/tables/tbl_token/records":
            return httpx.Response(
                200,
                json={"code": 0, "data": {"record": {"record_id": "rec_test"}}},
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    async def scenario() -> None:
        client = FeishuAPIClient(build_settings())
        client._client = httpx.AsyncClient(
            base_url=client._settings.feishu_api_base,
            timeout=client._settings.feishu_request_timeout_seconds,
            trust_env=False,
            transport=httpx.MockTransport(handler),
        )
        try:
            record_id = await client.create_issue_record(
                {
                    "fld_description": "字段ID验证",
                    "fld_status": "待处理",
                    "fld_source": "内部反馈",
                }
            )
        finally:
            await client.close()

        assert record_id == "rec_test"

    asyncio.run(scenario())

    create_request = requests[-1]
    assert create_request.method == "POST"
    assert create_request.url.path == "/open-apis/bitable/v1/apps/app_token/tables/tbl_token/records"
    assert json.loads(create_request.read().decode("utf-8")) == {
        "fields": {"问题描述": "字段ID验证", "状态": "待处理", "问题来源": "内部反馈"}
    }
