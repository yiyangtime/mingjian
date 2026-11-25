"""
报告推送：飞书 / 钉钉 / 企业微信 / Telegram / 通用 Webhook。

渠道配置存在 settings 表（键 push），页面里改、保存即生效、重启自动读回；
可以同时启用多个渠道，也可以关闭自动推送、只在页面上手动推送。

安全约束：
  · Webhook 地址与密钥都按凭证对待：GET 只回「是否已设置 + 尾号」，原文不出后端；
  · 编辑已有渠道时留空表示沿用原值，不会被空字符串覆盖。

可靠性约束：
  · 单个渠道失败不影响其它渠道，也不抛给主流程；
  · 每个渠道返回独立的成功/失败与耗时，由调用方写日志与审计。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.parse
import uuid

from ..core import net
from . import store

SETTING_KEY = "push"

# 字段约定：webhook 系填 url（飞书 / 钉钉可选 secret 加签），
# Telegram 的 Bot Token 存在 secret 里，chat_id 明文本地保存。
TYPES = [
    {"id": "feishu", "name": "飞书", "group": "国内", "fields": ["url", "secret"],
     "note": "群机器人 Webhook；填了签名密钥就自动加签"},
    {"id": "dingtalk", "name": "钉钉", "group": "国内", "fields": ["url", "secret"],
     "note": "群机器人 Webhook；填了加签密钥就自动加签"},
    {"id": "wecom", "name": "企业微信", "group": "国内", "fields": ["url"],
     "note": "群机器人 Webhook（个人微信没有官方机器人接口）"},
    {"id": "telegram", "name": "Telegram", "group": "国外", "fields": ["secret", "chat_id"],
     "note": "Bot Token + Chat ID，可推送到群或频道"},
    {"id": "webhook", "name": "通用 Webhook", "group": "国外", "fields": ["url", "secret"],
     "note": "POST JSON；填了密钥则带 Authorization: Bearer"},
]
_TYPE_IDS = {t["id"] for t in TYPES}


def _type_name(tid: str) -> str:
    return next((t["name"] for t in TYPES if t["id"] == tid), tid)


def _hint(value: str, keep: int = 4) -> str:
    v = str(value or "")
    return ("…" + v[-keep:]) if v else ""


def _url_hint(url: str) -> str:
    """只暴露 scheme 与域名，路径里的 token 一律打码。"""
    if not url:
        return ""
    try:
        u = urllib.parse.urlsplit(url)
        return f"{u.scheme}://{u.netloc}/…{url[-4:]}"
    except Exception:  # noqa: BLE001 —— 打码失败也不能把原文带出去
        return "…" + url[-4:]


def _clean(data: dict) -> dict:
    rows = data.get("channels")
    rows = [c for c in rows if isinstance(c, dict)] if isinstance(rows, list) else []
    channels = []
    for c in rows:
        tid = str(c.get("type") or "")
        if tid not in _TYPE_IDS:
            continue
        channels.append({
            "id": str(c.get("id") or ""),
            "type": tid,
            "name": str(c.get("name") or "")[:60],
            "url": str(c.get("url") or ""),
            "secret": str(c.get("secret") or ""),
            "chat_id": str(c.get("chat_id") or ""),
            "enabled": bool(c.get("enabled", True)),
        })
    return {"channels": channels,
            "auto": bool(data.get("auto")),
            "base_url": str(data.get("base_url") or "").strip()}


def _load_raw() -> tuple[dict, bool]:
    """(配置, 库里是否已有记录)；没有记录时默认关闭自动推送。"""
    store.init()
    stored = store.get_setting(SETTING_KEY)
    if isinstance(stored, dict) and "channels" in stored:
        return _clean(stored), True
    return {"channels": [], "auto": False, "base_url": ""}, False


def _save(state: dict) -> None:
    store.put_setting(SETTING_KEY, state)


def _channel_out(c: dict) -> dict:
    return {
        "id": c["id"],
        "type": c["type"],
        "name": c["name"] or _type_name(c["type"]),
        "url_set": bool(c["url"]),
        "url_hint": _url_hint(c["url"]),
        "secret_set": bool(c["secret"]),
        "secret_hint": _hint(c["secret"]),
        "chat_id": c["chat_id"],
        "enabled": c["enabled"],
    }


def public() -> dict:
    """前端可见配置：Webhook 路径与密钥只给尾号，原文不出后端。"""
    state, recorded = _load_raw()
    rows = [_channel_out(c) for c in state["channels"]]
    return {
        "types": TYPES,
        "channels": rows,
        "auto": state["auto"],
        "base_url": state["base_url"],
        "enabled": any(r["enabled"] for r in rows),
        "recorded": recorded,
    }


def _resolve(data: dict, existing: dict | None = None) -> dict:
    """把页面提交的字段合并成一条渠道配置；空值不覆盖已存值。"""
    tid = str(data.get("type") or (existing or {}).get("type") or "")
    if tid not in _TYPE_IDS:
        raise ValueError("未知的推送渠道类型")
    url = str(data.get("url") or "").strip()
    secret = str(data.get("secret") or "").strip()
    chat_id = str(data.get("chat_id") or "").strip()
    if existing:
        url = url or existing["url"]
        secret = secret or existing["secret"]
        chat_id = chat_id or existing["chat_id"]
    if tid == "telegram":
        if not secret:
            raise ValueError("Telegram 需要 Bot Token；编辑已有渠道时留空表示沿用原值")
        if not chat_id:
            raise ValueError("Telegram 需要 Chat ID")
    elif not url.startswith(("http://", "https://")):
        raise ValueError("Webhook 地址需以 http:// 或 https:// 开头")
    name = str(data.get("name") or (existing or {}).get("name") or _type_name(tid))[:60]
    if "enabled" in data:
        enabled = bool(data.get("enabled"))
    else:
        enabled = bool(existing["enabled"]) if existing else True
    return {"id": str((existing or {}).get("id") or ""), "type": tid, "name": name,
            "url": url, "secret": secret, "chat_id": chat_id, "enabled": enabled}


def save_channel(data: dict) -> dict:
    state, _ = _load_raw()
    cid = str(data.get("id") or "").strip()
    existing = next((c for c in state["channels"] if c["id"] == cid), None) if cid else None
    if cid and not existing:
        raise ValueError("渠道不存在，请刷新后重试")
    row = _resolve(data, existing)
    if existing:
        existing.update(row)
    else:
        row["id"] = "P" + time.strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:4]
        state["channels"].append(row)
    _save(state)
    return public()


def toggle_channel(cid: str, enabled: bool) -> dict:
    state, _ = _load_raw()
    target = next((c for c in state["channels"] if c["id"] == (cid or "").strip()), None)
    if not target:
        raise ValueError("渠道不存在，请刷新后重试")
    target["enabled"] = bool(enabled)
    _save(state)
    return public()


def delete_channel(cid: str) -> dict:
    state, _ = _load_raw()
    items = [c for c in state["channels"] if c["id"] != (cid or "").strip()]
    if len(items) == len(state["channels"]):
        raise ValueError("渠道不存在，请刷新后重试")
    state["channels"] = items
    _save(state)
    return public()


def set_options(auto: bool | None = None, base_url: str | None = None) -> dict:
    state, _ = _load_raw()
    if auto is not None:
        state["auto"] = bool(auto)
    if base_url is not None:
        url = str(base_url or "").strip().rstrip("/")
        if url and not url.startswith(("http://", "https://")):
            raise ValueError("服务地址需以 http:// 或 https:// 开头")
        state["base_url"] = url
    _save(state)
    return public()


def auto_enabled() -> bool:
    state, _ = _load_raw()
    return bool(state["auto"] and any(c["enabled"] for c in state["channels"]))


def base_url() -> str:
    return _load_raw()[0]["base_url"]


# ---------------- 发送 ----------------

def _sign(secret: str, ts: str) -> str:
    """飞书 / 钉钉共用的加签算法：timestamp\\nsecret 作为 key，空串做 HMAC-SHA256。"""
    key = f"{ts}\n{secret}".encode("utf-8")
    return base64.b64encode(hmac.new(key, b"", hashlib.sha256).digest()).decode()


def _payload(c: dict, title: str, text: str) -> tuple[str, dict, dict]:
    """按渠道类型组装 (url, body, headers)。"""
    tid = c["type"]
    if tid == "feishu":
        body = {"msg_type": "text", "content": {"text": text}}
        if c["secret"]:
            ts = str(int(time.time()))
            body.update({"timestamp": ts, "sign": _sign(c["secret"], ts)})
        return c["url"], body, {}
    if tid == "dingtalk":
        url = c["url"]
        body = {"msgtype": "markdown", "markdown": {"title": title, "text": text}}
        if c["secret"]:
            ts = str(int(time.time() * 1000))
            url = f"{url}&timestamp={ts}&sign={urllib.parse.quote_plus(_sign(c['secret'], ts))}"
        return url, body, {}
    if tid == "wecom":
        return c["url"], {"msgtype": "markdown", "markdown": {"content": text}}, {}
    if tid == "telegram":
        return (f"https://api.telegram.org/bot{c['secret']}/sendMessage",
                {"chat_id": c["chat_id"], "text": text, "disable_web_page_preview": True}, {})
    headers = {"Authorization": f"Bearer {c['secret']}"} if c["secret"] else {}
    return c["url"], {"title": title, "text": text}, headers


def _check(tid: str, resp: str) -> None:
    """把各平台的「HTTP 200 但业务失败」翻译成异常。"""
    try:
        data = json.loads(resp or "{}")
    except Exception:  # noqa: BLE001 —— 非 JSON 响应按平台协议判定
        data = {}
    if tid == "feishu":
        code = data.get("code", data.get("StatusCode"))
        if code not in (0, None):
            msg = data.get("msg") or data.get("StatusMessage") or ""
            raise RuntimeError(f"飞书返回 {code}：{str(msg)[:80]}")
    elif tid in ("dingtalk", "wecom"):
        code = data.get("errcode")
        if code is not None and int(code) != 0:
            label = "钉钉" if tid == "dingtalk" else "企业微信"
            raise RuntimeError(f"{label}返回 {code}：{str(data.get('errmsg') or '')[:80]}")
    elif tid == "telegram" and not data.get("ok"):
        raise RuntimeError(f"Telegram 返回：{str(data.get('description') or '')[:80]}")


def _send(c: dict, title: str, text: str) -> None:
    url, body, headers = _payload(c, title, text)
    resp = net.post_json(url, body, headers=headers, timeout=10, retries=1)
    _check(c["type"], resp)


def send_all(title: str, text: str) -> list[dict]:
    """向所有启用渠道推送；逐个收集结果，任何失败都不外抛。"""
    state, _ = _load_raw()
    out = []
    for c in state["channels"]:
        if not c["enabled"]:
            continue
        t0 = time.time()
        try:
            _send(c, title, text)
            out.append({"id": c["id"], "name": c["name"] or _type_name(c["type"]),
                        "type": c["type"], "ok": True,
                        "ms": int((time.time() - t0) * 1000), "error": ""})
        except Exception as e:  # noqa: BLE001 —— 失败原因要原样回给页面与日志
            out.append({"id": c["id"], "name": c["name"] or _type_name(c["type"]),
                        "type": c["type"], "ok": False,
                        "ms": int((time.time() - t0) * 1000),
                        "error": f"{type(e).__name__}: {e}"[:300]})
    return out


def probe(data: dict) -> dict:
    """用页面提交、尚未保存的配置发一条测试消息；不落盘。"""
    cid = str(data.get("id") or "").strip()
    state, _ = _load_raw()
    existing = next((c for c in state["channels"] if c["id"] == cid), None) if cid else None
    c = _resolve(data, existing)
    t0 = time.time()
    try:
        _send(c, "明鉴 · 推送测试",
              f"这是一条来自明鉴的测试消息（{time.strftime('%Y-%m-%d %H:%M:%S')}）。"
              "收到即表示该渠道配置可用。")
    except Exception as e:  # noqa: BLE001 —— 连接失败的原因要原样带回页面
        return {"ok": False, "ms": int((time.time() - t0) * 1000),
                "error": f"{type(e).__name__}: {e}"[:300]}
    return {"ok": True, "ms": int((time.time() - t0) * 1000), "reply": "已送达"}
