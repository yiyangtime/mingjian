"""
经营指标联动：把公开财报指标做成可下钻的看板，并接上「口径 / 血缘 / 关联情报 / 场景化取数」。

数据全部来自公开定期报告（经东财数据接口）。这里刻意不做两件事：
  · 不编造企业内部指标（产能利用率、单台均价这类必须甲方给数），缺就是缺；
  · 不开放自由 SQL，只允许「已定义指标 × 报告期」的白名单取数（对应需求 C-04）。

对甲方交付时，把 INDICATORS 换成财务口径签字确认的指标表即可，其余逻辑不变。
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse

from ..core import net
from ..core.log import get as _get_logger

LOG = _get_logger("metrics")

FIN_API = "https://datacenter.eastmoney.com/securities/api/data/v1/get"

# ---------------------------------------------------------------- 取数缓存
# 网络取数是主要耗时（实测单次 0.8—1.2 秒，一次页面要看两三个接口）。
# 只缓存网络结果，不缓存组装结果——关联情报这类依赖 facts 的部分必须保持实时。
_TTL_OK = 1800      # 取数成功：30 分钟
_TTL_FAIL = 120     # 取数失败：2 分钟，既不反复打不通的接口，恢复后也能很快反映
_cache: dict[tuple, tuple[float, object, int]] = {}


def _cache_get(key):
    hit = _cache.get(key)
    if not hit:
        return None
    ts, val, ttl = hit
    if time.time() - ts > ttl:
        _cache.pop(key, None)
        return None
    return val


def _cache_put(key, val, ok: bool = True) -> None:
    _cache[key] = (time.time(), val, _TTL_OK if ok else _TTL_FAIL)


def clear_cache() -> int:
    """清空取数缓存，返回清掉的条目数。refresh=1 时调用。"""
    n = len(_cache)
    _cache.clear()
    return n

# 已定义的指标白名单。key 用于取数与前端引用，field 对应数据接口字段。
INDICATORS = [
    {"k": "revenue", "name": "营业收入", "field": "TOTALOPERATEREVE", "yoy": "TOTALOPERATEREVETZ",
     "unit": "亿元", "scale": 1e8, "better": "up", "digits": 2,
     "caliber": "合并利润表营业收入，含主营业务收入与其他业务收入，已抵消内部交易；不含税。",
     "formula": "合并利润表「营业收入」本期累计数"},
    {"k": "profit", "name": "归母净利润", "field": "PARENTNETPROFIT", "yoy": "PARENTNETPROFITTZ",
     "unit": "亿元", "scale": 1e8, "better": "up", "digits": 2,
     "caliber": "归属于母公司所有者的净利润，已扣除少数股东损益；为累计数。",
     "formula": "合并利润表「归属于母公司股东的净利润」本期累计数"},
    {"k": "gm", "name": "销售毛利率", "field": "XSMLL", "yoy": None,
     "unit": "%", "scale": 1, "better": "up", "digits": 2,
     "caliber": "（营业收入 − 营业成本）÷ 营业收入，按合并报表计算，不剔除运费与股份支付。",
     "formula": "(营业收入 − 营业成本) ÷ 营业收入"},
    {"k": "gp", "name": "毛利额", "field": "MLR", "yoy": None,
     "unit": "亿元", "scale": 1e8, "better": "up", "digits": 2,
     "caliber": "营业收入 − 营业成本，合并口径；反映规模与单位盈利的合成结果。",
     "formula": "合并利润表 营业收入 − 营业成本"},
    {"k": "cash", "name": "经营活动现金流净额", "field": "NETCASH_OPERATE_PK", "yoy": None,
     "unit": "亿元", "scale": 1e8, "better": "up", "digits": 2,
     "caliber": "经营活动产生的现金流量净额，累计数；与净利润的差额反映应收、存货与票据的变化。",
     "formula": "合并现金流量表 经营活动产生的现金流量净额"},
]

PERIOD_NAMES = {"一季报": "Q1", "中报": "H1", "三季报": "Q3", "年报": "FY"}

# 指标 ←→ 事件类型 的关联规则（声明式，可在界面上说明"可人工解除"）
EVENT_LINK = {
    "revenue": ["performance", "capacity", "order"],
    "profit": ["performance", "cost"],
    "gm": ["performance", "cost", "price"],
    "gp": ["performance", "cost"],
    "cash": ["finance", "performance"],
}


def _secucode(code: str, market: str) -> str:
    code = (code or "").strip()
    if not code:
        return ""
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    if code.startswith(("4", "8")):
        return f"{code}.BJ"
    return f"{code}.SZ"


def _num(v):
    try:
        if v in (None, "", "-"):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_series(code: str, market: str, limit: int = 8) -> tuple[list[dict], str]:
    """取最近若干报告期的主要指标。返回 (按期升序的列表, 错误说明)。

    带缓存：成功结果存 30 分钟，失败结果只存 2 分钟。
    """
    key = ("series", code, market, limit)
    hit = _cache_get(key)
    if hit is not None:
        return hit
    val = _fetch_series(code, market, limit)
    _cache_put(key, val, ok=not val[1])
    return val


def _fetch_series(code: str, market: str, limit: int = 8) -> tuple[list[dict], str]:
    secucode = _secucode(code, market)
    if not secucode:
        return [], "未匹配到上市主体：非上市或名称不精确，不适用公开财报指标"
    # filter 必须整体 quote：把引号和括号留在明文里，接口会返回 400
    url = (FIN_API
           + "?reportName=RPT_F10_FINANCE_MAINFINADATA&columns=ALL&filter="
           + urllib.parse.quote(f'(SECUCODE="{secucode}")')
           + f"&pageNumber=1&pageSize={limit}"
           + "&sortColumns=REPORT_DATE&sortTypes=-1&source=HSF10&client=PC")
    try:
        raw = net.fetch(url, timeout=18, retries=2).text
        data = json.loads(raw)
    except Exception as e:
        LOG.warning("财务数据接口失败 %s：%s: %s", secucode, type(e).__name__, e)
        return [], f"财务数据接口不可用：{type(e).__name__}: {e}"
    if not data.get("success"):
        LOG.warning("财务数据接口返回失败 %s：%s", secucode, data.get("message"))
        return [], f"财务数据接口返回失败：{data.get('message') or '未知原因'}"
    rows = ((data.get("result") or {}).get("data")) or []
    out = []
    for r in rows:
        rd = (r.get("REPORT_DATE") or "")[:10]
        if not rd:
            continue
        out.append({
            "date": rd,
            "period": r.get("REPORT_TYPE") or "",
            "name": r.get("REPORT_DATE_NAME") or rd,
            "raw": r,
        })
    out.sort(key=lambda x: x["date"])
    return out, ""


_ORDER = {"一季报": 1, "中报": 2, "三季报": 3, "年报": 4}
_NAME_RE = re.compile(r"(\d{4})(一季报|中报|三季报|年报)")

MAINOP_API = FIN_API
MAINOP_TYPE = {"1": "按行业", "2": "按产品", "3": "按地区"}


def mainop(code: str, market: str = "") -> dict:
    """主营构成：按产品 / 按地区 / 按行业的收入、占比与毛利率（可跨期）。

    这是"产业结构"而非"股票行情"的口径：用来回答"哪块业务在增长、哪块在拖累毛利"。
    带缓存：成功 30 分钟，失败 2 分钟。
    """
    key = ("mainop", code, market)
    hit = _cache_get(key)
    if hit is not None:
        return hit
    val = _mainop(code, market)
    _cache_put(key, val, ok=bool(val))
    return val


def _mainop(code: str, market: str = "") -> dict:
    sc = _secucode(code, market)
    if not sc:
        return {}
    url = (MAINOP_API
           + "?reportName=RPT_F10_FN_MAINOP"
           + "&columns=SECUCODE,REPORT_DATE,MAINOP_TYPE,ITEM_NAME,MAIN_BUSINESS_INCOME,MBI_RATIO,GROSS_RPOFIT_RATIO"
           + "&filter=" + urllib.parse.quote(f'(SECUCODE="{sc}")')
           + "&pageNumber=1&pageSize=80&sortTypes=-1&sortColumns=REPORT_DATE&source=HSF10&client=PC")
    try:
        data = json.loads(net.fetch(url, headers={"Referer": "https://emweb.securities.eastmoney.com/"},
                                    timeout=18, retries=2).text)
        rows = ((data.get("result") or {}).get("data")) or []
    except Exception:
        return {}

    periods: dict[str, dict] = {}
    for r in rows:
        rd = (r.get("REPORT_DATE") or "")[:10]
        t = str(r.get("MAINOP_TYPE") or "")
        name = (r.get("ITEM_NAME") or "").strip()
        if not rd or not name or t not in MAINOP_TYPE:
            continue
        inc = _num(r.get("MAIN_BUSINESS_INCOME"))
        ratio = _num(r.get("MBI_RATIO"))
        gm = _num(r.get("GROSS_RPOFIT_RATIO"))
        p = periods.setdefault(rd, {"date": rd, "按行业": [], "按产品": [], "按地区": []})
        p[MAINOP_TYPE[t]].append({
            "name": name,
            "income": round((inc or 0) / 1e8, 2),
            "ratio": round((ratio or 0) * 100, 2),
            "gm": round(gm * 100, 2) if gm is not None else None,
        })

    if not periods:
        return {}
    dates = sorted(periods)
    latest = dates[-1]
    # 同报告期上年（半年报对半年报、年报对年报），用于看结构变化
    prev = next((d for d in reversed(dates[:-1]) if d[5:] == latest[5:]), None)
    out = {"periods": dates[-4:], "latest": periods[latest], "latest_date": latest,
           "prev": periods.get(prev) if prev else None, "prev_date": prev,
           "source": "定期报告「主营业务构成」"}
    return out


def _ym(name: str):
    m = _NAME_RE.match(name or "")
    if not m:
        return None, 0
    return int(m.group(1)), _ORDER.get(m.group(2), 0)


def _cum_map(series: list[dict], field: str) -> dict:
    """{(年, 期序): 累计值}"""
    out = {}
    for row in series:
        y, k = _ym(row["name"])
        if not y or not k:
            continue
        v = _num(row["raw"].get(field))
        if v is not None:
            out[(y, k)] = v
    return out


def _quarters(cm: dict) -> list[tuple[str, float]]:
    """把累计数差分成单季数。累计数直接画趋势会误导：年报 3620 与一季报 847 不可比。"""
    pairs = []
    for (y, k), cur in sorted(cm.items()):
        if k == 1:
            pairs.append((f"{y}一季报", cur))
            continue
        # 缺上一期就无法差分，宁可不出这个点，也不让年报累计数假装成单季
        prev = cm.get((y, k - 1))
        if prev is None:
            continue
        pairs.append((f"{y}{('一季报','中报','三季报','年报')[k - 1]}", cur - prev))
    return pairs


def _gm_quarters(series: list[dict]) -> list[tuple[str, float]]:
    """单季毛利率：由单季收入与单季成本推算，不能直接对累计毛利率做差分。"""
    rev = _cum_map(series, "TOTALOPERATEREVE")
    gm = _cum_map(series, "XSMLL")
    cost = {}
    for key, r in rev.items():
        g = gm.get(key)
        if g is None:
            continue
        cost[key] = r * (1 - g / 100.0)
    rev_q = dict(_quarters(rev))
    cost_q = dict(_quarters(cost))
    out = []
    for name, r in rev_q.items():
        c = cost_q.get(name)
        if not r or c is None:
            continue
        out.append((name, (r - c) / r * 100))
    return sorted(out, key=lambda x: (x[0][:4], _ORDER.get(x[0][4:], 0)))


# ---------------------------------------------------------------- 原文定位

_DATE_PERIOD = {"03-31": "一季报", "06-30": "中报", "09-30": "三季报", "12-31": "年报"}


def period_name_of(date_str: str) -> str:
    """2026-09-30 → 2026三季报。认不出日期口径就返回空串。"""
    d = (date_str or "")[:10]
    if len(d) < 10:
        return ""
    kind = _DATE_PERIOD.get(d[5:])
    return f"{d[:4]}{kind}" if kind else ""


def _period_report(entity: dict, period_name: str) -> dict:
    """把报告期定位到巨潮的定期报告原文 PDF。

    定位不到返回 {}，不拼一个看起来像原文的链接——没有链接比错链接好。
    """
    if not period_name:
        return {}
    code = str(entity.get("code") or "")
    if not code:
        return {}
    key = ("report", code, period_name)
    hit = _cache_get(key)
    if hit is not None:
        return hit
    org = entity.get("org_id") or ""
    col = entity.get("column") or ""
    try:
        from ..infra.sources import cninfo
        if not (org and col):
            ent = cninfo.resolve(code) or {}
            org = org or ent.get("org_id", "")
            col = col or ent.get("column", "")
        rep = cninfo.report_pdf(code, org, col, period_name)
    except Exception:
        rep = {}
    _cache_put(key, rep, ok=bool(rep))
    return rep


def build(company: str, entity: dict, facts: list[dict]) -> dict:
    """
    组装指标看板：指标值、趋势、同比、口径、血缘、关联情报。

    facts 传该主体最近一次运行的事实，用于「指标 ←→ 外部事件」的关联。
    """
    code = entity.get("code") or entity.get("entity_key") or ""
    series, err = fetch_series(code, entity.get("market", ""))

    indicators: list[dict] = []

    if series:
        latest = series[-1]
        latest_name = latest["name"]
        # 该报告期定期报告的原文 PDF，供前端把「来源」做成可点链接
        rep = _period_report(entity, latest_name)

        for ind in INDICATORS:
            cur = _num(latest["raw"].get(ind["field"]))
            if cur is None:
                continue

            # 趋势：能拆单季的拆单季；毛利率这类比率按单季重算，不跨累计数比较
            label = "单季"
            if ind["k"] == "gm":
                qs = _gm_quarters(series)
                trend = [round(v, ind["digits"]) for _, v in qs][-8:]
            elif ind["unit"] == "%":
                label = "累计（同报告期）"
                same = [(n, _num(r["raw"].get(ind["field"])))
                        for n, r in [(x["name"], x) for x in series]]
                trend = [round(v, ind["digits"]) for n, v in same
                         if v is not None and n[4:] == latest_name[4:]][-8:]
            else:
                qs = _quarters(_cum_map(series, ind["field"]))
                trend = [round(v / ind["scale"], ind["digits"]) for _, v in qs][-8:]

            # 同比：优先用接口给的同比；没有就与上年同期比
            yoy = _num(latest["raw"].get(ind["yoy"])) if ind["yoy"] else None
            if yoy is None:
                base = None
                for row in series:
                    if row["name"][4:] == latest_name[4:] and row["date"][:4] != latest["date"][:4]:
                        base = _num(row["raw"].get(ind["field"]))
                if base:
                    if ind["unit"] == "%":
                        # 毛利率 / ROE 这类本身就是百分比，同比应表达为"百分点之差"，
                        # 用相对变化率会把 22.72 与 25.31 说成 -10.24%，是口径错误。
                        yoy = cur - base
                    else:
                        yoy = (cur - base) / abs(base) * 100
            yoy = round(yoy, 2) if yoy is not None else None

            linked = [f["id"] for f in facts if f.get("type") in EVENT_LINK.get(ind["k"], [])]

            indicators.append({
                **{k: ind[k] for k in ("k", "name", "unit", "better", "digits", "caliber", "formula")},
                "val": round(cur / ind["scale"], ind["digits"]),
                "yoy": yoy,
                "mom": None,   # 累计数不构成环比，宁可不给，也不编
                "trend": trend,
                "trend_label": label,
                "period": latest_name,
                "src": f"{latest['period']} {latest_name}（{latest['date']}）",
                "url": rep.get("url", ""),
                "src_title": rep.get("title", ""),
                "owner": "上市公司定期报告 · 财务负责人签署",
                "freq": "按季度披露",
                "lineage": [
                    ["交易所定期报告（合并报表）", "法定披露"],
                    ["东财财务数据接口", "数据源"],
                    ["明鉴 指标库", "平台"],
                ],
                "events": linked[:4],
            })

    # 关联情报：真实事实，不硬凑。没有对应事件的指标就是空的。
    events = {}
    for f in facts[:24]:
        events[f["id"]] = {
            "t": f.get("statement") or "",
            "d": (f.get("published_at") or "")[:10],
            "src": f.get("source") or "",
            "kind": f.get("type_label") or "",
            "url": f.get("url") or "",
        }

    # 待接入说明：企业内部指标必须甲方给数，这里诚实列出，不编数字
    pending = [
        ("产能利用率", "需要 MES 工时统计或设备运行日志"),
        ("单台均价 / 单位成本", "需要销售订单表与成本归集单"),
        ("政策补贴确认收入", "需要政府补助台账与批文"),
        ("在手订单与交付节奏", "需要订单台账与排产计划"),
    ]

    gaps = []
    if err:
        gaps.append(err)
    elif not series:
        gaps.append("未取到公开财报数据，可能是非上市主体或接口临时不可用")
    gaps.append("企业内部经营指标未接入：页面只呈现公开披露口径，不能替代管理报表")
    gaps.append("累计数不构成环比，环比一栏留空而非估算")

    # 产业结构：按产品 / 地区 / 行业的收入占比与毛利率（真实披露，非估算）
    structure = mainop(code) if code else {}
    if structure:
        # 主营构成来自另一份报告（常为中报/年报），单独定位它的原文
        st_rep = _period_report(entity, period_name_of(structure.get("latest_date", "")))
        structure["url"] = st_rep.get("url", "")
        structure["src_title"] = st_rep.get("title", "")
    if not structure:
        gaps.append("未取到「主营业务构成」，产品/地区拆分一栏留空")
    elif not structure.get("prev"):
        gaps.append("主营构成只有单期数据，结构变化暂无法比较")

    return {
        "company": company,
        "entity": entity,
        "indicators": indicators,
        "structure": structure,
        "events": events,
        "pending": [{"name": n, "need": d} for n, d in pending],
        "gaps": gaps,
        "periods": [r["name"] for r in series],
    }


def query(company: str, entity: dict, metric: str, periods: int = 4) -> dict:
    """
    场景化取数：只允许白名单指标 × 报告期，不提供自由 SQL。
    返回的每一行都带口径与来源，可并入报告作为事实引用。
    """
    ind = next((x for x in INDICATORS if x["k"] == metric), None)
    if not ind:
        return {"ok": False, "error": f"指标「{metric}」不在白名单内", "rows": []}

    series, err = fetch_series(entity.get("code") or "", entity.get("market", ""), limit=max(8, periods))
    if not series:
        return {"ok": False, "error": err or "未取到数据", "rows": []}

    rows = []
    for row in series[-periods:]:
        v = _num(row["raw"].get(ind["field"]))
        if v is None:
            continue
        yoy = _num(row["raw"].get(ind["yoy"])) if ind["yoy"] else None
        rows.append({
            "period": row["name"],
            "date": row["date"],
            "value": round(v / ind["scale"], ind["digits"]),
            "unit": ind["unit"],
            "yoy": round(yoy, 2) if yoy is not None else None,
        })
    rows.reverse()
    return {
        "ok": True, "rows": rows,
        "metric": {"k": ind["k"], "name": ind["name"], "unit": ind["unit"], "caliber": ind["caliber"]},
        "note": "取数为白名单指标，口径固定；如需新指标须先补口径定义并双签。",
    }
