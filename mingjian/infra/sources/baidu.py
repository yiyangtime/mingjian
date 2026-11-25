"""
百度资讯适配器：东方财富检索失败时的回退源。

为什么需要它：
  非上市企业不在证券主体库里，只能从公开资讯反查；东方财富搜索在部分
  网络会被连接级风控（HTTP 200，但只回股民资料、没有文章列表）。这时
  再有一个公开资讯入口，搜索与线索发现才不至于整条路断掉。

边界：
  · 只取搜索结果首屏（约 10 条）。搜索页的后续页由前端脚本加载，静态
    请求拿不到，故 `page > 1` 直接返回空，不假装能翻页；
  · 摘要是搜索页自带的片段，可能被截断，只能算 snippet 级证据；正文仍由
    上层按链接抓取；
  · 不登录、不绕验证码。命中安全验证时按上游失败返回，不重试、不伪造。
"""
from __future__ import annotations

import html as htmllib
import re
import time
import urllib.parse
from datetime import datetime, timedelta

from ...core import net
from ...core.textproc import clip

SEARCH_API = "https://news.baidu.com/ns"

_HEADERS = {
    "Referer": "https://news.baidu.com/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_H3 = re.compile(r"<h3[^>]*>(.*?)</h3>", re.S)
_A = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_TITLE_ARIA = re.compile(r'aria-label="标题：([^"]+)"')
_DATE = re.compile(r'aria-label="发布于：([^"]+)"')
_SOURCE = re.compile(r'aria-label="新闻来源：([^"]+)"')
_SUMMARY = re.compile(r'aria-label="摘要\s*(.*?)摘要结束', re.S)
_TAG = re.compile(r"<[^>]+>")
_COMMENT = re.compile(r"<!--.*?-->")
_WS = re.compile(r"\s+")

_CACHE_TTL = 300
_cache: dict[str, tuple[float, list[dict]]] = {}


def _text(s: str) -> str:
    """去标签、去注释、反转义，并压成一行。"""
    s = _COMMENT.sub("", s or "")
    s = _TAG.sub("", s)
    s = htmllib.unescape(s)
    return _WS.sub(" ", s).strip()


def _clean_url(u: str) -> str:
    return htmllib.unescape(u or "").strip()


def _date(raw: str, now: datetime | None = None) -> str:
    """把「昨天22:11 / 3小时前 / 11月24日」归一成可排序的日期时间。

    解析不了就原样保留，不猜。归一后统一为 `YYYY-MM-DD HH:MM` 或
    `YYYY-MM-DD`，下游按 `[:10]` 取日期仍然成立。
    """
    raw = (raw or "").strip()
    if not raw:
        return ""
    now = now or datetime.now()

    m = re.match(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?"
                 r"(?:\s+(\d{1,2}):(\d{2}))?", raw)
    if m:
        y, mo, d, hh, mm = m.groups()
        return _fmt(int(y), int(mo), int(d), hh, mm)

    m = re.match(r"(今天|昨天|前天)(?:\s*(\d{1,2}):(\d{2}))?", raw)
    if m:
        delta = {"今天": 0, "昨天": 1, "前天": 2}[m.group(1)]
        t = now - timedelta(days=delta)
        return _fmt(t.year, t.month, t.day, m.group(2), m.group(3))

    m = re.match(r"(\d+)\s*天前", raw)
    if m:
        t = now - timedelta(days=int(m.group(1)))
        return t.strftime("%Y-%m-%d")
    m = re.match(r"(\d+)\s*分钟前", raw)
    if m:
        t = now - timedelta(minutes=int(m.group(1)))
        return t.strftime("%Y-%m-%d %H:%M")
    m = re.match(r"(\d+)\s*小时前", raw)
    if m:
        t = now - timedelta(hours=int(m.group(1)))
        return t.strftime("%Y-%m-%d %H:%M")
    if raw == "刚刚":
        return now.strftime("%Y-%m-%d %H:%M")

    m = re.match(r"(\d{1,2})月(\d{1,2})日(?:\s*(\d{1,2}):(\d{2}))?", raw)
    if m:
        mo, d, hh, mm = m.groups()
        year = now.year
        try:
            candidate = datetime(year, int(mo), int(d))
            # 年底看到「1月」多半是上一年，不是明年
            if candidate.date() > (now + timedelta(days=1)).date():
                year -= 1
        except ValueError:
            return raw
        return _fmt(year, int(mo), int(d), hh, mm)

    return raw[:19]


def _fmt(year: int, month: int, day: int, hh: str | None, mm: str | None) -> str:
    base = f"{year:04d}-{month:02d}-{day:02d}"
    if hh is None:
        return base
    return f"{base} {int(hh):02d}:{int(mm or 0):02d}"


def _parse(raw: str, limit: int) -> list[dict]:
    marks = list(_H3.finditer(raw))
    out: list[dict] = []
    seen: set[str] = set()
    for i, mark in enumerate(marks):
        a = _A.search(mark.group(1))
        if not a:
            continue
        url = _clean_url(a.group(1))
        if not url or url in seen:
            continue
        title = _text(a.group(2))
        aria = _TITLE_ARIA.search(mark.group(1))
        if aria and len(aria.group(1).strip()) > len(title):
            title = _text(aria.group(1))
        if not title:
            continue
        seen.add(url)
        end = marks[i + 1].start() if i + 1 < len(marks) else min(len(raw), mark.end() + 3000)
        chunk = raw[mark.end():end]
        dm, sm, mm = _DATE.search(chunk), _SOURCE.search(chunk), _SUMMARY.search(chunk)
        out.append({
            "kind": "news",
            "title": clip(title, 200),
            "url": url,
            "source": _text(sm.group(1)) if sm else "百度资讯",
            "published_at": _date(dm.group(1)) if dm else "",
            "body": clip(_text(mm.group(1)), 500) if mm else "",
            "evidence_level": "snippet",
            "extra": {"engine": "baidu"},
        })
        if len(out) >= limit:
            break
    return out


def _done(meta: dict | None, stat: dict, started: float, out: list[dict]) -> list[dict]:
    stat["ms"] = int((time.perf_counter() - started) * 1000)
    if meta is not None:
        meta.update(stat)
    return out


def _err_text(e: Exception) -> str:
    code = getattr(e, "status", None)
    name = type(e).__name__
    if code:
        return f"{name} {code}"
    text = str(e) or ""
    return f"{name}: {text[:80]}" if text else name


def search(keyword: str, page: int = 1, size: int = 10,
           meta: dict | None = None) -> list[dict]:
    """按时间检索资讯；失败返回空列表，不抛异常。

    `meta` 写入 `status`（ok / empty / error）、`error`、`ms`、`raw` 与
    `via=baidu`。只提供首屏，`page > 1` 记 empty。
    """
    stat = {"status": "ok", "error": "", "ms": 0, "raw": 0, "via": "baidu"}
    started = time.perf_counter()
    kw = (keyword or "").strip()
    if not kw:
        stat["status"] = "empty"
        return _done(meta, stat, started, [])
    if page > 1:
        stat["status"] = "empty"
        stat["error"] = "该源只提供首屏"
        return _done(meta, stat, started, [])

    size = max(10, min(20, int(size or 10)))
    key = f"{kw}|{size}"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        items = [dict(x) for x in hit[1][:size]]
        stat["raw"] = len(items)
        stat["cached"] = True
        return _done(meta, stat, started, items)

    params = urllib.parse.urlencode({
        "word": kw, "tn": "news", "from": "news", "cl": "2",
        "rn": str(size), "ct": "1",
    }, quote_via=urllib.parse.quote)
    try:
        raw = net.fetch(f"{SEARCH_API}?{params}", headers=_HEADERS).text
        if "百度安全验证" in raw or "请输入验证码" in raw:
            stat["status"] = "error"
            stat["error"] = "上游安全验证"
            return _done(meta, stat, started, [])
        items = _parse(raw, size)
        if not items:
            # 有明确“没有找到”才算空结果；否则按页面结构变化处理，
            # 免得把解析失败说成“这家公司没有资讯”。
            stat["status"] = "empty" if ("没有找到" in raw or "未找到" in raw) else "error"
            if stat["status"] == "error":
                stat["error"] = "结果页结构变化，未解析到条目"
            return _done(meta, stat, started, [])
        stat["raw"] = len(items)
        _cache[key] = (time.time(), items)
        return _done(meta, stat, started, items)
    except Exception as e:
        stat["status"] = "error"
        stat["error"] = _err_text(e)
        return _done(meta, stat, started, [])
