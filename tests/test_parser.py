import json

from app.domain.parser import parse_issue_from_message


def test_parse_text_message_extracts_priority_and_module():
    issue = parse_issue_from_message(
        message_type="text",
        raw_content=json.dumps({"text": "@_user_1 P0 codex web 页面崩了"}),
        mentions=[{"key": "@_user_1"}],
        module_aliases={"codex": ("codex",)},
        allowed_priorities={"P0", "P1", "P2", "P3"},
        allowed_surfaces=("web", "cli", "server"),
    )

    assert issue.description == "P0 codex web 页面崩了"
    assert issue.priority == "P0"
    assert issue.modules == ["codex"]
    assert issue.surfaces == ["web"]
    assert issue.resources == []


def test_parse_post_message_extracts_images_and_text():
    raw_content = json.dumps(
        {
            "zh_cn": {
                "title": "ignored",
                "content": [
                    [
                        {"tag": "at", "user_id": "ou_bot"},
                        {"tag": "text", "text": "claude code 这里有个 P2 问题"},
                    ],
                    [
                        {"tag": "img", "image_key": "img_123"},
                    ],
                ],
            }
        },
        ensure_ascii=False,
    )

    issue = parse_issue_from_message(
        message_type="post",
        raw_content=raw_content,
        mentions=[{"key": "@_user_1"}],
        module_aliases={"claude code": ("claude code",)},
        allowed_priorities={"P0", "P1", "P2", "P3"},
        allowed_surfaces=("web", "cli", "server"),
    )

    assert issue.description == "claude code 这里有个 P2 问题"
    assert issue.priority == "P2"
    assert issue.modules == ["claude code"]
    assert issue.surfaces == []
    assert len(issue.resources) == 1
    assert issue.resources[0].key == "img_123"
    assert issue.resources[0].resource_type == "image"


def test_parse_raw_post_message_from_im_api():
    raw_content = json.dumps(
        {
            "title": "",
            "content": [
                [{"tag": "img", "image_key": "img_raw"}],
                [
                    {"tag": "at", "user_id": "@_user_1", "user_name": "teamo code 工单"},
                    {"tag": "text", "text": " +bug：codex mode thinking 超时"},
                ],
            ],
        },
        ensure_ascii=False,
    )

    issue = parse_issue_from_message(
        message_type="post",
        raw_content=raw_content,
        mentions=[{"key": "@_user_1"}],
        module_aliases={"codex": ("codex",)},
        allowed_priorities={"P0", "P1", "P2", "P3"},
        allowed_surfaces=("web", "cli", "server"),
    )

    assert issue.description == "+bug：codex mode thinking 超时"
    assert issue.priority is None
    assert issue.modules == ["codex"]
    assert issue.surfaces == []
    assert issue.resources[0].key == "img_raw"


def test_parse_text_message_extracts_multiple_surfaces():
    issue = parse_issue_from_message(
        message_type="text",
        raw_content=json.dumps({"text": "@_user_1 cli 和 server 都有问题"}),
        mentions=[{"key": "@_user_1"}],
        module_aliases={},
        allowed_priorities={"P0", "P1", "P2", "P3"},
        allowed_surfaces=("web", "cli", "server"),
    )

    assert issue.description == "cli 和 server 都有问题"
    assert issue.surfaces == ["cli", "server"]
