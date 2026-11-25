"""竞品追踪路由：多家公司逐家跑完整链路后按事件维度对比。"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from ...config import CFG
from ...runtime import jobs

router = APIRouter(tags=["竞品追踪"])


@router.post("/api/competitor-tracking/compare")
def start_compare(data: dict | None = Body(default=None)):
    data = data or {}
    names = [c.strip() for c in (data.get("companies") or []) if c and c.strip()]
    if len(names) < 2:
        raise HTTPException(400, "竞品对比至少需要两家公司")
    if len(names) > CFG.max_companies:
        raise HTTPException(400, f"竞品对比一次最多 {CFG.max_companies} 家公司")
    rid = jobs._enqueue_compare(names, jobs._cfg_from_params(data, max_fetch_cap=20),
                                (data.get("self_key") or "").strip())
    return {"run_id": rid, "status": "queued"}
