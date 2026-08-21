import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.cookiejar import CookieJar
from datetime import datetime, timedelta, timezone


BEIJING = timezone(timedelta(hours=8))
AMA_THRESHOLD = 0.2
ROUTER_SUCCESS_THRESHOLD = 98.0
DISCOUNT_MIN_REQUESTS = 30
DISCOUNT_WINDOW_MINUTES = 30
DISCOUNT_EXEMPT_MODELS = {"gpt-image-2"}
DISCOUNT_THRESHOLDS = {
    "gpt": 0.15,
    "claude": 0.30,
    "gemini": 0.30,
}
DISCOUNT_MODEL_THRESHOLDS = {
    "gpt-5.6-luna": 0.40,
}
AMA_DASHBOARD_URL = "http://43.159.11.241:3000/"
ROUTER_DISCOUNT_DASHBOARD_URL = "https://op.teamocode.com/discount-rate"
ROUTER_SUCCESS_DASHBOARD_URL = "https://op.teamocode.com/request-status"
WU_YUPENG = "ou_98e6339957c6b8ff83ae7afbc72a1ee0"
LI_SHUFAN = "ou_0e3da4c3e50d087359cae8a4de4f9024"
SHE_RUIXU = "ou_9b234971cb8284e4a67065a9fab75c45"
YUAN_MING = "ou_623908c2e415b292bd554a1d470d6a2b"
YANG_ZIZHAO = "ou_12838894a23e187f1a4e44c08c7a464b"
CHANG_KEJIE = "ou_a9a32ff10ab6b1927a201a1ddf127a6b"
WEEKDAY_DUTY = {
    9: SHE_RUIXU, 10: SHE_RUIXU,
    11: YUAN_MING, 12: YUAN_MING, 13: YUAN_MING, 14: YUAN_MING,
    15: YANG_ZIZHAO, 16: YANG_ZIZHAO, 17: YANG_ZIZHAO, 18: YANG_ZIZHAO,
    19: CHANG_KEJIE, 20: CHANG_KEJIE, 21: CHANG_KEJIE,
    22: WU_YUPENG, 23: WU_YUPENG,
}
TTFT_THRESHOLD_SECONDS = 10.0
TTFT_MODELS = (
    "gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5", "gpt-5-mini",
    "openai-search", "gpt-5.4-nano", "gpt-5.1", "gpt-5.2",
    "gemini-3.5-flash", "gemini-3.6-flash",
)
IMAGE_QUERY = "a cat"
IMAGE_MODELS = (
    {"label": "Nano Banana 2", "engine": "gemini-3.1-flash-image-preview", "threshold": 120},
    {"label": "GPT-Image-2", "engine": "gpt-image-2", "threshold": 180},
)


def _request_json(url, *, headers=None, body=None, timeout=55):
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers or {})
    if data is not None:
        request.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read(1000).decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc


def _ama(now):
    start = now - timedelta(hours=1)
    query = urllib.parse.urlencode({
        "from_dt": start.isoformat(),
        "to_dt": now.isoformat(),
        "message_limit": 5,
        "event_limit": 5,
    })
    payload = _request_json(
        f"{os.environ['AMA_BASE_URL'].rstrip('/')}/api/qr-error/dashboard?{query}",
        headers={"x-api-key": os.environ["AMA_API_KEY"]},
    )
    summary = payload.get("summary") or {}
    metadata = payload.get("metadata") or {}
    rate = float(summary.get("alert_rate") or 0)
    coverage_warning = bool(metadata.get("coverage_warning"))
    return {
        "alert": rate >= AMA_THRESHOLD,
        "rate": rate,
        "errors": int(summary.get("total") or 0),
        "requests": int(summary.get("total_requests") or 0),
        "coverage_warning": coverage_warning,
    }


def _router_headers():
    raw = f"{os.environ['ROUTER_USERNAME']}:{os.environ['ROUTER_PASSWORD']}".encode()
    return {"authorization": "Basic " + base64.b64encode(raw).decode()}


def _router(path, params=None):
    base = os.environ["ROUTER_BASE_URL"].rstrip("/")
    query = "" if not params else "?" + urllib.parse.urlencode(params)
    return _request_json(f"{base}/admin/metrics/{path}{query}", headers=_router_headers())


def _discount_family(model):
    value = str(model or "").strip().lower()
    if value.startswith("claude"):
        return "claude"
    if value.startswith("gemini"):
        return "gemini"
    if value.startswith(("gpt", "chatgpt", "o1", "o3", "o4", "codex-")):
        return "gpt"
    return None


def _discount():
    end = datetime.now(BEIJING)
    start = end - timedelta(minutes=DISCOUNT_WINDOW_MINUTES)
    payload = _router("discount-rate", {
        "start": start.strftime("%Y-%m-%d"),
        "end": end.strftime("%Y-%m-%d"),
        "start_ts": start.isoformat(timespec="seconds"),
        "end_ts": end.isoformat(timespec="seconds"),
    })
    rows = []
    for item in payload.get("models") or []:
        model = str(item.get("model") or "")
        normalized_model = model.strip().lower()
        family = _discount_family(model)
        if not family:
            continue
        requests = int(item.get("reqs") or 0)
        rate = float(item.get("avg_effective_provider_discount_rate") or 0)
        threshold = DISCOUNT_MODEL_THRESHOLDS.get(
            normalized_model,
            DISCOUNT_THRESHOLDS[family],
        )
        exempt = normalized_model in DISCOUNT_EXEMPT_MODELS
        exceeded = not exempt and rate > threshold
        alert = exceeded and requests >= DISCOUNT_MIN_REQUESTS
        rows.append({
            "model": model,
            "family": family,
            "requests": requests,
            "rate": rate,
            "threshold": threshold,
            "exempt": exempt,
            "alert": alert,
            "observed": exceeded and not alert,
        })
    return {
        "alert": any(row["alert"] for row in rows),
        "rows": rows,
        "total_requests": int(payload.get("total_reqs") or 0),
        "start": payload.get("start"),
        "end": payload.get("end"),
    }


def _success():
    end = datetime.now(BEIJING)
    start = end - timedelta(minutes=30)
    payload = _router("request-status", {
        "start": start.strftime("%Y-%m-%d"),
        "end": end.strftime("%Y-%m-%d"),
        "start_ts": start.isoformat(timespec="seconds"),
        "end_ts": end.isoformat(timespec="seconds"),
    })
    rate = payload.get("success_rate")
    if rate is None and isinstance(payload.get("summary"), dict):
        rate = payload["summary"].get("success_rate")
    rate = None if rate is None else float(rate)
    return {
        "alert": rate is None or rate <= ROUTER_SUCCESS_THRESHOLD,
        "rate": rate,
        "total": int(payload.get("total_reqs") or 0),
        "start": payload.get("start"),
        "end": payload.get("end"),
        "warnings": payload.get("warnings") or [],
    }


def _collect(name, fn):
    try:
        return fn()
    except Exception as exc:
        return {"alert": True, "error": f"{name} 采集失败：{exc}"[:500]}


def _askmany_login():
    jar = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    data = urllib.parse.urlencode({
        "username": os.environ["ASKMANY_USERNAME"],
        "password": os.environ["ASKMANY_PASSWORD"],
    }).encode()
    request = urllib.request.Request(
        os.environ["ASKMANY_BASE_URL"].rstrip("/") + "/api/oauth2/token",
        data=data,
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    with opener.open(request, timeout=20) as response:
        body = json.loads(response.read().decode())
    if body.get("code") != 0:
        raise RuntimeError(f"登录失败：{body.get('message') or body.get('code')}")
    for cookie in jar:
        if cookie.name == "Authorization":
            value = cookie.value.strip('"')
            return value[7:] if value.startswith("Bearer ") else value
    raise RuntimeError("登录成功但未返回 Authorization Cookie")


def _probe_ttft(model, token):
    ids = {name: str(uuid.uuid4()) for name in ("conv_id", "question_id", "session_id", "session_group_id")}
    payload = {"query": "hi", "engine": model, **ids, "new_session": 1}
    if model.endswith("search"):
        payload["search_scopes"] = ["Chinese-Search"]
    request = urllib.request.Request(
        os.environ["ASKMANY_BASE_URL"].rstrip("/") + "/api/engine/sseQuery",
        data=json.dumps(payload).encode(),
        headers={
            "content-type": "application/json",
            "accept": "text/event-stream",
            "cookie": f"Authorization=Bearer {token}",
        },
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            while time.monotonic() - started < 20:
                raw = response.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                try:
                    event = json.loads(data)
                except ValueError:
                    continue
                if event.get("event") == "error":
                    return {"model": model, "seconds": None, "error": str(event.get("message") or "SSE error")[:160], "alert": True}
                if event.get("event") in ("resp", "thinking") and isinstance(event.get("content"), str) and event["content"]:
                    seconds = round(time.monotonic() - started, 3)
                    return {"model": model, "seconds": seconds, "error": None, "alert": seconds >= TTFT_THRESHOLD_SECONDS}
    except Exception as exc:
        return {"model": model, "seconds": None, "error": str(exc)[:160], "alert": True}
    return {"model": model, "seconds": None, "error": "20 秒内无正文 token", "alert": True}


def _ttft():
    token = _askmany_login()
    rows = []
    with ThreadPoolExecutor(max_workers=len(TTFT_MODELS)) as pool:
        futures = {pool.submit(_probe_ttft, model, token): model for model in TTFT_MODELS}
        for future in as_completed(futures):
            rows.append(future.result())
    rows.sort(key=lambda row: TTFT_MODELS.index(row["model"]))
    return {"alert": any(row["alert"] for row in rows), "rows": rows}


def _fmt_ttft(result):
    if result.get("error"):
        return result["error"]
    abnormal = [row for row in result["rows"] if row["alert"]]
    if not abnormal:
        return f"全部通过（{len(result['rows'])}/{len(result['rows'])}，阈值 < {TTFT_THRESHOLD_SECONDS:g}s）"
    parts = []
    for row in abnormal:
        value = "失败：" + row["error"] if row["error"] else f"{row['seconds']:.3f}s"
        parts.append(f"{row['model']} {value}")
    return "异常模型：" + "；".join(parts) + f"（阈值 < {TTFT_THRESHOLD_SECONDS:g}s）"


def _image_urls(text):
    candidates = re.findall(r"https?://[^\s)\]\"']+|/images/[^\s)\]\"']+", text or "")
    return {url.rstrip(".,;，；") for url in candidates if "/images/" in url}


def _probe_image(spec, token):
    ids = {name: str(uuid.uuid4()) for name in ("conv_id", "question_id", "session_id", "session_group_id")}
    payload = {"query": IMAGE_QUERY, "engine": spec["engine"], **ids, "new_session": 1}
    request = urllib.request.Request(
        os.environ["ASKMANY_BASE_URL"].rstrip("/") + "/api/engine/sseQuery",
        data=json.dumps(payload).encode(),
        headers={
            "content-type": "application/json",
            "accept": "text/event-stream",
            "cookie": f"Authorization=Bearer {token}",
        },
    )
    started = time.monotonic()
    deadline = spec["threshold"] + 15
    content = ""
    urls = set()
    first_image_seconds = None
    try:
        with urllib.request.urlopen(request, timeout=deadline + 5) as response:
            while time.monotonic() - started < deadline:
                raw = response.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except ValueError:
                    continue
                if event.get("event") == "error":
                    raise RuntimeError(str(event.get("message") or "SSE error"))
                chunk = event.get("content")
                if isinstance(chunk, str):
                    content += chunk
                    urls.update(_image_urls(content))
                    if urls and first_image_seconds is None:
                        first_image_seconds = round(time.monotonic() - started, 3)
                if event.get("event") in ("all_done", "done"):
                    break
    except Exception as exc:
        return {**spec, "seconds": first_image_seconds, "image_count": len(urls), "error": str(exc)[:160], "alert": True}
    if not urls:
        return {**spec, "seconds": None, "image_count": 0, "error": f"{deadline} 秒内未检测到生成图片", "alert": True}
    return {
        **spec,
        "seconds": first_image_seconds,
        "image_count": len(urls),
        "error": None,
        "alert": first_image_seconds >= spec["threshold"],
    }


def _images():
    token = _askmany_login()
    rows = []
    with ThreadPoolExecutor(max_workers=len(IMAGE_MODELS)) as pool:
        futures = {pool.submit(_probe_image, spec, token): spec for spec in IMAGE_MODELS}
        for future in as_completed(futures):
            rows.append(future.result())
    rows.sort(key=lambda row: next(i for i, spec in enumerate(IMAGE_MODELS) if spec["engine"] == row["engine"]))
    return {"alert": any(row["alert"] for row in rows), "rows": rows}


def _fmt_images(result):
    if result.get("error"):
        return result["error"]
    parts = []
    for row in result["rows"]:
        if row["error"]:
            detail = f"失败：{row['error']}（图片 {row['image_count']} 张）"
        else:
            detail = f"{row['seconds']:.3f}s，图片 {row['image_count']} 张，阈值 < {row['threshold']}s"
        parts.append(f"{row['label']} {detail}")
    return f"测试 query：{IMAGE_QUERY}；" + "；".join(parts)


def _fmt_discount(result):
    if result.get("error"):
        return result["error"]
    alerts = [r for r in result["rows"] if r["alert"]]
    observed = [r for r in result["rows"] if r["observed"]]
    fmt = lambda r: f"{r['model']} {r['rate']:.4f}（{r['requests']}次）"
    parts = []
    if alerts:
        parts.append("告警：" + "；".join(map(fmt, alerts)))
    if observed:
        parts.append("未告警(<30次)：" + "；".join(map(fmt, observed)))
    if not parts:
        parts.append("全部通过")
    return f"{result.get('start')} ~ {result.get('end')}，总请求 {result['total_requests']}；" + "；".join(parts)


def _mention_line(now):
    beijing = now.astimezone(BEIJING)
    slot = beijing + timedelta(hours=1) if beijing.minute >= 50 else beijing
    if slot.weekday() < 5:
        duty = WEEKDAY_DUTY.get(slot.hour)
    elif 9 <= slot.hour < 12:
        duty = SHE_RUIXU
    elif 12 <= slot.hour < 15:
        duty = YUAN_MING
    elif 15 <= slot.hour < 19:
        duty = YANG_ZIZHAO
    elif 19 <= slot.hour <= 23:
        duty = CHANG_KEJIE
    else:
        duty = None
    ids = list(dict.fromkeys([WU_YUPENG, LI_SHUFAN, duty]))
    mentions = " ".join(f"<at id={open_id}></at>" for open_id in ids if open_id)
    return f"**通知：** {mentions}（{slot.hour:02d}:00 值班）"


def _card(now, ama, discount, success, ttft=None, images=None):
    ttft = ttft or {"alert": False, "rows": []}
    images = images or {"alert": False, "rows": []}
    alert = any(x.get("alert") for x in (ama, discount, success, ttft, images))
    color = "red" if alert else "green"
    ama_text = ama.get("error") or f"{ama['rate']:.2f}%（{ama['errors']}/{ama['requests']}，阈值 < {AMA_THRESHOLD}%）"
    if ama.get("coverage_warning"):
        ama_text += "；CLS 覆盖不完整"
    success_text = success.get("error")
    if not success_text:
        shown = "N/A" if success["rate"] is None else f"{success['rate']:.2f}%"
        success_text = f"{shown}（总请求 {success['total']}，阈值 > {ROUTER_SUCCESS_THRESHOLD}%）"
    elements = [
        {"tag": "markdown", "content": f"**时间：** {now.astimezone(BEIJING):%Y-%m-%d %H:%M:%S} 北京时间"},
        {"tag": "markdown", "content": _mention_line(now)},
        {"tag": "hr"},
        {"tag": "markdown", "content": f"**{'⚠️' if ama.get('alert') else '✅'} AMA 二维码报警率**  [打开生产看板]({AMA_DASHBOARD_URL})\n{ama_text}"},
        {"tag": "markdown", "content": f"**{'⚠️' if discount.get('alert') else '✅'} Router 折扣率**  [打开生产看板]({ROUTER_DISCOUNT_DASHBOARD_URL})\n{_fmt_discount(discount)}"},
        {"tag": "markdown", "content": f"**{'⚠️' if success.get('alert') else '✅'} Router 请求成功率**  [打开生产看板]({ROUTER_SUCCESS_DASHBOARD_URL})\n{success_text}"},
        {"tag": "markdown", "content": f"**{'⚠️' if ttft.get('alert') else '✅'} AskManyAI TTFT**  [打开生产站](https://askmanyai.cn/)\n{_fmt_ttft(ttft)}"},
        {"tag": "markdown", "content": f"**{'⚠️' if images.get('alert') else '✅'} AskManyAI 图片生成**  [打开生产站](https://askmanyai.cn/)\n{_fmt_images(images)}"},
    ]
    return {"msg_type": "interactive", "card": {"header": {"template": color, "title": {"tag": "plain_text", "content": f"{'⚠️' if alert else '✅'} API 监控报告"}}, "elements": elements}}


def main_handler(event, context):
    now = datetime.now(timezone.utc)
    ama = _collect("AMA 报警率", lambda: _ama(now))
    discount = _collect("Router 折扣率", _discount)
    success = _collect("Router 成功率", _success)
    ttft = _collect("AskManyAI TTFT", _ttft)
    images = _collect("AskManyAI 图片生成", _images)
    card = _card(now, ama, discount, success, ttft, images)
    dry_run = os.environ.get("DRY_RUN", "").strip().lower() in {"1", "true", "yes", "on"}
    if not dry_run:
        response = _request_json(os.environ["FEISHU_WEBHOOK_URL"], body=card, timeout=20)
        if response.get("code", response.get("StatusCode", 0)) != 0:
            raise RuntimeError(f"Feishu webhook rejected report: {response}")
    result = {
        "ok": True,
        "alert": any(x.get("alert") for x in (ama, discount, success, ttft, images)),
        "dry_run": dry_run,
    }
    print(json.dumps(result, ensure_ascii=False))
    return result
