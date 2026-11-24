"""
模型接入配置：页面里改，运行中生效，配置落库。

只支持 OpenAI 兼容的 /chat/completions 端点——国内外主流厂商都提供这一形态，
自定义网关也能直接填。配置存在 settings 表（键 llm），重启后自动读回；
旧版 config.json 的 llm_* 字段只在库里还没有配置时迁移一次。

优先级：库里的生效配置 > 环境变量 > config.json > 规则引擎。
页面里「停用」也会落库，重启后不会因为环境变量或 config.json 又生效。

密钥永不回传前端：GET 只给「是否已设置 + 后四位」。
"""
from __future__ import annotations

import json
import time
import uuid

from ..config import CFG, CONFIG_PATH
from . import llm, store

SETTING_KEY = "llm"

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
    """config.json 原文。只作为旧配置的一次性迁移来源。"""
    if not CONFIG_PATH.exists():
        return {}
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _timeout_of(value, fallback: float) -> float:
    try:
        return max(5.0, min(300.0, float(value)))
    except (TypeError, ValueError):
        return fallback


def _clean(data: dict) -> dict:
    """归一库里的 JSON：丢掉非字典项；活动项必须真实存在。"""
    items = data.get("profiles")
    items = [p for p in items if isinstance(p, dict)] if isinstance(items, list) else []
    active = str(data.get("active") or "")
    if active and not any(str(p.get("id") or "") == active for p in items):
        active = ""
    return {"profiles": items, "active": active}


def _from_file() -> dict:
    """旧版 config.json 里的模型配置（多套或单套），没有则返回空结构。"""
    raw = _read_file()
    items = raw.get("llm_profiles")
    items = [p for p in items if isinstance(p, dict)] if isinstance(items, list) else []
    if not items:
        base = str(raw.get("llm_base") or "")
        key = str(raw.get("llm_key") or "")
        model = str(raw.get("llm_model") or "")
        if not (base and key and model):
            return {"profiles": [], "active": ""}
        pid = "M" + time.strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:4]
        items = [{"id": pid, "name": model, "base": base, "key": key,
                  "model": model, "timeout": _timeout_of(raw.get("llm_timeout"), CFG.llm_timeout)}]
    active = str(raw.get("llm_active") or "")
    if active and not any(str(p.get("id") or "") == active for p in items):
        active = ""
    return {"profiles": items, "active": active or str(items[0].get("id") or "")}


def _save(state: dict) -> None:
    store.put_setting(SETTING_KEY, {"profiles": state["profiles"], "active": state["active"]})


def _load_raw() -> tuple[dict, bool]:
    """(配置, 库里是否已有记录)；旧配置在首次读取时迁入，之后以库为准。"""
    store.init()                       # 临时库、首次调用也能直接用
    stored = store.get_setting(SETTING_KEY)
    if isinstance(stored, dict) and "profiles" in stored:
        return _clean(stored), True
    legacy = _from_file()
    if legacy["profiles"]:
        _save(legacy)
        return legacy, True
    return legacy, False


def _load() -> dict:
    return _load_raw()[0]


def _active(state: dict) -> dict | None:
    """当前生效的配置；缺 base / key / model 任一项视为未配置。"""
    pid = str(state.get("active") or "")
    for p in state["profiles"]:
        if pid and str(p.get("id") or "") == pid:
            return p if (p.get("base") and p.get("key") and p.get("model")) else None
    return None


def _profile_out(p: dict, active_id: str) -> dict:
    key = str(p.get("key") or "")
    return {
        "id": str(p.get("id") or ""),
        "name": str(p.get("name") or p.get("model") or "未命名配置")[:60],
        "base": str(p.get("base") or ""),
        "model": str(p.get("model") or ""),
        "timeout": _timeout_of(p.get("timeout"), CFG.llm_timeout),
        "key_set": bool(key),
        "key_hint": ("…" + key[-4:]) if key else "",
        "active": bool(active_id and str(p.get("id") or "") == active_id),
    }


def _apply(p: dict) -> None:
    CFG.llm_base = str(p.get("base") or "")
    CFG.llm_key = str(p.get("key") or "")
    CFG.llm_model = str(p.get("model") or "")
    CFG.llm_timeout = _timeout_of(p.get("timeout"), CFG.llm_timeout)


def _clear_runtime() -> None:
    CFG.llm_base = CFG.llm_key = CFG.llm_model = ""


def bootstrap() -> None:
    """启动时把库里的生效配置读回内存；库里有记录就以库为准。

    库里有记录但当前是「停用」时显式清空内存配置——否则环境变量或
    config.json 里的旧值会在重启后把已停用的模型顶回来。
    """
    state, recorded = _load_raw()
    if not recorded:
        return                          # 库里没有配置：沿用环境变量 / config.json
    prof = _active(state)
    if prof:
        _apply(prof)
    else:
        _clear_runtime()


def public() -> dict:
    """前端可见的配置：密钥只暴露尾号，列表里也不回传明文。"""
    state = _load()
    active_id = state["active"] if CFG.llm_enabled else ""
    profiles = [_profile_out(p, active_id) for p in state["profiles"]]
    if not profiles and CFG.llm_enabled:
        # 只有环境变量或旧 config.json 提供、尚未在页面保存过：合成一条只读记录
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


def save_profile(data: dict) -> dict:
    """新增或编辑一套配置，并立即切为当前使用。"""
    base = str(data.get("base") or "").strip().rstrip("/")
    model = str(data.get("model") or "").strip()
    if not base or not model:
        raise ValueError("Base URL 与模型名都不能为空")
    if not base.startswith(("http://", "https://")):
        raise ValueError("Base URL 需以 http:// 或 https:// 开头")
    timeout = _timeout_of(data.get("timeout"), CFG.llm_timeout)

    state = _load()
    items = state["profiles"]
    pid = str(data.get("id") or "").strip()
    # 页面拿到的是合成记录 current：落到第一条已存配置上，避免重复建档
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
    _save({"profiles": items, "active": prof["id"]})
    _apply(prof)
    return public()


def activate(pid: str) -> dict:
    state = _load()
    pid = (pid or "").strip()
    if pid == "current" and CFG.llm_enabled and not state["profiles"]:
        return public()
    if pid == "current" and state["profiles"]:
        pid = str(state["profiles"][0].get("id") or "")
    prof = next((p for p in state["profiles"] if str(p.get("id") or "") == pid), None)
    if not prof:
        raise ValueError("配置不存在，请刷新后重试")
    if not (prof.get("base") and prof.get("key") and prof.get("model")):
        raise ValueError("该配置缺少 Base URL / API Key / 模型名，请先补全")
    _save({"profiles": state["profiles"], "active": pid})
    _apply(prof)
    return public()


def delete_profile(pid: str) -> dict:
    state = _load()
    pid = (pid or "").strip()
    if pid == "current" and not state["profiles"]:
        return clear()
    target = next((p for p in state["profiles"] if str(p.get("id") or "") == pid), None)
    if not target:
        raise ValueError("配置不存在，请刷新后重试")
    items = [p for p in state["profiles"] if str(p.get("id") or "") != pid]
    active = state["active"]
    nxt = None
    if active == pid:
        # 删除当前配置后优先切到另一套完整配置；都不完整则回到规则引擎
        nxt = next((p for p in items if p.get("base") and p.get("key") and p.get("model")), None)
        active = str(nxt.get("id") or "") if nxt else ""
    _save({"profiles": items, "active": active})
    if not items or (active != state["active"] and not nxt):
        _clear_runtime()
    elif nxt:
        _apply(nxt)
    return public()


def clear() -> dict:
    """停用当前配置：列表保留，随时可重新启用。"""
    state = _load()
    _save({"profiles": state["profiles"], "active": ""})
    _clear_runtime()
    return public()


def probe(base: str = "", key: str = "", model: str = "",
          timeout: float | None = None, profile_id: str = "") -> dict:
    """用页面上尚未保存的值试一次；不落盘、不改 CFG。"""
    base = (base or CFG.llm_base).strip().rstrip("/")
    key = (key or "").strip()
    model = (model or CFG.llm_model).strip()
    if not key and profile_id:
        for p in _load()["profiles"]:
            if str(p.get("id") or "") == profile_id:
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
