"""公司与主体路由：实体搜索、档案补全、我方自述档案、竞品线索发现。"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Body, HTTPException

from ...core.textproc import similarity
from ...infra import registry, store
from ...infra.sources import cninfo, eastmoney
from ...runtime import compare
from ...services import discover, subject
from ..deps import PROFILE_TTL, profile_cache

router = APIRouter(tags=["企业洞察"])


# 名字互含且长度足够、或 3-gram 高度重合，视为同一主体（简称 vs 工商全称）
def _same_entity(a: str, b: str) -> bool:
    if a == b:
        return True
    if len(a) >= 3 and len(b) >= 3 and (a in b or b in a):
        return True
    return len(a) >= 4 and len(b) >= 4 and similarity(a, b) >= 0.82


def _local_matches(q: str, limit: int) -> list[dict]:
    """本地已知企业：自述档案与历史运行里出现过的名字，含非上市。

    非上市对手没有证券代码，证券主体库搜不到；但只要在本机跑过或建过自述
    档案，就应当能直接搜回来，不必重新手输全称。
    """
    names: list[str] = []
    try:
        names.extend(str(s.get("name") or "").strip() for s in subject.list_all(50))
    except Exception:
        pass
    try:
        for r in store.list_runs(60):
            names.extend(part.strip() for part in str(r.get("company") or "").split("·"))
    except Exception:
        pass
    out: list[dict] = []
    for name in names:
        if not name or any(_same_entity(name, x["name"]) for x in out):
            continue
        if not (q in name or name in q or similarity(name, q) >= 0.62):
            continue
        out.append({"name": name, "code": "", "market": "",
                    "source": "local", "hint": "本地档案"})
        if len(out) >= limit:
            break
    return out


def _registry_matches(q: str, limit: int) -> tuple[list[dict], str]:
    """工商登记：本地缓存优先，未配接口或缓存不足时再查正式 API。

    接口按次计费，本地命中过半就不再消耗额度；上游失败只降级并记一句
    提示，不能让工商接口的可用性决定搜索能不能用。
    """
    out: list[dict] = []

    def add(row: dict) -> None:
        name = str(row.get("name") or "").strip()
        if not name or len(out) >= limit:
            return
        if any(_same_entity(name, x["name"]) for x in out):
            return
        hint = " · ".join(x for x in (str(row.get("reg_status") or ""),
                                       str(row.get("reg_capital") or "")) if x)
        out.append({"name": name, "code": "", "market": "", "source": "registry",
                    "hint": hint or "工商登记",
                    "credit_code": row.get("credit_code") or "",
                    "reg_status": row.get("reg_status") or "",
                    "reg_capital": row.get("reg_capital") or "",
                    "establish_date": row.get("establish_date") or "",
                    "website": row.get("website") or ""})

    for row in store.search_companies(q, limit):
        add(dict(row))
    if len(out) >= limit or not registry.enabled():
        return out, ""
    meta: dict = {}
    for row in registry.search(q, limit=limit, meta=meta):
        add(row)
    if meta.get("status") == "error":
        return out, f"工商接口暂时不可用（{meta.get('error')}）；已退回家档案与资讯线索"
    return out, ""


@router.get("/api/company/entity/search")
def entity_search(q: str = "", limit: int = 8):
    """公司搜索：工商登记 + 上市公司库 + 本地档案 + 公开资讯主体。

    工商接口配置后能覆盖各类合法登记企业；没有配置时保持原行为：上市
    公司走证券主体库，非上市从资讯报道与本地档案反查。两路都取不到时，
    仍额外返回一条“按输入名称建档”的候选，让搜索框本身就是入口。
    """
    q = q.strip()
    if not q:
        return {"items": []}
    limit = max(1, min(20, int(limit or 8)))
    with ThreadPoolExecutor(max_workers=2) as pool:
        listed_f = pool.submit(cninfo.search, q, limit)
        clue_f = pool.submit(discover.clues, q, min(6, limit))
        reg_f = pool.submit(_registry_matches, q, limit)
        listed = listed_f.result() or []
        clues = clue_f.result() or []
        reg_items, reg_note = reg_f.result()

    items: list[dict] = []
    for it in listed:
        name = str(it.get("name") or "").strip()
        if not name or any(_same_entity(name, x["name"]) for x in items):
            continue
        items.append({**it, "source": "listed"})
    for it in reg_items:
        if len(items) >= limit:
            break
        if any(_same_entity(it["name"], x["name"]) for x in items):
            continue
        items.append(it)
    for it in _local_matches(q, limit):
        if any(_same_entity(it["name"], x["name"]) for x in items):
            continue
        items.append(it)
        if len(items) >= limit:
            break
    for it in clues:
        name = str(it.get("name") or "").strip()
        if not name or len(items) >= limit:
            continue
        if any(_same_entity(name, x["name"]) for x in items):
            continue
        items.append({"name": name, "code": "", "market": "", "source": "news",
                      "hint": it.get("hint") or ""})

    # 证券库与资讯都取不到时，输入词本身仍可建档。保留这一条，搜索框
    # 才是一个完整入口；否则非上市主体只能靠用户自己找到隐藏的添加按钮。
    if len(q) >= 2 and not any(_same_entity(str(x.get("name") or ""), q) for x in items):
        manual = {"name": q, "code": "", "market": "", "source": "manual",
                  "hint": "未收录，按输入名称建档"}
        if len(items) >= limit:
            items = items[:limit - 1]
        items.append(manual)
    return {"items": items[:limit], "notes": [reg_note] if reg_note else []}


@router.get("/api/company/entity/profile")
def entity_profile(name: str = "", code: str = "", market: str = ""):
    """公司档案补全：代码、类型、所处产业、主营。非上市退回企业自述档案。"""
    name, code, market = name.strip(), code.strip(), market.strip()
    if not name and not code:
        raise HTTPException(400, "请提供公司名称")
    cache_key = f"{code}|{market}" if code else name
    hit = profile_cache.get(cache_key)
    if hit and time.time() - hit[0] < PROFILE_TTL:
        return dict(hit[1])

    ent: dict = {}
    if not code and name:
        ent = cninfo.resolve(name) or {}
        code = str(ent.get("code") or "")
        market = market or str(ent.get("market") or "")
    short = str(ent.get("short_name") or name)
    prof = eastmoney.profile(code, market) if code else {}
    sub: dict = {}
    if not prof.get("industry") and not prof.get("main_business"):
        sub = subject.get(name or short) or {}
    # 非上市主体：本地缓存或工商接口能给出信用代码、法人、注册资本与经营范围
    reg = {} if code else (registry.resolve(name or short) or {})

    if code:
        if len(code) == 5 or "港" in market:
            type_label = "上市公司 · 港股"
        elif code.startswith(("43", "83", "87", "88", "92")) or "北" in market:
            type_label = "上市公司 · 北交所"
        else:
            type_label = "上市公司 · A股"
    else:
        type_label = "非上市企业"
    sources = []
    if prof.get("industry") or prof.get("main_business"):
        sources.append("公开档案")
    if reg:
        sources.append("工商登记")
    if sub:
        sources.append("企业自述档案")
    out = {
        "name": prof.get("short_name") or short or name,
        "full_name": prof.get("full_name") or sub.get("name") or reg.get("name") or "",
        "code": code,
        "market": market,
        "type_label": type_label,
        "listed": bool(code),
        "industry": prof.get("industry") or reg.get("industry") or sub.get("industry") or "",
        "main_business": (prof.get("main_business") or sub.get("products")
                           or reg.get("business") or ""),
        "region": prof.get("address") or sub.get("region") or reg.get("location") or "",
        "credit_code": reg.get("credit_code") or "",
        "reg_status": reg.get("reg_status") or "",
        "reg_capital": reg.get("reg_capital") or "",
        "establish_date": reg.get("establish_date") or "",
        "website": prof.get("website") or reg.get("website") or "",
        "source": " · ".join(sources),
    }
    # 取不到任何字段时不缓存，避免自述档案后补后仍然命中空结果
    if out["industry"] or out["main_business"]:
        profile_cache[cache_key] = (time.time(), out)
    return out


@router.get("/api/company/subject")
def get_subject(key: str = ""):
    key = key.strip()
    if key:
        s = subject.get(key)
        if not s:
            return {"subject": {}, "found": False}
        return {"subject": s, "found": True,
                "baseline": subject.baseline(s),
                "coverage": subject.coverage(s, compare.DIMENSIONS)}
    return {"subjects": subject.list_all(), "fields": subject.FIELDS}


@router.post("/api/company/subject")
def save_subject(data: dict | None = Body(default=None)):
    data = data or {}
    try:
        s = subject.save(data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"档案保存失败：{type(e).__name__}")
    return {"ok": True, "subject": s,
            "baseline": subject.baseline(s),
            "coverage": subject.coverage(s, compare.DIMENSIONS),
            "note": "已存为企业自述基线，参与竞品追踪时标为「企业自述」，不冒充公开事实"}


@router.post("/api/company/discover")
def discover_companies(data: dict | None = Body(default=None)):
    data = data or {}
    products = str(data.get("products") or "").strip()
    region = str(data.get("region") or "").strip()
    if not products and not region:
        raise HTTPException(400, "至少填写主营产品（品类词）")
    try:
        return discover.find(products, region,
                             days=int(data.get("days") or 180),
                             limit=int(data.get("limit") or 12),
                             exclude=data.get("exclude") or [])
    except Exception as e:
        raise HTTPException(500, f"线索检索失败：{type(e).__name__}: {e}")
