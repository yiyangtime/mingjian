"""
模型接入配置：页面里改，运行中生效。

只支持 OpenAI 兼容的 /chat/completions 端点——国内外主流厂商都提供这一形态，
自定义网关也能直接填。写回 config.json 的 llm_* 字段，同时更新内存里的 CFG，
不必重启服务。

密钥永不回传前端：GET 只给「是否已设置 + 后四位」。
"""
from __future__ import annotations

import json
import time
import uuid

from ..config import CFG, CONFIG_PATH
from . import llm

# 预设只负责填好 base 与候选模型，所有字段仍可手改
PROVIDERS = [
    {"id": "deepseek", "name": "DeepSeek", "group": "国内",
     "base": "https://api.deepseek.com/v1",
     "models": ["deepseek-chat", "deepseek-reasoner"]},
    {"id": "dashscope", "name": "通义千问（阿里云百炼）", "group": "国内",
     "base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
     "models": ["qwen-plus", "qwen-max", "qwen-turbo"]},
    {"id": "moonshot", "name": "Kimi（月之暗面）", "group": "国内",
     "base": "https://api.moonshot.cn/v1",
     "models": ["moonshot-v1-8k", "moonshot-v1-32k", "kimi-k2-0711-preview"]},
    {"id": "zhipu", "name": "智谱 GLM", "group": "国内",
     "base": "https://open.bigmodel.cn/api/paas/v4",
     "models": ["glm-4-flash", "glm-4-plus", "glm-4-air"]},
    {"id": "siliconflow", "name": "硅基流动", "group": "国内",
     "base": "https://api.siliconflow.cn/v1",
     "models": ["deepseek-ai/DeepSeek-V3", "Qwen/Qwen2.5-72B-Instruct"]},
    {"id": "ark", "name": "火山方舟（豆包）", "group": "国内",
     "base": "https://ark.cn-beijing.volces.com/api/v3",
     "models": ["doubao-1.5-pro-32k-250115"],
     "note": "模型名也可填方舟的推理接入点 ID"},
    {"id": "qianfan", "name": "百度千帆", "group": "国内",
     "base": "https://qianfan.baidubce.com/v2",
     "models": ["ernie-4.5-turbo-128k"]},
    {"id": "openai", "name": "OpenAI", "group": "国外",
     "base": "https://api.openai.com/v1",
     "models": ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"]},
    {"id": "gemini", "name": "Google Gemini", "group": "国外",
     "base": "https://generativelanguage.googleapis.com/v1beta/openai",
     "models": ["gemini-2.0-flash", "gemini-2.5-flash"]},
    {"id": "openrouter", "name": "OpenRouter", "group": "国外",
     "base": "https://openrouter.ai/api/v1",
     "models": ["openai/gpt-4o-mini", "anthropic/claude-3.5-sonnet"]},
    {"id": "ollama", "name": "Ollama（本地）", "group": "本地",
     "base": "http://127.0.0.1:11434/v1",
     "models": ["qwen2.5:7b", "llama3.1:8b"],
     "note": "本地服务不校验密钥，可填任意占位符", "keyless": True},
]


def _read_file() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _write_raw(raw: dict) -> None:
    CONFIG_PATH.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _seed_profiles(raw: dict) -> list[dict]:
    """把旧版单套配置沉成第一条记录，之后新增/切换都不会丢已存密钥。"""
    items = raw.get("llm_profiles")
    if isinstance(items, list) and items:
        return items
    if not (CFG.llm_base and CFG.llm_key and CFG.llm_model):
        return []
    pid = "M" + time.strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:4]
    items = [{"id": pid, "name": CFG.llm_model, "base": CFG.llm_base,
              "key": CFG.llm_key, "model": CFG.llm_model,
              "timeout": CFG.llm_timeout}]
    raw["llm_profiles"] = items
    raw["llm_active"] = pid
    _write_raw(raw)
    return items


def _profile_out(p: dict, active_id: str) -> dict:
    key = str(p.get("key") or "")
    return {
        "id": str(p.get("id") or ""),
        "name": str(p.get("name") or p.get("model") or "未命名配置")[:60],
        "base": str(p.get("base") or ""),
        "model": str(p.get("model") or ""),
        "timeout": float(p.get("timeout") or CFG.llm_timeout),
        "key_set": bool(key),
        "key_hint": ("…" + key[-4:]) if key else "",
        "active": bool(active_id and str(p.get("id") or "") == active_id),
    }


def public() -> dict:
    """前端可见的配置：密钥只暴露尾号，列表里也不回传明文。"""
    raw = _read_file()
    items = raw.get("llm_profiles")
    items = items if isinstance(items, list) else []
    active_id = str(raw.get("llm_active") or "") if CFG.llm_enabled else ""
    profiles = [_profile_out(p, active_id) for p in items if isinstance(p, dict)]
    if not profiles and CFG.llm_enabled:
        # 旧版只有一套配置：合成一条只读记录，第一次保存/切换时再落盘
        profiles = [{
            "id": "current", "name": CFG.llm_model, "base": CFG.llm_base,
            "model": CFG.llm_model, "timeout": CFG.llm_timeout,
            "key_set": True, "key_hint": "…" + CFG.llm_key[-4:], "active": True,
        }]
        active_id = "current"
    return {
        "providers": PROVIDERS,
        "profiles": profiles,
        "active_id": active_id,
        "base": CFG.llm_base,
        "model": CFG.llm_model,
        "timeout": CFG.llm_timeout,
        "key_set": bool(CFG.llm_key),
        "key_hint": ("…" + CFG.llm_key[-4:]) if CFG.llm_key else "",
        "active": CFG.llm_enabled,
    }


def _apply(p: dict) -> None:
    CFG.llm_base = str(p.get("base") or "")
    CFG.llm_key = str(p.get("key") or "")
    CFG.llm_model = str(p.get("model") or "")
    try:
        CFG.llm_timeout = float(p.get("timeout") or CFG.llm_timeout)
    except (TypeError, ValueError):
        pass


def save_profile(data: dict) -> dict:
    """新增或编辑一套配置，并立即切为当前使用。"""
    base = str(data.get("base") or "").strip().rstrip("/")
    model = str(data.get("model") or "").strip()
    if not base or not model:
        raise ValueError("Base URL 与模型名都不能为空")
    if not base.startswith(("http://", "https://")):
        raise ValueError("Base URL 需以 http:// 或 https:// 开头")
    try:
        timeout = float(data.get("timeout") or CFG.llm_timeout)
    except (TypeError, ValueError):
        timeout = CFG.llm_timeout
    timeout = max(5.0, min(300.0, timeout))

    raw = _read_file()
    items = _seed_profiles(raw)
    pid = str(data.get("id") or "").strip()
    # 旧版页面拿到的是合成记录 current：落到刚沉淀出的第一条上，避免重复建档
    if pid == "current" and items:
        pid = str(items[0].get("id") or "")
    existing = next((p for p in items if str(p.get("id") or "") == pid), None) if pid else None
    key = str(data.get("key") or "").strip()
    if existing and not key:
        key = str(existing.get("key") or "")
    if pid == "current" and not key:
        key = CFG.llm_key
    if not key:
        raise ValueError("API Key 不能为空；编辑已有配置时留空表示沿用原密钥")

    if existing:
        existing.update({"name": str(data.get("name") or existing.get("name") or model)[:60],
                         "base": base, "key": key, "model": model, "timeout": timeout})
        prof = existing
    else:
        prof = {"id": "M" + time.strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:4],
                "name": str(data.get("name") or model)[:60],
                "base": base, "key": key, "model": model, "timeout": timeout}
        items.append(prof)
    raw["llm_profiles"] = items
    raw["llm_active"] = prof["id"]
    raw.update({"llm_base": base, "llm_key": key, "llm_model": model,
                "llm_timeout": timeout})
    _write_raw(raw)
    _apply(prof)
    return public()


def activate(pid: str) -> dict:
    raw = _read_file()
    items = _seed_profiles(raw)
    pid = (pid or "").strip()
    if pid == "current" and CFG.llm_enabled and not items:
        return public()
    prof = next((p for p in items if str(p.get("id") or "") == pid), None)
    if not prof:
        raise ValueError("配置不存在，请刷新后重试")
    raw["llm_active"] = pid
    raw.update({"llm_base": prof.get("base") or "", "llm_key": prof.get("key") or "",
                "llm_model": prof.get("model") or "",
                "llm_timeout": float(prof.get("timeout") or CFG.llm_timeout)})
    _write_raw(raw)
    _apply(prof)
    return public()


def delete_profile(pid: str) -> dict:
    raw = _read_file()
    items = _seed_profiles(raw)
    pid = (pid or "").strip()
    if pid == "current" and not items:
        return clear()
    target = next((p for p in items if str(p.get("id") or "") == pid), None)
    if not target:
        raise ValueError("配置不存在，请刷新后重试")
    items = [p for p in items if str(p.get("id") or "") != pid]
    raw["llm_profiles"] = items
    if str(raw.get("llm_active") or "") == pid:
        if items:
            nxt = items[0]
            raw["llm_active"] = nxt["id"]
            raw.update({"llm_base": nxt.get("base") or "", "llm_key": nxt.get("key") or "",
                        "llm_model": nxt.get("model") or "",
                        "llm_timeout": float(nxt.get("timeout") or CFG.llm_timeout)})
            _apply(nxt)
        else:
            raw["llm_active"] = ""
            raw.update({"llm_base": "", "llm_key": "", "llm_model": ""})
            CFG.llm_base = CFG.llm_key = CFG.llm_model = ""
    _write_raw(raw)
    return public()


def _write_file(values: dict) -> None:
    """合并写 config.json，保留甲方自己加的其它键。"""
    raw: dict = {}
    if CONFIG_PATH.exists():
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
    raw.update(values)
    CONFIG_PATH.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def save(base: str, key: str | None, model: str, timeout: float | None) -> dict:
    base = (base or "").strip().rstrip("/")
    model = (model or "").strip()
    if not base or not model:
        raise ValueError("Base URL 与模型名都不能为空；要停用请点「清除配置」")
    if not base.startswith(("http://", "https://")):
        raise ValueError("Base URL 需以 http:// 或 https:// 开头")
    key = (key or "").strip()
    if not key:
        key = CFG.llm_key          # 留空表示沿用已保存的密钥
    if not key:
        raise ValueError("API Key 不能为空")
    try:
        timeout = float(timeout or CFG.llm_timeout)
    except (TypeError, ValueError):
        timeout = CFG.llm_timeout
    timeout = max(5.0, min(300.0, timeout))

    _write_file({"llm_base": base, "llm_key": key, "llm_model": model,
                 "llm_timeout": timeout})
    CFG.llm_base, CFG.llm_key, CFG.llm_model, CFG.llm_timeout = (
        base, key, model, timeout)
    return public()


def clear() -> dict:
    raw = _read_file()
    raw.update({"llm_base": "", "llm_key": "", "llm_model": "", "llm_active": ""})
    _write_raw(raw)
    CFG.llm_base = CFG.llm_key = CFG.llm_model = ""
    return public()


def probe(base: str = "", key: str = "", model: str = "",
          timeout: float | None = None, profile_id: str = "") -> dict:
    """用页面上尚未保存的值试一次；不落盘、不改 CFG。"""
    base = (base or CFG.llm_base).strip().rstrip("/")
    key = (key or "").strip()
    model = (model or CFG.llm_model).strip()
    if not key and profile_id:
        raw = _read_file()
        items = raw.get("llm_profiles")
        for p in items if isinstance(items, list) else []:
            if isinstance(p, dict) and str(p.get("id") or "") == profile_id:
                key = str(p.get("key") or "")
                break
    if not key:
        key = CFG.llm_key
    if not (base and key and model):
        raise ValueError("Base URL、API Key、模型名需先填写")
    wait = float(timeout or CFG.llm_timeout)
    wait = max(5.0, min(45.0, wait))

    t0 = time.time()
    try:
        reply = llm.chat_with(
            base, key, model, wait,
            [{"role": "user", "content": "只回复两个字母：OK"}],
            temperature=0, max_tokens=16)
    except Exception as e:  # noqa: BLE001 —— 连接失败的原因要原样带回页面
        return {"ok": False, "ms": int((time.time() - t0) * 1000),
                "error": f"{type(e).__name__}: {e}"[:300]}
    return {"ok": True, "ms": int((time.time() - t0) * 1000),
            "reply": (reply or "").strip()[:120]}
