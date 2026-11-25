"""研究助理路由：技能注册表执行、跨运行记忆读写与沉淀。"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from ...infra import store
from ...services import memory, skills

router = APIRouter(tags=["研究助理"])


@router.get("/api/research-assistant/skills")
def list_skills():
    return {"skills": skills.public(), "kinds": skills.KIND_LABEL}


@router.get("/api/research-assistant/memory")
def list_memory(q: str = "", scope: str = "", kind: str = "", limit: int = 50):
    q, scope, kind = q.strip(), scope.strip(), kind.strip()
    limit = max(1, min(200, limit))
    if q:
        items = memory.recall(q, scope, limit=max(limit * 3, 20))
        if kind:
            items = [x for x in items if x.get("kind") == kind][:limit]
        total = len(items)
    else:
        got = memory.listing(scope, kind, limit)
        items, total = got["items"], got["total"]
    return {"items": items, "total": total,
            "stats": memory.stats(), "kinds": list(memory.KINDS)}


@router.post("/api/research-assistant/skills/run")
def run_skill(data: dict | None = Body(default=None)):
    data = data or {}
    name = (data.get("name") or "").strip()
    args = data.get("args") or {}
    try:
        res = skills.run(name, args)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"技能执行失败：{type(e).__name__}: {e}")
    store.add_audit("", "", "skill_run", name,
                    reason=f"{skills.brief(args)}｜{'成功' if res.get('ok') else '失败'}"
                           f"｜{res.get('elapsed_ms')}ms", actor="业务用户")
    return res


@router.post("/api/research-assistant/memory")
def write_memory(data: dict | None = Body(default=None)):
    data = data or {}
    try:
        item = memory.remember(data.get("scope") or "global", data.get("key") or "",
                               data.get("content") or "", data.get("kind") or "note",
                               refs=data.get("refs") or [],
                               source=data.get("source") or "manual",
                               confidence=data.get("confidence"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    store.add_audit(item.get("scope") or "", "", "memory_write", item.get("id") or "",
                    reason=(item.get("content") or "")[:120], actor="业务用户")
    return {"ok": True, "item": item}


@router.post("/api/research-assistant/memory/delete")
def delete_memory(data: dict | None = Body(default=None)):
    data = data or {}
    mid = (data.get("id") or "").strip()
    if not mid:
        raise HTTPException(400, "缺少记忆编号")
    if not memory.forget(mid):
        raise HTTPException(404, "记忆不存在")
    store.add_audit("", "", "memory_delete", mid, reason="删除记忆", actor="业务用户")
    return {"ok": True, "stats": memory.stats()}


@router.post("/api/research-assistant/memory/harvest")
def harvest_memory(data: dict | None = Body(default=None)):
    data = data or {}
    rid = (data.get("run_id") or "").strip()
    if not rid:
        raise HTTPException(400, "缺少运行编号")
    try:
        res = memory.harvest_run(rid, int(data.get("limit") or 12))
    except ValueError as e:
        raise HTTPException(400, str(e))
    store.add_audit("", rid, "memory_harvest", rid,
                    reason=f"沉淀 {res.get('written')} 条", actor="业务用户")
    return {"ok": True, **res}
