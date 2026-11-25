"""企业洞察运行路由：发起、轮询、取消、续跑、历史、报告与事实表。"""
from __future__ import annotations

import json
import threading

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import Response

from ...infra import store
from ...runtime import jobs, pipeline

router = APIRouter(tags=["企业洞察"])


@router.get("/api/company/run/{rid}")
def get_run(rid: str, since: int = 0):
    since = max(0, since)
    with jobs._LOCK:
        job = jobs._JOBS.get(rid)
        if job:
            status = job["status"]
            company = job["company"]
            kind = job.get("kind", "run")
            steps = sorted(job["steps"].values(), key=lambda x: x["idx"])
            error = job.get("error")
            result = job.get("result") if status == "done" else None
    if not job:
        # 内存里没有（服务重启过）：事件与结果都从库里重建，深链接仍然能打开
        run = store.get_run(rid)
        if run:
            status, company = run["status"], run["company"]
            error = run.get("error")
            kind = "compare" if rid.startswith("C") else "run"
            steps = jobs._steps_of(rid)
            # 竞品追踪的矩阵只在内存里，重启后不伪造 result，报告文件仍可下载
            result = jobs._result_from_db(rid) if status == "done" and kind == "run" else None
        else:
            # 极端情况下只剩事件（早期没有 runs 行的历史）：靠事件判断是否跑完
            evs = store.events_of_run(rid, 0, 5000)
            if not evs:
                raise HTTPException(404, "找不到该运行")
            status = "done" if any(e.get("type") == "done" for e in evs) else "failed"
            company, kind, steps, result = "", "compare", jobs._steps_of(rid), None
            error = None if status == "done" else "进程重启，运行未完成；请重新发起"
    if not job and status in ("queued", "running"):
        # 理论上启动时已归集；这里再兜一次，避免页面卡在永不结束的“运行中”
        status, error = "failed", error or "服务重启，运行被中断；可从断点续跑"
    events = store.events_of_run(rid, since)
    next_seq = int(events[-1].get("seq") or 0) + 1 if events else since
    return {
        "run_id": rid, "status": status, "company": company, "kind": kind,
        "steps": steps, "events": events, "next": next_seq,
        "result": result, "error": error,
        "cancelable": status in ("queued", "running"),
        "resumable": kind == "run" and status in ("failed", "cancelled"),
    }


@router.get("/api/company/runs")
def list_runs(limit: int = 20, offset: int = 0, q: str = "", status: str = ""):
    limit = max(1, min(100, limit))
    offset = max(0, offset)
    q, status = q.strip(), status.strip()
    rows = store.list_runs(limit, offset, q, status)
    for r in rows:
        try:
            r["stats"] = json.loads(r.get("stats") or "{}")
        except Exception:
            r["stats"] = {}
        r["kind"] = "compare" if str(r.get("id", "")).startswith("C") else "run"
        r.pop("params", None)
    return {"runs": rows, "total": store.count_runs(q, status),
            "limit": limit, "offset": offset}


@router.get("/api/company/report/{rid}")
def get_report(rid: str):
    rid = rid.replace(".md", "")
    md = jobs._report_path(rid)
    if md.exists():
        return Response(
            md.read_bytes(), media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{rid}.md"'},
        )
    with jobs._LOCK:
        job = jobs._JOBS.get(rid)
    if job and job.get("result"):
        return Response(job["result"]["report"].encode("utf-8"),
                        media_type="text/markdown; charset=utf-8")
    raise HTTPException(404, "报告不存在")


@router.get("/api/company/facts/{rid}")
def get_facts(rid: str):
    rid = rid.replace(".csv", "")
    facts = store.facts_of_run(rid)
    if not facts:
        raise HTTPException(404, "没有该运行的事实数据")
    slim = [{"id": f["id"].split("-")[-1], "type_label": f["type_label"],
             "statement": f["statement"], "amount": f["amount"],
             "event_date": f["event_date"], "confidence": f["confidence"],
             "corroboration": f["corroboration"], "evidence_quote": f["evidence_quote"],
             "doc_id": f["doc_id"]} for f in facts]
    return Response(
        pipeline.facts_csv(slim).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="facts_{rid}.csv"'},
    )


@router.post("/api/company/run")
def start_run(data: dict | None = Body(default=None)):
    data = data or {}
    company = str(data.get("company") or "").strip()
    if not company:
        raise HTTPException(400, "请提供公司名称")
    rid = jobs._enqueue_run(company, jobs._cfg_from_params(data))
    return {"run_id": rid, "status": "queued"}


@router.post("/api/company/run/{rid}/cancel")
def cancel_run(rid: str):
    with jobs._LOCK:
        job = jobs._JOBS.get(rid)
        active = bool(job and job.get("status") in ("queued", "running"))
    if not active:
        raise HTTPException(409, "该运行不在执行中，无法取消")
    jobs._CANCEL.add(rid)
    store.add_audit("", rid, "cancel", rid, reason="用户取消", actor="业务用户")
    return {"ok": True, "run_id": rid,
            "note": "已发出取消信号，当前步骤结束后停止"}


@router.post("/api/company/run/{rid}/resume")
def resume_run(rid: str):
    with jobs._LOCK:
        job = jobs._JOBS.get(rid)
        busy = bool(job and job.get("status") in ("queued", "running"))
    if busy:
        raise HTTPException(409, "该运行仍在执行中")
    if rid.startswith("C"):
        raise HTTPException(400, "竞品追踪暂不支持续跑，请重新发起")
    run = store.get_run(rid)
    company = (run or {}).get("company") or (job or {}).get("company") or ""
    status = (run or {}).get("status") or (job or {}).get("status") or ""
    if not company:
        raise HTTPException(404, "找不到该运行")
    if status not in ("failed", "cancelled"):
        raise HTTPException(400, "只有失败或已取消的运行可以续跑")
    try:
        params = json.loads((run or {}).get("params") or "{}")
    except Exception:
        params = {}
    cp = store.last_checkpoint(rid)
    job = jobs._new_job(rid, company)
    with jobs._LOCK:
        jobs._JOBS[rid] = job
    threading.Thread(target=jobs._worker,
                     args=(job, jobs._cfg_from_params(params)),
                     kwargs={"resume": True}, daemon=True).start()
    store.add_audit("", rid, "resume", company,
                    reason=(f"从第 {cp['step']} 步检查点续跑" if cp else "无检查点，从头重跑"),
                    actor="业务用户")
    return {"run_id": rid, "status": "queued",
            "from_step": (cp or {}).get("step", 0)}
