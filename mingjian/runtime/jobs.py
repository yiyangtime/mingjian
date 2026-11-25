"""
后台作业：运行队列、事件推送、取消与续跑。

HTTP 层（api/）只做参数校验与入队；真正的长任务在守护线程里跑，
事件先进内存供前端轮询，再落 SQLite 供服务重启后回放。
"""
from __future__ import annotations

import copy
import json
import threading
import time
from pathlib import Path

from ..config import CFG, OUT_DIR
from ..core.log import get as _get_logger
from ..infra import store
from . import compare, pipeline

LOG = _get_logger("jobs")

_LOCK = threading.Lock()
_JOBS: dict[str, dict] = {}
# 取消信号只放内存：进程重启后线程已不在，不能再取消；此时只能续跑。
_CANCEL: set[str] = set()


def _report_path(rid: str) -> Path:
    """报告文件名的定位：现在统一为 <公司>_<运行号>.md，早期版本是 <运行号>.md。"""
    for pat in (f"*_{rid}.md", f"{rid}.md"):
        hit = next(iter(sorted(OUT_DIR.glob(pat))), None)
        if hit:
            return hit
    return OUT_DIR / f"{rid}.md"


def _result_from_db(rid: str) -> dict | None:
    """进程重启后内存任务没了，历史运行从库里重建，深链接仍能打开。"""
    run = store.get_run(rid)
    if not run or run.get("status") != "done":
        return None

    entity = store.get_entity(run["entity_key"]) or {}
    docs = store.docs_of_run(rid)
    by_id = {d["id"]: d for d in docs}

    facts = []
    for row in store.facts_of_run(rid):
        full_id = row.get("id") or ""
        short_id = full_id[len(rid) + 1:] if full_id.startswith(rid + "-") else full_id
        doc = by_id.get(row.get("doc_id") or "", {})
        facts.append({
            "id": short_id, "doc_id": row.get("doc_id"),
            "type": row.get("type"), "type_label": row.get("type_label"),
            "statement": row.get("statement"), "evidence_quote": row.get("evidence_quote") or "",
            "amount": row.get("amount") or "", "event_date": row.get("event_date") or "",
            "confidence": float(row.get("confidence") or 0),
            "corroboration": int(row.get("corroboration") or 1),
            "source": doc.get("source") or "", "url": doc.get("url") or "",
            "published_at": doc.get("published_at") or "",
            "evidence_level": doc.get("evidence_level") or "",
        })

    try:
        stats = json.loads(run.get("stats") or "{}")
    except Exception:
        stats = {}

    md = ""
    f = _report_path(rid)
    if f.exists():
        md = f.read_text(encoding="utf-8")

    base = store.last_snapshot(run["entity_key"], exclude_run=rid)
    changes = {
        "has_baseline": bool(base),
        "baseline_count": len(base["facts"]) if base else 0,
        "baseline_run": (base or {}).get("run_id", ""),
        "new": [], "escalated": [], "faded": [],
    }

    return {
        "run_id": rid, "company": run["company"], "entity": entity,
        "entity_key": run["entity_key"],
        "profile": {}, "quote": {}, "docs": docs, "facts": facts, "changes": changes,
        "report": md, "stats": stats, "errors": [], "restored": True,
    }


def _steps_of(rid: str, job: dict | None = None) -> list[dict]:
    """从落库事件重建步骤条；内存 job 只在落库失败时兜底。"""
    events = store.events_of_run(rid, 0, 5000)
    if not events and job:
        events = job.get("events") or []
    steps: dict[int, dict] = {}
    for e in events:
        if e.get("type") == "step" and e.get("idx") is not None:
            steps[int(e["idx"])] = {
                "idx": e["idx"], "name": e.get("name", ""),
                "status": e.get("status", ""), "detail": e.get("detail", ""),
            }
    return sorted(steps.values(), key=lambda x: x["idx"])


def _new_job(rid: str, company: str, kind: str = "run") -> dict:
    return {
        "id": rid, "company": company, "kind": kind, "status": "queued",
        "steps": {s["idx"]: s for s in _steps_of(rid)},
        "events": [], "result": None, "error": None,
        "seq": store.max_event_seq(rid),
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def _job_push(job: dict, e: dict) -> None:
    """事件先进内存（前端轮询要快），再落 SQLite（重启后可回放）。"""
    with _LOCK:
        seq = int(job.get("seq") or 0) + 1
        job["seq"] = seq
        ev = dict(e)
        ev["seq"] = seq
        if ev.get("type") == "step" and ev.get("idx") is not None:
            job["steps"][ev["idx"]] = {
                "idx": ev["idx"], "name": ev.get("name", ""),
                "status": ev.get("status", ""), "detail": ev.get("detail", ""),
            }
        job["events"].append(ev)
    try:
        store.append_event(job["id"], seq, ev)
    except Exception as ex:  # 落库失败不应阻断执行
        LOG.warning("事件落库失败 run=%s seq=%s: %s", job.get("id"), seq, ex)


def _worker(job: dict, cfg, resume: bool = False) -> None:
    try:
        job["status"] = "running"
        res = pipeline.run(
            job["company"], cfg=cfg, on_event=lambda e: _job_push(job, e),
            run_id=job["id"], resume=resume,
            cancel_check=lambda: job["id"] in _CANCEL,
        )
        job["result"] = res
        job["status"] = "done"
        # 报告与事实表由 pipeline 第 7 步统一落盘（<公司>_<运行号>.md/csv），此处不再重复写
    except pipeline.Cancelled:
        job["status"] = "cancelled"
        job["error"] = "用户取消"
        _job_push(job, {"type": "log", "level": "warn", "msg": "已按用户请求停止，可从断点续跑",
                        "ts": time.strftime("%H:%M:%S")})
        LOG.info("任务已取消 run=%s company=%s", job.get("id"), job.get("company"))
    except Exception as e:
        job["status"] = "failed"
        job["error"] = f"{type(e).__name__}: {e}"
        _job_push(job, {"type": "log", "level": "error", "msg": job["error"],
                        "ts": time.strftime("%H:%M:%S")})
        LOG.exception("企业洞察任务失败 run=%s company=%s", job.get("id"), job.get("company"))
    finally:
        _CANCEL.discard(job["id"])


def _compare_worker(job: dict, names: list[str], cfg, self_key: str = "") -> None:
    try:
        job["status"] = "running"
        res = compare.run(names, cfg=cfg, on_event=lambda e: _job_push(job, e),
                          self_key=self_key, cancel_check=lambda: job["id"] in _CANCEL)
        job["result"] = res
        job["status"] = "done"
        store.finish_run(job["id"], "done", res.get("stats") or {})
        md = OUT_DIR / f"竞品追踪报告_{job['id']}.md"
        try:
            md.write_text(res["report"], encoding="utf-8")
        except Exception:
            pass
        _job_push(job, {"type": "done", "run_id": job["id"], "stats": res["stats"],
                        "ts": time.strftime("%H:%M:%S")})
    except pipeline.Cancelled:
        job["status"] = "cancelled"
        job["error"] = "用户取消"
        store.finish_run(job["id"], "cancelled", {}, "用户取消")
        _job_push(job, {"type": "log", "level": "warn", "msg": "已按用户请求停止",
                        "ts": time.strftime("%H:%M:%S")})
    except Exception as e:
        job["status"] = "failed"
        job["error"] = f"{type(e).__name__}: {e}"
        store.finish_run(job["id"], "failed", {}, job["error"])
        _job_push(job, {"type": "log", "level": "error", "msg": job["error"],
                        "ts": time.strftime("%H:%M:%S")})
        LOG.exception("竞品追踪任务失败 run=%s names=%s", job.get("id"),
                      "、".join(names) if names else "-")
    finally:
        _CANCEL.discard(job["id"])


def _cfg_from_params(params: dict, max_fetch_cap: int = 40):
    cfg = copy.copy(CFG)
    for k in ("days", "max_news", "max_ann", "max_fetch", "news_pages"):
        v = params.get(k)
        if v is None:
            continue
        try:
            setattr(cfg, k, int(v))
        except Exception:
            continue
    cfg.days = max(3, min(365, cfg.days))
    cfg.max_fetch = max(0, min(max_fetch_cap, cfg.max_fetch))
    return cfg


def _new_rid(prefix: str, seed) -> str:
    base = f"{prefix}{time.strftime('%Y%m%d%H%M%S')}-{abs(hash(seed)) % 10000:04d}"
    for i in range(100):
        rid = base if i == 0 else f"{base}-{i}"
        with _LOCK:
            if rid not in _JOBS and not store.get_run(rid):
                return rid
    raise RuntimeError("无法分配运行编号")


def _enqueue_run(company: str, cfg, trigger: str = "") -> str:
    rid = _new_rid("R", company)
    # 先落一行 runs：即使第 1 步就失败/被取消，重启后也能识别这次运行并续跑
    store.create_run(rid, "", company, cfg.to_dict())
    job = _new_job(rid, company)
    with _LOCK:
        _JOBS[rid] = job
    threading.Thread(target=_worker, args=(job, cfg), daemon=True).start()
    if trigger:
        store.add_audit("", rid, "enqueue", company, reason=trigger, actor="Scheduler")
    return rid


def _enqueue_compare(names: list[str], cfg, self_key: str = "", trigger: str = "") -> str:
    rid = _new_rid("C", tuple(names))
    company = " · ".join(names)
    # entity_key 留空：竞品追踪不是单一主体，不能参与指标/专题的"最近一次运行"选择
    store.create_run(rid, "", company, {"companies": names, "days": cfg.days,
                                        "max_fetch": cfg.max_fetch})
    job = _new_job(rid, company, kind="compare")
    with _LOCK:
        _JOBS[rid] = job
    threading.Thread(target=_compare_worker, args=(job, names, cfg, self_key), daemon=True).start()
    if trigger:
        store.add_audit("", rid, "enqueue", company, reason=trigger, actor="Scheduler")
    return rid


def _start_scheduled(s: dict) -> str:
    """调度线程的回调：把定时任务转成一次普通运行，复用同一套 worker。"""
    params = s.get("params") or {}
    cfg = _cfg_from_params(params)
    if s.get("kind") == "compare":
        names = [str(c).strip() for c in (params.get("companies") or []) if str(c).strip()]
        if len(names) < 2:
            raise ValueError("竞品追踪至少需要两家公司")
        rid = _enqueue_compare(names, cfg, params.get("self_key") or "",
                               trigger=f"定时任务：{s.get('name')}")
    else:
        company = str(params.get("company") or "").strip()
        if not company:
            raise ValueError("缺少公司名")
        rid = _enqueue_run(company, cfg, trigger=f"定时任务：{s.get('name')}")
    store.mark_schedule_run(s["id"], rid)
    return rid
