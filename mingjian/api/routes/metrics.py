"""指标与专题路由：经营指标、专题归因、白名单指标查询。"""
from __future__ import annotations

import time

from fastapi import APIRouter, Body, HTTPException

from ...infra.sources import cninfo
from ...services import metrics, topic
from ..deps import latest_facts, truthy

router = APIRouter(tags=["指标与专题"])


@router.get("/api/metrics")
def get_metrics(company: str = "", refresh: str = "0"):
    company = company.strip()
    if not company:
        raise HTTPException(400, "请提供公司名称")
    force = truthy(refresh)
    if force:
        metrics.clear_cache()
    ent, rid, facts = latest_facts(company)
    key = ent.get("code") or ent.get("short_name") or company
    out = metrics.build(company, ent, facts)
    out["entity_key"] = key
    out["run_id"] = rid
    out["cache"] = {"ttl_minutes": 30, "refreshed": force}
    return out


@router.get("/api/topic")
def get_topic(company: str = "", refresh: str = "0", kind: str = ""):
    company = company.strip()
    if not company:
        raise HTTPException(400, "请提供公司名称")
    force = truthy(refresh)
    if force:
        metrics.clear_cache()
    ent, rid, facts = latest_facts(company)
    key = ent.get("code") or ent.get("short_name") or company
    out = topic.build(company, ent, facts, kind=kind)
    out["entity_key"] = key
    out["run_id"] = rid
    out["topic_id"] = "S-" + time.strftime("%Y%m%d") + "-" + key[-4:]
    out["cache"] = {"ttl_minutes": 30, "refreshed": force}
    return out


@router.post("/api/metrics/query")
def query_metrics(data: dict | None = Body(default=None)):
    data = data or {}
    company = str(data.get("company") or "").strip()
    ent = cninfo.resolve(company) or {"short_name": company, "code": "", "market": ""}
    return metrics.query(company, ent, data.get("metric") or "",
                         int(data.get("periods") or 4))
