#!/usr/bin/env python3
"""Schema-as-code: 用 lark-cli 一次性创建工单表的全部字段。

前置:
  1. 已经在飞书 UI 里手动建了一个空的 Bitable 文件
  2. 从 Bitable URL 拿到 app_token (Wiki 下挂的先用 lark-cli wiki +node-get 解析)
  3. lark-cli 已 auth (当前用户对该 Bitable 有可编辑权限)

用法:
  python3 scripts/setup_bitable.py <APP_TOKEN> [--table-name 工单表] [--as user|bot]
  python3 scripts/setup_bitable.py <APP_TOKEN> --table-id <existing_table_id>

结束后会打印 table_id 和每个字段的 field_id, 复制到 .env.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys


# 字段定义: (展示名, 完整 json payload 不含 name, 对应的 .env key)
# 参考: lark-base-shortcut-field-properties.md
FIELDS = [
    # --- bot 自动填 ---
    ("问题描述",          {"type": "text"},                                                  "BASE_DESCRIPTION_FIELD_ID"),
    ("提出者",            {"type": "user", "multiple": False},                               "BASE_REPORTER_FIELD_ID"),
    ("提出人 open_id",    {"type": "text"},                                                  "BASE_REPORTER_OPEN_ID_FIELD_ID"),
    ("群名",              {"type": "text"},                                                  "BASE_CHAT_NAME_FIELD_ID"),
    ("飞书消息 link",     {"type": "text", "style": {"type": "url"}},                        "BASE_MESSAGE_LINK_FIELD_ID"),
    ("截图视频",          {"type": "attachment"},                                            "BASE_ATTACHMENT_FIELD_ID"),
    ("优先级",            {"type": "select", "multiple": False,
                           "options": [{"name": x} for x in ("P0", "P1", "P2", "P3")]},     "BASE_PRIORITY_FIELD_ID"),
    ("所属模块",          {"type": "select", "multiple": True,
                           "options": [{"name": x} for x in ("模块A", "模块B")]},           "BASE_MODULE_FIELD_ID"),
    ("前后端",            {"type": "select", "multiple": True,
                           "options": [{"name": x} for x in ("web", "cli", "server")]},     "BASE_SURFACE_FIELD_ID"),
    ("问题来源",          {"type": "select", "multiple": False,
                           "options": [{"name": x} for x in ("内部反馈", "外部反馈")]},     "BASE_SOURCE_FIELD_ID"),
    # --- 人工分解时填 ---
    ("状态",              {"type": "select", "multiple": False,
                           "options": [{"name": x, "hue": h, "lightness": l} for x, h, l in (
                               ("待分解", "Blue",   "Lighter"),
                               ("已分解", "Yellow", "Light"),
                               ("已同步", "Green",  "Light"),
                               ("已关闭", "Gray",   "Lighter"),
                           )]},                                                              "BASE_STATUS_FIELD_ID"),
    ("目标 Repo",         {"type": "text"},                                                  "BASE_TARGET_REPO_FIELD_ID"),
    ("子任务",            {"type": "text"},                                                  "BASE_SUBTASKS_FIELD_ID"),
    ("分解人",            {"type": "user", "multiple": False},                               "BASE_BREAKDOWN_BY_FIELD_ID"),
    # --- 同步脚本回写 ---
    ("GitHub Issue URLs", {"type": "text"},                                                  "BASE_GITHUB_ISSUES_FIELD_ID"),
    # --- 兼容老 schema ---
    ("负责人",            {"type": "user", "multiple": False},                               "BASE_ASSIGNEE_FIELD_ID"),
]


def run_lark(args: list[str]) -> dict:
    print(f"$ {' '.join(args)}", file=sys.stderr)
    proc = subprocess.run(args, capture_output=True, text=True)
    out = proc.stdout
    brace = out.find("{")
    if brace < 0:
        print(f"ERROR: lark-cli stdout 没有 JSON\nstderr:\n{proc.stderr}\nstdout:\n{out}", file=sys.stderr)
        sys.exit(1)
    try:
        resp = json.loads(out[brace:])
    except json.JSONDecodeError as e:
        print(f"ERROR: JSON 解析失败: {e}\n原始输出:\n{out}", file=sys.stderr)
        sys.exit(1)
    if not resp.get("ok", proc.returncode == 0):
        print(f"ERROR: lark-cli 返回 ok=false:\n{json.dumps(resp, ensure_ascii=False, indent=2)}", file=sys.stderr)
        sys.exit(1)
    if proc.returncode != 0:
        print(f"ERROR: lark-cli exit {proc.returncode}\nstderr:\n{proc.stderr}", file=sys.stderr)
        sys.exit(1)
    return resp


def create_table(app_token: str, name: str, identity: str) -> str:
    resp = run_lark([
        "lark-cli", "base", "+table-create",
        "--base-token", app_token,
        "--name", name,
        "--as", identity,
    ])
    data = resp.get("data") or {}
    table_id = (data.get("table") or {}).get("id") or data.get("table_id")
    if not table_id:
        print(f"ERROR: 没拿到 table_id, 完整响应:\n{json.dumps(resp, ensure_ascii=False, indent=2)}", file=sys.stderr)
        sys.exit(1)
    return table_id


def create_field(app_token: str, table_id: str, name: str, payload: dict, identity: str) -> str:
    body = dict(payload)
    body["name"] = name
    resp = run_lark([
        "lark-cli", "base", "+field-create",
        "--base-token", app_token,
        "--table-id", table_id,
        "--json", json.dumps(body, ensure_ascii=False),
        "--as", identity,
    ])
    data = resp.get("data") or {}
    field = data.get("field") or {}
    field_id = field.get("field_id") or field.get("id") or data.get("field_id")
    if not field_id:
        print(f"ERROR: 字段「{name}」没拿到 field_id, 完整响应:\n{json.dumps(resp, ensure_ascii=False, indent=2)}", file=sys.stderr)
        sys.exit(1)
    return field_id


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("app_token", help="Bitable app_token (Wiki 下挂的请先用 lark-cli wiki +node-get 解出 obj_token)")
    ap.add_argument("--table-name", default="工单表")
    ap.add_argument("--table-id", default=None, help="复用已存在的 table_id, 跳过 +table-create")
    ap.add_argument("--as", dest="identity", default="user", choices=["user", "bot"])
    ap.add_argument("--skip", action="append", default=[], help="跳过特定字段名 (可多次, 用于断点续跑)")
    args = ap.parse_args()

    if args.table_id:
        print(f"==> 复用 table {args.table_id} (跳过 +table-create)")
        table_id = args.table_id
    else:
        print(f"==> 在 base {args.app_token} 创建表「{args.table_name}」")
        table_id = create_table(args.app_token, args.table_name, args.identity)
        print(f"    table_id = {table_id}")

    skip_set = set(args.skip)
    results: list[tuple[str, str, str]] = []
    for name, payload, env_key in FIELDS:
        if name in skip_set:
            print(f"==> 跳过字段「{name}」(--skip)")
            continue
        print(f"==> 创建字段「{name}」({payload.get('type')})")
        field_id = create_field(args.app_token, table_id, name, payload, args.identity)
        print(f"    field_id = {field_id}")
        results.append((name, env_key, field_id))

    print()
    print("=" * 72)
    print("建表完成。把下面这一坨粘到 .env：")
    print("=" * 72)
    print()
    print(f"TEAMO_FEISHU_BASE_APP_TOKEN={args.app_token}")
    print(f"TEAMO_FEISHU_BASE_TABLE_ID={table_id}")
    print()
    for _, env_key, field_id in results:
        print(f"TEAMO_FEISHU_{env_key}={field_id}")
    print()


if __name__ == "__main__":
    main()
