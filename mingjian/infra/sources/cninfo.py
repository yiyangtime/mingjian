"""
巨潮资讯（证监会指定信息披露网站）适配器。

两类调用：
  resolve()         公司名/代码 → 标准实体（代码、orgId、简称）
  announcements()   实体 → 一段时间内的全部公告（标题、日期、PDF 链接）

公告是这套系统里可信度最高的一类材料：有明确发文主体、明确日期、可下载原文。
因此证据等级直接给 full（拿到 PDF 正文时）或 title（仅标题）。
"""
from __future__ import annotations

import json
import re
import time
from urllib.parse import urljoin

from ...core import net
from ...core.textproc import clip

TOP_SEARCH = "http://www.cninfo.com.cn/new/information/topSearch/query"
ANN_QUERY = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
STATIC = "http://static.cninfo.com.cn/"

_HEADERS = {
    "Referer": "http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice",
    "X-Requested-With": "XMLHttpRequest",
}

# 定期报告在巨潮的分类标识与法定披露窗口（年报为次年 1—6 月）
_REPORT_CAT = {
    "一季报": "category_yjdbg_szsh",
    "中报": "category_bndbg_szsh",
    "三季报": "category_sjdbg_szsh",
    "年报": "category_ndbg_szsh",
}
_REPORT_WINDOW = {
    "一季报": ("04-01", "06-30"),
    "中报": ("07-01", "09-30"),
    "三季报": ("10-01", "12-31"),
    "年报": ("01-01", "06-30"),
}
_REPORT_NAME_RE = re.compile(r"(\d{4})(一季报|中报|三季报|年报)")
# 摘要、英文版、更正说明都不是可引用的原文
_REPORT_SKIP = ("摘要", "英文", "取消", "更正", "补充", "说明", "问询", "已取消")


def column_of(code: str) -> str:
    """按代码判断交易所板块，决定查询用的 column。"""
    code = (code or "").strip()
    if len(code) == 5:
        return "hke"
    if code.startswith(("60", "68", "90")):
        return "sse"
    if code.startswith(("43", "83", "87", "88", "92")):
        return "bj"
    return "szse"


_RESOLVE_TTL = 6 * 3600      # 代码与 orgId 不会变，成功结果复用到 6 小时
_RESOLVE_FAIL_TTL = 120      # 解析失败只留 2 分钟，网络抖动恢复后能立刻重试
_SEARCH_TTL = 300            # 搜索联想直接反映输入词，缓存短一些
_resolved: dict[str, tuple[float, dict | None, int]] = {}
_search_cache: dict[str, tuple[float, list[dict]]] = {}


def resolve(keyword: str) -> dict | None:
    """公司名或代码 → 实体。优先 A 股，其次港股。

    带缓存：每次页面请求都解析一遍没有意义，代码不会变。
    """
    kw = (keyword or "").strip()
    if not kw:
        return None
    hit = _resolved.get(kw)
    if hit and time.time() - hit[0] < hit[2]:
        return dict(hit[1]) if hit[1] else None
    val = _resolve_raw(kw)
    _resolved[kw] = (time.time(), val, _RESOLVE_TTL if val else _RESOLVE_FAIL_TTL)
    return dict(val) if val else None


def _resolve_raw(kw: str) -> dict | None:
    try:
        raw = net.post_form(TOP_SEARCH, {"keyWord": kw, "maxNum": "10"},
                            headers=_HEADERS, referer="http://www.cninfo.com.cn/")
        arr = json.loads(raw)
    except Exception:
        return None
    if not isinstance(arr, list) or not arr:
        return None

    def score(item: dict) -> tuple:
        cat = item.get("category") or ""
        name = item.get("zwjc") or ""
        return (
            0 if "A股" in cat else 1,
            0 if name == kw else 1,
            0 if "退市" not in str(item.get("delisted")) else 1,
        )

    arr = sorted(arr, key=score)
    it = arr[0]
    code = str(it.get("code") or "")
    return {
        "short_name": it.get("zwjc") or kw,
        "code": code,
        "org_id": it.get("orgId") or "",
        "market": it.get("category") or "",
        "column": column_of(code),
        "resolved_by": "cninfo.topSearch",
    }


def search(keyword: str, limit: int = 8) -> list[dict]:
    """公司搜索联想：返回候选列表，供「新增监测对象」等场景选择。

    只做检索不到就返回空列表，不抛异常；上游不可用不应阻断页面。
    """
    kw = (keyword or "").strip()
    if not kw:
        return []
    limit = max(1, min(20, int(limit or 8)))
    hit = _search_cache.get(kw)
    if hit and time.time() - hit[0] < _SEARCH_TTL:
        return [dict(x) for x in hit[1][:limit]]
    try:
        raw = net.post_form(TOP_SEARCH, {"keyWord": kw, "maxNum": "10"},
                            headers=_HEADERS, referer="http://www.cninfo.com.cn/")
        arr = json.loads(raw)
    except Exception:
        return []
    if not isinstance(arr, list):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    # 同一家公司可能同时有 A 股与 H 股：优先进 A 股，按简称去重
    arr = sorted(arr, key=lambda x: 0 if (isinstance(x, dict)
                                         and "A股" in str(x.get("category") or "")) else 1)
    for it in arr:
        if not isinstance(it, dict):
            continue
        code = str(it.get("code") or "")
        name = str(it.get("zwjc") or code).strip()
        if not code or not name or name in seen:
            continue
        seen.add(name)
        out.append({
            "name": name,
            "code": code,
            "market": it.get("category") or "",
            "org_id": it.get("orgId") or "",
            "column": column_of(code),
            "delisted": bool(it.get("delisted")),
        })
        if len(out) >= 20:
            break
    _search_cache[kw] = (time.time(), out)
    return [dict(x) for x in out[:limit]]


def announcements(code: str, org_id: str, column: str, days: int = 30,
                  page_size: int = 40, keyword: str = "") -> list[dict]:
    """拉取公告列表。返回统一结构，正文留空（由上层决定是否抓 PDF）。"""
    now = time.localtime()
    end = time.strftime("%Y-%m-%d", now)
    start = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
    out: list[dict] = []
    page = 1
    while len(out) < page_size and page <= 3:
        fields = {
            "pageNum": str(page), "pageSize": "30", "column": column, "tabName": "fulltext",
            "plate": "", "stock": f"{code},{org_id}", "searchkey": keyword, "secid": "",
            "category": "", "trade": "", "seDate": f"{start}~{end}",
            "sortName": "", "sortType": "", "isHLtitle": "true",
        }
        try:
            raw = net.post_form(ANN_QUERY, fields, headers=_HEADERS,
                                referer="http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice")
            data = json.loads(raw)
        except Exception:
            break
        items = (data or {}).get("announcements") or []
        if not items:
            break
        for it in items:
            title = (it.get("announcementTitle") or "").replace("<em>", "").replace("</em>", "")
            title = title.strip()
            if not title:
                continue
            ts = it.get("announcementTime")
            pub = time.strftime("%Y-%m-%d", time.localtime(ts / 1000)) if ts else ""
            adj = it.get("adjunctUrl") or ""
            out.append({
                "kind": "announcement",
                "title": clip(title, 200),
                "url": urljoin(STATIC, adj) if adj else "",
                "source": "巨潮资讯",
                "published_at": pub,
                "body": "",
                "evidence_level": "title",
                "extra": {
                    "announcement_id": it.get("announcementId"),
                    "type": it.get("adjunctType") or "",
                    "size_kb": it.get("adjunctSize"),
                    "page_column": it.get("pageColumn"),
                    "code": it.get("secCode"),
                },
            })
            if len(out) >= page_size:
                break
        total = (data or {}).get("totalAnnouncement") or 0
        if len(out) >= min(total, page_size):
            break
        page += 1
    return out


def report_pdf(code: str, org_id: str, column: str, period_name: str) -> dict:
    """定位某个报告期的定期报告原文 PDF。

    period_name 形如「2026三季报」。做法是用巨潮的分类 + 法定披露窗口收窄，
    只在该报告期对应的披露月份里找，避免翻全年公告。
    定位不到返回 {} —— 宁可没有链接，也不给一个错的。
    """
    m = _REPORT_NAME_RE.search(period_name or "")
    if not m or not code or not org_id:
        return {}
    year, kind = int(m.group(1)), m.group(2)
    cat = _REPORT_CAT.get(kind)
    win = _REPORT_WINDOW.get(kind)
    if not cat or not win:
        return {}
    # 年报于次年披露，其余在当年
    y = year + 1 if kind == "年报" else year
    fields = {
        "pageNum": "1", "pageSize": "30", "column": column, "tabName": "fulltext",
        "plate": "", "stock": f"{code},{org_id}", "searchkey": "",
        "secid": "", "category": cat, "trade": "", "seDate": f"{y}-{win[0]}~{y}-{win[1]}",
        "sortName": "", "sortType": "", "isHLtitle": "true",
    }
    try:
        raw = net.post_form(ANN_QUERY, fields, headers=_HEADERS,
                            referer="http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice")
        items = (json.loads(raw) or {}).get("announcements") or []
    except Exception:
        return {}

    for it in items:
        title = (it.get("announcementTitle") or "").replace("<em>", "").replace("</em>", "").strip()
        adj = it.get("adjunctUrl") or ""
        if not title or not adj:
            continue
        if any(x in title for x in _REPORT_SKIP):
            continue
        if kind in ("年报",) and "年度报告" not in title:
            continue
        if kind == "中报" and "半年度报告" not in title:
            continue
        if kind in ("一季报", "三季报") and "季度报告" not in title:
            continue
        ts = it.get("announcementTime")
        pub = time.strftime("%Y-%m-%d", time.localtime(ts / 1000)) if ts else ""
        return {"url": urljoin(STATIC, adj), "title": clip(title, 120),
                "date": pub, "period": period_name, "source": "巨潮资讯 · 定期报告原文"}
    return {}
