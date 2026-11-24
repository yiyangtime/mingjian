"""
东方财富适配器：公司档案、资讯检索、行情。

为什么选它而不是通用搜索引擎：
  1) 检索结果是"按时间排序的公司相关资讯"，天然带发布时间和媒体名；
  2) 有结构化摘要，正文抓取失败时仍可作为 snippet 级证据；
  3) 公司档案给出全称/行业/主营，是公司画像与行业判断的输入。
"""
from __future__ import annotations

import json
import re
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


def search(keyword: str, page: int = 1, size: int = 10) -> list[dict]:
    """按时间倒序检索资讯。失败返回空列表，不抛异常。"""
    param = {
        "uid": "", "keyword": keyword, "type": ["cmsArticleWebOld"],
        "client": "web", "clientType": "web", "clientVersion": "curr",
        "param": {"cmsArticleWebOld": {
            "searchScope": "default", "sort": "time", "pageIndex": page,
            "pageSize": size, "preTag": "<em>", "postTag": "</em>",
        }},
    }
    url = f"{SEARCH_API}?cb=cb&param=" + urllib.parse.quote(json.dumps(param, ensure_ascii=False))
    try:
        raw = net.fetch(url, headers=_EM_HEADERS).text
        m = re.search(r"cb\((.*)\)\s*$", raw, re.S)
        data = json.loads(m.group(1)) if m else {}
        items = ((data.get("result") or {}).get("cmsArticleWebOld")) or []
    except Exception:
        return []
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
    return out


def news_bundle(name: str, short: str, days: int = 30) -> list[dict]:
    """
    多路检索合并去重。第一路是公司名（覆盖全部近期资讯），
    后几路按事件类型补采，避免"只搜公司名"漏掉行业政策类信息。
    """
    queries = [name]
    for suffix in ("扩产 产能", "订单 合作 中标", "业绩 财报", "价格 降价"):
        queries.append(f"{name} {suffix}")

    seen_title: list[str] = []
    out: list[dict] = []
    for qi, q in enumerate(queries):
        pages = CFG.news_pages if qi == 0 else 1
        for p in range(1, pages + 1):
            items = search(q, page=p, size=10)
            if not items:
                break
            for it in items:
                key = re.sub(r"\s+", "", it["title"])
                if key in seen_title:
                    continue
                if not _relevant(it, short, name):
                    continue
                seen_title.append(key)
                it["extra"]["query"] = q
                out.append(it)
            if len(out) >= CFG.max_news:
                return out[:CFG.max_news]
    return out[:CFG.max_news]


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
