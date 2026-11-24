"""政策公告路由：目录扫描、上传解析、人工确认留痕、工信部/省厅官网取数。"""
from __future__ import annotations

import base64
import time

from fastapi import APIRouter, Body, HTTPException

from ...config import ROOT
from ...infra import store
from ...infra.sources import cninfo, eastmoney
from ...services import govpolicy, policy, subject
from ..deps import truthy

router = APIRouter(tags=["政策取数"])

POLICY_DIR = ROOT / "materials"


@router.get("/api/policy/scan")
def policy_scan():
    docs, skipped = policy.scan_dir(POLICY_DIR)
    for i, d in enumerate(docs, 1):
        d["id"] = "D%02d" % i
    return {"docs": docs, "stats": policy.stats(docs), "skipped": skipped,
            "dir": str(POLICY_DIR), "files": len(docs)}


@router.get("/api/policy/audit")
def policy_audit(limit: int = 100):
    return {"audit": store.list_audit("policy", limit)}


@router.get("/api/policy/gov/health")
def gov_health():
    snap = govpolicy.health_snapshot()
    return {
        "provinces": snap,
        "unavailable": govpolicy.unavailable_provinces(),
        "tested": sum(1 for v in snap.values() if v.get("available") is not None),
        "total": len(snap),
    }


@router.get("/api/policy/gov")
def gov_collect(company: str = "", refresh: str = "0", province: str = "",
                central: int = 4, local: int = 4, keyword: list[str] | None = None):
    company = company.strip()
    if not company:
        raise HTTPException(400, "请提供公司名称")
    if not truthy(refresh):
        hit = govpolicy.load_cache(company)
        if hit:
            hit["cached"] = True
            return hit

    ent = cninfo.resolve(company) or {"short_name": company, "code": ""}
    prof = eastmoney.profile(ent.get("code", ""), ent.get("market", "")) if ent.get("code") else {}
    # 非上市主体没有公开档案：退到自述档案取行业/产品/所在地，
    # 否则检索词只能从公司名里猜，取回的政策必然跑偏。
    sub = {}
    if not prof.get("industry") and not prof.get("main_business"):
        sub = subject.get(company) or {}
    ind_kw_source = "公开档案"
    if not prof.get("industry") and sub:
        ind_kw_source = "企业自述档案"
    logs: list[dict] = []

    def emit(e):
        if e.get("type") == "log":
            logs.append({"t": time.strftime("%H:%M:%S"), "msg": e.get("msg", ""),
                         "kind": e.get("kind", "info")})

    res = govpolicy.collect(
        company,
        industry=prof.get("industry") or sub.get("industry", ""),
        main_business=prof.get("main_business") or sub.get("products", ""),
        address=prof.get("address") or sub.get("region", ""),
        province=province,
        max_central=max(0, central),
        max_local=max(0, local),
        keywords=[k for k in (keyword or []) if k] or None,
        on_event=emit,
    )
    res.update({"cached": False, "logs": logs, "entity": ent,
                "industry": prof.get("industry") or sub.get("industry", ""),
                "industry_source": ind_kw_source,
                "address": prof.get("address") or sub.get("region", ""),
                "full_name": prof.get("full_name", "")})
    govpolicy.save_cache(res)
    store.add_audit("policy", company, "gov_fetch",
                    f"{len(res.get('docs') or [])} 份",
                    reason=f"官网取数，检索词 {'、'.join((res.get('keywords') or [])[:4])}",
                    actor="业务用户")
    return res


@router.post("/api/policy/parse")
def parse_policy(data: dict | None = Body(default=None)):
    data = data or {}
    files = data.get("files") or []
    if not files:
        raise HTTPException(400, "没有收到文件")
    docs, skipped = [], []
    for i, f in enumerate(files[:30], 1):
        name = str(f.get("name") or "未命名")[:120]
        try:
            raw = base64.b64decode(f.get("b64") or "")
        except Exception:
            skipped.append(f"{name}：文件内容解码失败")
            continue
        if len(raw) > 40 * 1024 * 1024:
            skipped.append(f"{name}：超过 40MB，未解析")
            continue
        try:
            docs.append(policy.parse_bytes(name, raw, doc_id="D%02d" % i))
        except Exception as e:
            skipped.append(f"{name}：{type(e).__name__}: {e}")
    for i, d in enumerate(docs, 1):
        d["id"] = "D%02d" % i
    store.add_audit("policy", "-", "parse", f"{len(docs)} 份",
                    reason=f"上传解析，跳过 {len(skipped)} 份", actor="业务用户")
    return {"docs": docs, "stats": policy.stats(docs),
            "skipped": skipped, "files": len(docs)}


@router.post("/api/policy/confirm")
def confirm_policy(data: dict | None = Body(default=None)):
    data = data or {}
    action = data.get("action") or "confirm"
    store.add_audit("policy", data.get("doc") or "-", action,
                    data.get("field") or "", data.get("old_value") or "",
                    data.get("new_value") or "", data.get("reason") or "",
                    data.get("actor") or "业务用户")
    rows = store.list_audit("policy", 100)
    return {"ok": True, "audit": rows,
            "note": "已写入审计日志，原值保留不覆盖"}
