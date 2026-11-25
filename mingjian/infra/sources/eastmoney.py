"""
东方财富适配器：公司档案、资讯检索、行情。

为什么选它而不是通用搜索引擎：
  1) 检索结果是"按时间排序的公司相关资讯"，天然带发布时间和媒体名；
  2) 有结构化摘要，正文抓取失败时仍可作为 snippet 级证据；
  3) 公司档案给出全称/行业/主营，是公司画像与行业判断的输入。

资讯检索的入口是 `search`；需要“主源失败时回退”时用 `search_any`，
它只在东财失败时转百度资讯首屏，主源正常空结果不换源。
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse

from ...config import CFG
from ...core import net
from ...core.textproc import clip

SEARCH_API = "https://search-api-web.eastmoney.com/search/jsonp"
PROFILE_API = "https://datacenter-web.eastmoney.com/api/data/v1/get"
QUOTE_API = "https://push2.eastmoney.com/api/qt/stock/get"

_EM_HEADERS = {"Referer": "https://so.eastmoney.com/"}


def secucode(code: str, market: str = "") -> str:
    c = (code or "").strip()
    if len(c) == 5:  # 港股
        return f"{c}.HK"
    if c.startswith(("60", "68", "90")):
        return f"{c}.SH"
    if c.startswith(("43", "83", "87", "88", "92")):
        return f"{c}.BJ"
    return f"{c}.SZ"


def secid(code: str) -> str:
    c = (code or "").strip()
    return f"1.{c}" if c.startswith(("60", "68", "90", "11", "5")) else f"0.{c}"


def _strip(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()


def search(keyword: str, page: int = 1, size: int = 10,
           meta: dict | None = None) -> list[dict]:
    """按时间倒序检索资讯。失败返回空列表，不抛异常。

    `meta` 传入空 dict 时写入调用结果：`status`（ok / empty / error）、
    `error`（失败原因，含 HTTP 状态码）与 `ms`（耗时）。不传时行为不变，
    供只关心条目的调用方使用。
    """
    param = {
        "uid": "", "keyword": keyword, "type": ["cmsArticleWebOld"],
        "client": "web", "clientType": "web", "clientVersion": "curr",
        "param": {"cmsArticleWebOld": {
            "searchScope": "default", "sort": "time", "pageIndex": page,
            "pageSize": size, "preTag": "<em>", "postTag": "</em>",
        }},
    }
    url = f"{SEARCH_API}?cb=cb&param=" + urllib.parse.quote(json.dumps(param, ensure_ascii=False))
    stat = {"status": "ok", "error": "", "ms": 0, "raw": 0}
    started = time.perf_counter()
    try:
        raw = net.fetch(url, headers=_EM_HEADERS).text
        raw = raw.lstrip("\ufeff")
        # 回调名由上游决定：过去是 cb，实测会换成 jQuery3510..._1693...；
        # 只认 cb( 会把正常响应误判成“非 JSONP”。这里只校验“函数名(JSON)”。
        m = re.match(r"^\s*[\w$.]+\s*\((.*)\)\s*;?\s*$", raw, re.S)
        if not m:
            stat["status"] = "error"
            stat["error"] = "非 JSONP 响应"
            return _search_done(meta, stat, started, [])
        data = json.loads(m.group(1))
        result = data.get("result") or {}
        items = result.get("cmsArticleWebOld") or []
        if "cmsArticleWebOld" not in result:
            # 上游风控时仍返回 200，但 result 里只有 passportWeb（股民资料），
            # 没有文章列表；把它记成失败，不能当成“这家公司没有资讯”。
            # result 为空对象则按“确实没有结果”处理，不误报成风控。
            if "passportWeb" in result:
                stat["status"] = "error"
                stat["error"] = "上游风控：仅返回股民资料"
                return _search_done(meta, stat, started, [])
            if result:
                stat["status"] = "error"
                stat["error"] = "上游返回缺少文章列表"
                return _search_done(meta, stat, started, [])
        stat["raw"] = len(items)
    except Exception as e:
        stat["status"] = "error"
        stat["error"] = _err_text(e)
        return _search_done(meta, stat, started, [])
    out = []
    for it in items:
        title = _strip(it.get("title"))
        if not title:
            continue
        out.append({
            "kind": "news",
            "title": clip(title, 200),
            "url": it.get("url") or "",
            "source": it.get("mediaName") or "东方财富",
            "published_at": (it.get("date") or "")[:19],
            "body": _strip(it.get("content")),
            "evidence_level": "snippet",
            "extra": {"em_code": it.get("code")},
        })
    if not out:
        # 接口正常但确实没有结果；与 error 分开，才能判断“真没资讯”
        stat["status"] = "empty"
    return _search_done(meta, stat, started, out)


def search_any(keyword: str, page: int = 1, size: int = 10,
               meta: dict | None = None) -> list[dict]:
    """主源失败时回退到百度资讯首屏。

    只在东方财富检索**失败**（网络错误、风控、响应异常）时回退；主源正常
    返回空结果时不改口径，避免同一查询在不同源之间悄悄换口径。
    回退命中时 `meta["via"]` 为 `baidu`，并保留 `fallback_from` 说明主源
    为什么没用上。
    """
    stat = meta if meta is not None else {}
    items = search(keyword, page=page, size=size, meta=stat)
    if items or page > 1 or stat.get("status") != "error":
        return items
    try:
        from . import baidu
        backup: dict = {}
        items = baidu.search(keyword, page=1, size=size, meta=backup)
    except Exception:
        return []
    if not items:
        return []
    if meta is not None:
        primary = str(stat.get("error") or "检索失败")
        meta.update(backup)
        meta["via"] = "baidu"
        meta["fallback_from"] = f"eastmoney: {primary}"
    for it in items:
        it.setdefault("extra", {})["via"] = "baidu"
    return items


def _search_done(meta, stat: dict, started: float, out: list[dict]) -> list[dict]:
    stat["ms"] = int((time.perf_counter() - started) * 1000)
    if meta is not None:
        meta.update(stat)
    return out


def _err_text(e: Exception) -> str:
    """把异常压成一行：优先带 HTTP 状态码，其次是类型与首段原因。"""
    code = getattr(e, "status", None)
    name = type(e).__name__
    if code:
        return f"{name} {code}"
    text = str(e) or ""
    return f"{name}: {text[:80]}" if text else name


# 内置检索词基线：第一路公司名负责覆盖，后几路按事件补采。
# 运行期由上层传入版本化检索词；默认值留在 infra，避免反向依赖 services。
DEFAULT_TERMS: tuple[str, ...] = ("扩产 产能", "订单 合作 中标", "业绩 财报", "价格 降价")


def queries_for(name: str, terms: list[str] | tuple[str, ...] | None = None) -> list[str]:
    """公司名一路 + 每个检索词一路；空名、空词与重复查询丢弃。"""
    base = str(name or "").strip()
    if not base:
        return []
    out = [base]
    for term in (terms or DEFAULT_TERMS):
        t = " ".join(str(term or "").split())
        q = f"{base} {t}"
        if t and q not in out:
            out.append(q)
    return out


def news_bundle(name: str, short: str, days: int = 30,
                terms: list[str] | tuple[str, ...] | None = None,
                trace: dict | None = None) -> list[dict]:
    """
    多路检索合并去重。第一路是公司名（覆盖全部近期资讯），
    后几路按事件类型补采，避免"只搜公司名"漏掉行业政策类信息。
    `terms` 是版本化的检索词；不传时用内置基线。
    `trace` 传入空 dict 时写入采集留痕：每路查询的候选 / 入围 / 重复 /
    不相关条数，以及被丢弃条目的标题与原因。只记元数据，不存正文。
    每路先走东方财富；主源失败时回退百度资讯首屏，并在 trace 的 `via`
    字段标出实际来源。
    """
    queries = queries_for(name, terms)
    if trace is not None:
        trace["queries"] = []
        trace["rejected"] = []

    seen_title: list[str] = []
    out: list[dict] = []
    for qi, q in enumerate(queries):
        stat = {"query": q, "raw": 0, "kept": 0, "duplicate": 0, "irrelevant": 0,
                "status": "ok", "error": "", "ms": 0, "via": "eastmoney"}
        pages = CFG.news_pages if qi == 0 else 1
        for p in range(1, pages + 1):
            meta: dict = {}
            items = search_any(q, page=p, size=10, meta=meta)
            stat["ms"] += int(meta.get("ms") or 0)
            if meta.get("via"):
                stat["via"] = meta["via"]
            if meta.get("fallback_from"):
                stat["fallback_from"] = meta["fallback_from"]
            if meta.get("status") == "error":
                stat["status"] = "error"
                stat["error"] = str(meta.get("error") or "检索失败")
                break
            if not items:
                break
            for it in items:
                stat["raw"] += 1
                key = re.sub(r"\s+", "", it["title"])
                if key in seen_title:
                    stat["duplicate"] += 1
                    _trace_reject(trace, q, it, "duplicate")
                    continue
                if not _relevant(it, short, name):
                    stat["irrelevant"] += 1
                    _trace_reject(trace, q, it, "irrelevant")
                    continue
                seen_title.append(key)
                stat["kept"] += 1
                it["extra"]["query"] = q
                out.append(it)
            if len(out) >= CFG.max_news:
                break
            if meta.get("via") == "baidu":
                # 备用源只有首屏；继续翻页只会把已成功的这一路标成失败
                break
        if stat["status"] != "error" and stat["raw"] == 0:
            # 接口正常但一条未返回；与检索失败区分开
            stat["status"] = "empty"
        if trace is not None:
            trace["queries"].append(stat)
        if len(out) >= CFG.max_news:
            break
    return out[:CFG.max_news]


def _trace_reject(trace, query: str, item: dict, reason: str) -> None:
    """被丢弃条目的轻量留痕：只有标题、链接与拒因，不含正文。"""
    if trace is None:
        return
    trace["rejected"].append({
        "query": query,
        "title": clip(str(item.get("title") or ""), 200),
        "url": item.get("url") or "",
        "source": item.get("source") or "",
        "published_at": item.get("published_at") or "",
        "reason": reason,
    })


def _relevant(item: dict, short: str, full: str) -> bool:
    blob = (item.get("title") or "") + (item.get("body") or "")
    names = {n for n in (short, full) if n}
    return any(n in blob for n in names)


def profile(code: str, market: str = "") -> dict:
    """公司档案：全称、行业、主营业务、注册地、成立日期。"""
    sc = secucode(code, market)
    url = (PROFILE_API + "?reportName=RPT_F10_BASIC_ORGINFO&columns=ALL&filter="
           + urllib.parse.quote(f'(SECUCODE="{sc}")') + "&pageNumber=1&pageSize=5&source=HSF10&client=PC")
    try:
        d = net.fetch(url, headers=_EM_HEADERS).json()
        rows = ((d.get("result") or {}).get("data")) or []
    except Exception:
        rows = []
    if not rows:
        return {}
    r = rows[0]
    return {
        "full_name": r.get("ORG_NAME") or "",
        "short_name": r.get("SECURITY_NAME_ABBR") or "",
        "industry": r.get("EM2016") or r.get("INDUSTRYCSRC1") or "",
        "industry_csrc": r.get("INDUSTRYCSRC1") or "",
        "main_business": (r.get("MAIN_BUSINESS") or "").replace(",", "，"),
        "profile": (r.get("ORG_PROFILE") or "").strip(),
        "address": r.get("REG_ADDRESS") or "",
        "website": (r.get("ORG_WEB") or "").strip(),
        "founded": (r.get("FOUND_DATE") or "")[:10],
    }


def quote(code: str) -> dict:
    """行情快照：现价、涨跌幅、总市值、流通市值、PE、PB。"""
    url = (QUOTE_API + f"?secid={secid(code)}"
           "&fields=f43,f57,f58,f60,f116,f117,f162,f167,f169,f170")
    try:
        d = net.fetch(url, headers=_EM_HEADERS).json().get("data") or {}
        return {
            "price": (d.get("f43") or 0) / 100,
            "prev_close": (d.get("f60") or 0) / 100,
            "market_cap": d.get("f116") or 0,
            "float_cap": d.get("f117") or 0,
            "pe": (d.get("f162") or 0) / 100,
            "pb": (d.get("f167") or 0) / 100,
        }
    except Exception:
        return {}
