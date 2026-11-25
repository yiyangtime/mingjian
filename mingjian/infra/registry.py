"""
企业工商登记数据源：正式接口接入 + 本地缓存。

国家企业信用信息公示系统只提供网页查询，没有面向公众的开放 API；要覆盖
"所有合法注册的企业"，稳定路径是使用商业数据服务的授权接口（天眼查、
企查查、爱企查、启信宝或云市场上的同类商品）。本项目不绑定厂商：

  · 预设只提供请求形态，端点、鉴权、结果路径、字段映射都可改；
  · 密钥只落库、不回传前端，与模型配置同一口径；
  · 未配置时整条链路关闭，搜索行为与之前完全一致；
  · 上游失败只降级、不抛出——工商接口挂了不应阻断检索。

结果按统一社会信用代码/企业名归一并写入 companies 表：接口按次计费，
查过一次就留作本地档案，下次搜索不再消耗额度。
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import urllib.parse
import uuid
from concurrent.futures import ThreadPoolExecutor

from ..core.net import FetchError, fetch
from . import store

SETTING_KEY = "registry"
CACHE_TTL = 300.0

# 预设只负责把常见形态填好；字段以厂商当期文档为准，页面可逐项覆盖。
PRESETS = [
    {
        "id": "tianyancha", "name": "天眼查开放平台", "vendor": "tianyancha",
        "base": "https://open.api.tianyancha.com",
        "path": "/services/open/search/2.0",
        "auth": "header", "auth_name": "Authorization", "auth_prefix": "",
        "fields": {"keyword": "word", "page": "pageNum", "size": "pageSize"},
        "items_path": "result.items",
        "code_path": "error_code", "msg_path": "reason",
        "ok_codes": ["0"], "empty_codes": ["300000"],
        "map": {
            "name": "name", "credit_code": "creditCode",
            "legal_person": "legalPersonName", "reg_capital": "regCapital",
            "reg_status": "regStatus", "establish_date": "estiblishTime",
            "location": "regLocation", "industry": "industry",
            "business": "businessScope",
        },
        "note": "搜索接口按次计费；estiblishTime 为时间戳，已自动转日期。",
    },
    {
        "id": "qcc", "name": "企查查开放平台", "vendor": "qcc",
        "base": "https://api.qichacha.com",
        "path": "/ECIV4/Search",
        "auth": "qcc", "auth_name": "", "auth_prefix": "",
        "fields": {"keyword": "searchKey", "page": "pageIndex", "size": "pageSize"},
        "items_path": "Result",
        "code_path": "Status", "msg_path": "Message",
        "ok_codes": ["200"], "empty_codes": [],
        "map": {
            "name": "Name", "credit_code": "CreditCode",
            "legal_person": "OperName", "reg_capital": "RegCapital",
            "reg_status": "Status", "establish_date": "StartDate",
            "location": "Address", "industry": "Industry",
            "business": "Scope",
        },
        "note": "密钥由 key 与 secret 组成，请求头自动签名。",
    },
]

# 标准字段 → 中文名，用于页面核对与档案展示
FIELDS = {
    "name": "企业名称", "credit_code": "统一社会信用代码",
    "legal_person": "法定代表人", "reg_capital": "注册资本",
    "reg_status": "经营状态", "establish_date": "成立日期",
    "location": "注册地址", "website": "官网", "industry": "行业",
    "business": "经营范围",
}

_cache: dict[str, tuple[float, list[dict]]] = {}
_cache_lock = threading.Lock()


def _timeout_of(value, fallback: float = 8.0) -> float:
    try:
        return max(3.0, min(30.0, float(value)))
    except (TypeError, ValueError):
        return fallback


def _clean(data: dict) -> dict:
    items = data.get("profiles")
    items = [p for p in items if isinstance(p, dict)] if isinstance(items, list) else []
    active = str(data.get("active") or "")
    if active and not any(str(p.get("id") or "") == active for p in items):
        active = ""
    return {"profiles": items, "active": active}


def _load_raw() -> tuple[dict, bool]:
    store.init()
    stored = store.get_setting(SETTING_KEY)
    if isinstance(stored, dict) and "profiles" in stored:
        return _clean(stored), True
    return {"profiles": [], "active": ""}, False


def _save(state: dict) -> None:
    store.put_setting(SETTING_KEY, {"profiles": state["profiles"], "active": state["active"]})


def _active(state: dict) -> dict | None:
    pid = str(state.get("active") or "")
    for p in state["profiles"]:
        if pid and str(p.get("id") or "") == pid:
            return p if (p.get("base") and p.get("path") and p.get("key")) else None
    return None


def active() -> dict | None:
    """当前生效的工商接口；缺地址、路径或密钥视为未配置。"""
    return _active(_load_raw()[0])


def enabled() -> bool:
    return active() is not None


def _profile_out(p: dict) -> dict:
    key = str(p.get("key") or "")
    secret = str(p.get("secret") or "")
    preset = next((x for x in PRESETS if x["id"] == p.get("preset")), {})
    return {
        "id": str(p.get("id") or ""),
        "name": str(p.get("name") or preset.get("name") or "工商接口"),
        "preset": str(p.get("preset") or "custom"),
        "base": str(p.get("base") or ""),
        "path": str(p.get("path") or ""),
        "auth": str(p.get("auth") or "header"),
        "auth_name": str(p.get("auth_name") or ""),
        "auth_prefix": str(p.get("auth_prefix") or ""),
        "fields": p.get("fields") or {},
        "items_path": str(p.get("items_path") or ""),
        "code_path": str(p.get("code_path") or ""),
        "msg_path": str(p.get("msg_path") or ""),
        "map": p.get("map") or {},
        "ok_codes": p.get("ok_codes") or [],
        "empty_codes": p.get("empty_codes") or [],
        "timeout": _timeout_of(p.get("timeout")),
        "key_set": bool(key),
        "key_hint": ("…" + key[-4:]) if key else "",
        "secret_set": bool(secret),
        "active": bool(p.get("_active")),
    }


def public() -> dict:
    """前端可见配置：密钥只给尾号，原文不出后端。"""
    state = _load_raw()[0]
    act = _active(state)
    rows = []
    for p in state["profiles"]:
        item = dict(p)
        item["_active"] = bool(act and p.get("id") == act.get("id"))
        rows.append(_profile_out(item))
    return {
        "presets": [{k: v for k, v in x.items()} for x in PRESETS],
        "profiles": rows,
        "active_id": str(act.get("id") or "") if act else "",
        "enabled": bool(act),
        "fields": FIELDS,
        "cache": store.company_stats(),
    }


def _preset_of(data: dict) -> dict:
    pid = str(data.get("preset") or "")
    base = next((p for p in PRESETS if p["id"] == pid), {})
    out = dict(base)
    out.pop("id", None)
    out.pop("name", None)
    out.pop("vendor", None)
    out.pop("note", None)
    return out


def _merge_profile(data: dict, existing: dict | None) -> dict:
    """页面提交的字段覆盖预设；未提交的沿用原值。"""
    preset = _preset_of(data)
    old = dict(existing or {})
    # 只收配置字段：action / actor 这类请求控制字段不进配置
    keys = ("name", "preset", "base", "path", "auth", "auth_name", "auth_prefix",
            "key", "secret", "fields", "items_path", "code_path", "msg_path",
            "ok_codes", "empty_codes", "map", "timeout")
    merged = {**preset, **{k: v for k, v in data.items()
                           if k in keys and v not in (None, "")}}
    for k, v in old.items():
        if k in keys and (k not in merged or merged.get(k) in (None, "", {}, [])):
            merged[k] = v
    # 字典类字段整体替换，避免"预设的 map 与用户的 map 混在一起"这种半成品
    for k in ("fields", "map"):
        if isinstance(data.get(k), dict) and data[k]:
            merged[k] = data[k]
    return merged


def save_profile(data: dict) -> dict:
    preset = _preset_of(data)
    base = str(data.get("base") or preset.get("base") or "").strip().rstrip("/")
    path = str(data.get("path") or preset.get("path") or "").strip()
    if not base.startswith(("http://", "https://")):
        raise ValueError("接口地址需以 http:// 或 https:// 开头")
    if not path:
        raise ValueError("请求路径不能为空")
    key = str(data.get("key") or "").strip()
    state = _load_raw()[0]
    pid = str(data.get("id") or "").strip()
    existing = next((p for p in state["profiles"] if str(p.get("id") or "") == pid), None) \
        if pid else None
    if existing and not key:
        key = str(existing.get("key") or "")
    if not key:
        raise ValueError("API Key 不能为空；编辑已有配置时留空表示沿用原密钥")
    merged = _merge_profile(data, existing)
    merged.update({"base": base, "path": path, "key": key})
    merged["name"] = str(data.get("name") or merged.get("name") or "工商接口")[:60]
    old_preset = str((existing or {}).get("preset") or "")
    merged["preset"] = str(data.get("preset") or old_preset or "custom")
    merged["timeout"] = _timeout_of(data.get("timeout") or (existing or {}).get("timeout"), 8.0)
    if existing:
        existing.update(merged)
        prof = existing
    else:
        prof = {"id": "R" + time.strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:4], **merged}
        state["profiles"].append(prof)
    _save({"profiles": state["profiles"], "active": prof["id"]})
    return public()


def activate(pid: str) -> dict:
    state = _load_raw()[0]
    pid = (pid or "").strip()
    prof = next((p for p in state["profiles"] if str(p.get("id") or "") == pid), None)
    if not prof:
        raise ValueError("配置不存在，请刷新后重试")
    if not (prof.get("base") and prof.get("path") and prof.get("key")):
        raise ValueError("该配置缺少地址、路径或密钥，请先补全")
    _save({"profiles": state["profiles"], "active": pid})
    return public()


def delete_profile(pid: str) -> dict:
    state = _load_raw()[0]
    pid = (pid or "").strip()
    items = [p for p in state["profiles"] if str(p.get("id") or "") != pid]
    if len(items) == len(state["profiles"]):
        raise ValueError("配置不存在，请刷新后重试")
    active = state["active"] if state["active"] != pid else ""
    _save({"profiles": items, "active": active})
    return public()


def clear() -> dict:
    """停用：列表保留，随时可重新启用。"""
    state = _load_raw()[0]
    _save({"profiles": state["profiles"], "active": ""})
    return public()


# ---------------------------------------------------------------- 调用与归一化

def _dig(obj, path: str):
    cur = obj
    for seg in (path or "").split("."):
        if not seg:
            continue
        if isinstance(cur, list):
            try:
                cur = cur[int(seg)]
            except (ValueError, IndexError):
                return None
        elif isinstance(cur, dict):
            cur = cur.get(seg)
        else:
            return None
    return cur


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (dict, list)):
        return ""
    return str(value).strip()


def _date(value) -> str:
    """时间戳转本地日期；已是日期字符串则截到天。"""
    s = _text(value)
    if re.fullmatch(r"\d{10}|\d{13}", s):
        ts = int(s) / (1000 if len(s) == 13 else 1)
        try:
            return time.strftime("%Y-%m-%d", time.localtime(ts))
        except (OverflowError, OSError, ValueError):
            return s
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    return m.group(1) if m else s


def norm_key(name: str) -> str:
    """企业名归一化：去括号备注与空白，作为本地缓存主键。"""
    s = (name or "").strip()
    s = re.sub(r"[（(][^）)]*[）)]", "", s)
    return re.sub(r"\s+", "", s)


def _render_url(prof: dict, keyword: str, page: int, size: int) -> str:
    path = str(prof.get("path") or "")
    vals = {"{keyword}": urllib.parse.quote(keyword),
            "{page}": str(page), "{size}": str(size)}
    for k, v in vals.items():
        path = path.replace(k, v)
    url = str(prof.get("base") or "").rstrip("/") + path
    if "{keyword}" not in str(prof.get("path") or ""):
        names = prof.get("fields") or {}
        params = {
            str(names.get("keyword") or "keyword"): keyword,
            str(names.get("page") or "page"): page,
            str(names.get("size") or "size"): size,
        }
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    return url


def _headers(prof: dict) -> tuple[dict, str]:
    """返回 (请求头, 追加到 URL 的查询串)。"""
    key = str(prof.get("key") or "")
    auth = str(prof.get("auth") or "header")
    if auth == "qcc":
        secret = str(prof.get("secret") or "")
        timespan = str(int(time.time()))
        token = hashlib.md5((key + timespan + secret).encode()).hexdigest().upper()
        return {"Timespan": timespan, "Token": token}, "key=" + urllib.parse.quote(key)
    if auth == "query":
        return {}, f"{urllib.parse.quote(str(prof.get('auth_name') or 'key'))}=" \
                   f"{urllib.parse.quote(key)}"
    name = str(prof.get("auth_name") or "Authorization")
    prefix = str(prof.get("auth_prefix") or "")
    return {name: prefix + key}, ""


def _normalize(raw: dict, source: str, mapping_rules: dict) -> dict:
    row = raw if isinstance(raw, dict) else {}
    out: dict = {"source": source, "raw": row}
    for std, path in (mapping_rules or {}).items():
        if std in FIELDS:
            out[std] = _text(_dig(row, str(path)))
    out["establish_date"] = _date(out.get("establish_date") or "")
    if not out.get("name"):
        for alt in ("companyName", "entName", "CompanyName", "Name"):
            if _text(row.get(alt)):
                out["name"] = _text(row.get(alt))
                break
    return out


def _call(prof, keyword: str, page: int, size: int) -> tuple[list[dict], str, str]:
    """执行一次检索，返回 (条目, 状态, 错误)。状态：ok / empty / error。"""
    url = _render_url(prof, keyword, page, size)
    headers, extra = _headers(prof)
    if extra:
        url += ("&" if "?" in url else "?") + extra
    headers.setdefault("Accept", "application/json")
    try:
        resp = fetch(url, headers=headers, timeout=_timeout_of(prof.get("timeout")), retries=1)
        data = resp.json()
    except FetchError as e:
        return [], "error", f"网络失败：{e.reason}"
    except Exception as e:  # noqa: BLE001 —— 解析失败的原因要原样带回
        return [], "error", f"{type(e).__name__}: {str(e)[:120]}"
    if not isinstance(data, dict):
        return [], "error", "响应不是 JSON 对象"

    code = _text(_dig(data, str(prof.get("code_path") or "")))
    ok_codes = [str(x) for x in (prof.get("ok_codes") or [])]
    empty_codes = [str(x) for x in (prof.get("empty_codes") or [])]
    if code and empty_codes and code in empty_codes:
        return [], "empty", ""
    if code and ok_codes and code not in ok_codes:
        msg = _text(_dig(data, str(prof.get("msg_path") or ""))) or f"错误码 {code}"
        return [], "error", f"上游拒绝：{msg}"

    items = _dig(data, str(prof.get("items_path") or ""))
    if not isinstance(items, list):
        return [], "error", "结果路径未命中，请在设置页核对"
    out = [_normalize(x, "registry:" + str(prof.get("preset") or "custom"),
                      prof.get("map") or {})
           for x in items[:size]]
    return [x for x in out if x.get("name")], ("ok" if out else "empty"), ""


def search(keyword: str, limit: int = 8, meta: dict | None = None) -> list[dict]:
    """检索工商登记。未配置返回空并把 status 标成 disabled，调用方据此降级。"""
    stat = meta if meta is not None else {}
    stat.update({"status": "disabled", "vendor": "", "ms": 0, "total": 0, "error": ""})
    prof = active()
    if not prof or not (keyword or "").strip():
        return []
    stat["vendor"] = str(prof.get("preset") or "")
    ck = f"{prof.get('id')}|{keyword}|{limit}"
    with _cache_lock:
        hit = _cache.get(ck)
    if hit and time.time() - hit[0] < CACHE_TTL:
        stat.update({"status": "ok" if hit[1] else "empty", "total": len(hit[1])})
        return list(hit[1])
    t0 = time.time()
    items, status, err = _call(prof, keyword.strip(), 1, max(1, min(20, limit)))
    stat.update({"status": status, "ms": int((time.time() - t0) * 1000),
                 "total": len(items), "error": err})
    if items:
        for it in items:
            store.upsert_company({
                "key": norm_key(str(it.get("name"))),
                "name": it.get("name", ""),
                "credit_code": it.get("credit_code", ""),
                "legal_person": it.get("legal_person", ""),
                "reg_capital": it.get("reg_capital", ""),
                "reg_status": it.get("reg_status", ""),
                "establish_date": it.get("establish_date", ""),
                "location": it.get("location", ""),
                "website": it.get("website", ""),
                "industry": it.get("industry", ""),
                "business": it.get("business", ""),
                "source": it.get("source", ""),
                "raw": json.dumps(it.get("raw") or {}, ensure_ascii=False)[:20000],
            })
    with _cache_lock:
        _cache[ck] = (time.time(), items)
    return list(items)


def resolve(name: str) -> dict:
    """按名称取一条工商档案：先本地缓存，再远程检索同名/最相近的一条。"""
    key = norm_key(name)
    if not key:
        return {}
    local = store.get_company(key)
    if local:
        return dict(local)
    if not enabled():
        return {}
    items = search(name, limit=5)
    if not items:
        return {}
    exact = next((x for x in items if norm_key(str(x.get("name"))) == key), None)
    picked = exact or items[0]
    return dict(store.get_company(norm_key(str(picked.get("name")))) or picked)


def probe(data: dict) -> dict:
    """用页面上尚未保存的值试一次；不落盘、不改当前配置。"""
    prof = _merge_profile(data, None)
    prof["base"] = str(data.get("base") or "").strip().rstrip("/")
    prof["path"] = str(data.get("path") or "").strip()
    prof["key"] = str(data.get("key") or "").strip()
    if not (prof["base"] and prof["path"] and prof["key"]):
        raise ValueError("接口地址、请求路径与 API Key 需先填写")
    t0 = time.time()
    items, status, err = _call(prof, str(data.get("keyword") or "科技"), 1, 3)
    sample = items[0] if items else {}
    hit = [FIELDS[k] for k in FIELDS if sample.get(k)]
    return {
        "ok": status != "error",
        "status": status,
        "ms": int((time.time() - t0) * 1000),
        "count": len(items),
        "fields_hit": hit,
        "sample": {k: sample.get(k, "") for k in FIELDS},
        "error": err,
    }


def warmup(names: list[str], workers: int = 2) -> int:
    """批量补档：对监测名单里还没有工商记录的名称并发查询一次。"""
    todo = [n for n in names if n.strip() and not store.get_company(norm_key(n))]
    if not todo or not enabled():
        return 0
    with ThreadPoolExecutor(max_workers=max(1, min(4, workers))) as pool:
        list(pool.map(lambda n: resolve(n), todo))
    return len(todo)


__all__ = ["PRESETS", "FIELDS", "active", "clear", "delete_profile", "enabled",
           "activate", "norm_key", "probe", "public", "resolve",
           "save_profile", "search", "warmup"]