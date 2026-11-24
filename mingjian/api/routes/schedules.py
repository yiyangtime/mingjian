"""定时任务路由：每天/每周表达式的增删改查与立即触发。"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Body, HTTPException

from ...infra import scheduler, store
from ...runtime import jobs

router = APIRouter(tags=["定时调度"])


def _save_schedule_body(data: dict) -> dict:
    kind = str(data.get("kind") or "run").strip()
    if kind not in ("run", "compare"):
        raise ValueError("kind 只支持 run / compare")
    params = data.get("params") or {}
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except Exception:
            raise ValueError("params 不是合法 JSON")
    if not isinstance(params, dict):
        raise ValueError("params 必须是对象")
    spec = data.get("spec") or {}
    if isinstance(spec, str):
        try:
            spec = json.loads(spec)
        except Exception:
            raise ValueError("spec 不是合法 JSON")
    scheduler.validate(spec)

    if kind == "compare":
        names = [str(c).strip() for c in (params.get("companies") or []) if str(c).strip()]
        if len(names) < 2:
            raise ValueError("竞品周报至少需要两家公司")
        params = {**params, "companies": names[:6]}
        default_name = " · ".join(names[:3]) + (" 等" if len(names) > 3 else "") + " 竞品周报"
    else:
        company = str(params.get("company") or "").strip()
        if not company:
            raise ValueError("请提供公司名称")
        params = {**params, "company": company}
        default_name = company + " 情报"

    sid = str(data.get("id") or "").strip()
    if not sid:
        seed = json.dumps(params, sort_keys=True, ensure_ascii=False)
        sid = f"S{time.strftime('%Y%m%d%H%M%S')}-{abs(hash(seed)) % 10000:04d}"
    enabled = bool(data.get("enabled", True))
    return store.upsert_schedule({
        "id": sid,
        "name": str(data.get("name") or default_name).strip()[:60],
        "kind": kind, "params": params, "spec": spec, "enabled": enabled,
        "next_run_at": scheduler.next_run_text(spec) if enabled else "",
    })


@router.get("/api/schedules")
def list_schedules():
    rows = store.list_schedules()
    for s in rows:
        s["describe"] = scheduler.describe(s.get("spec") or {})
    return {"schedules": rows, "running": scheduler.running()}


@router.post("/api/schedules")
def save_schedule(data: dict | None = Body(default=None)):
    data = data or {}
    try:
        s = _save_schedule_body(data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    store.add_audit("", "", "schedule_save", s["id"],
                    reason=scheduler.describe(s.get("spec") or {}), actor="业务用户")
    s["describe"] = scheduler.describe(s.get("spec") or {})
    return {"ok": True, "schedule": s}


@router.post("/api/schedules/delete")
def delete_schedule(data: dict | None = Body(default=None)):
    data = data or {}
    sid = (data.get("id") or "").strip()
    if not sid:
        raise HTTPException(400, "缺少任务编号")
    store.delete_schedule(sid)
    store.add_audit("", "", "schedule_delete", sid, actor="业务用户")
    return {"ok": True}


@router.post("/api/schedules/toggle")
def toggle_schedule(data: dict | None = Body(default=None)):
    data = data or {}
    sid = (data.get("id") or "").strip()
    s = store.get_schedule(sid)
    if not s:
        raise HTTPException(404, "找不到该定时任务")
    enabled = bool(data.get("enabled", not s.get("enabled")))
    nxt = scheduler.next_run_text(s.get("spec") or {}) if enabled else ""
    store.set_schedule_enabled(sid, enabled, nxt)
    store.add_audit("", "", "schedule_toggle", sid,
                    reason="启用" if enabled else "停用", actor="业务用户")
    return {"ok": True, "schedule": store.get_schedule(sid)}


@router.post("/api/schedules/run")
def run_schedule(data: dict | None = Body(default=None)):
    data = data or {}
    s = store.get_schedule((data.get("id") or "").strip())
    if not s:
        raise HTTPException(404, "找不到该定时任务")
    try:
        rid = jobs._start_scheduled(s)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"触发失败：{type(e).__name__}: {e}")
    return {"ok": True, "run_id": rid, "schedule": store.get_schedule(s["id"])}
