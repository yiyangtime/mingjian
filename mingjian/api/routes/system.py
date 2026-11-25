"""运行治理路由：健康检查、系统信息、审计日志读写。"""
from __future__ import annotations

import platform
import time
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException

from ... import __version__
from ...config import CFG, OUT_DIR, ROOT
from ...infra import scheduler, store
from ..deps import START

router = APIRouter(tags=["运行治理"])


def _display_path(value: str, fallback: str) -> str:
    """只回传相对路径；外部库与项目外文件退回方言名，避免泄露主机目录。"""
    if not value:
        return fallback
    try:
        return Path(value).resolve().relative_to(ROOT).as_posix()
    except (OSError, TypeError, ValueError):
        return fallback


@router.get("/api/governance/health")
def health():
    return {"ok": True, "llm": CFG.llm_enabled,
            "model": CFG.llm_model or "rule-engine", "server": "fastapi",
            "version": __version__, "uptime_sec": int(time.time() - START)}


@router.get("/api/governance/system")
def system_info():
    reports = len(list(OUT_DIR.glob("*.md")))
    exports = len(list(OUT_DIR.glob("*.csv")))
    return {
        "version": __version__, "server": "fastapi",
        "python": platform.python_version(), "platform": platform.platform(),
        "uptime_sec": int(time.time() - START),
        "now": time.strftime("%Y-%m-%d %H:%M:%S"),
        "paths": {"root": ".", "db": _display_path(store.target(), store.dialect()),
                  "output": _display_path(str(OUT_DIR), "output"),
                  "logs": _display_path(str(ROOT / "logs"), "logs")},
        "db_backend": store.dialect(),
        "db_bytes": store.db_size(),
        "output": {"reports": reports, "tables": exports, "files": reports + exports},
        "counts": store.table_counts(),
        "llm": {"active": CFG.llm_enabled, "model": CFG.llm_model or "rule-engine",
                "base": CFG.llm_base},
        "scheduler": {"running": scheduler.running()},
        "limits": {"days": CFG.days, "max_fetch": CFG.max_fetch,
                   "max_facts": CFG.max_facts, "min_fact_conf": CFG.min_fact_conf,
                   "max_companies": CFG.max_companies},
    }


@router.get("/api/governance/audit")
def list_audit(limit: int = 50, q: str = "", action: str = ""):
    limit = max(1, min(200, limit))
    q, action = q.strip(), action.strip()
    # 先多取一些再过滤：审计量小，避免为可选筛选加索引与 SQL 分支
    rows = store.list_audit(None, max(limit * 4, 100) if (q or action) else limit)
    if action:
        rows = [r for r in rows if str(r.get("action") or "").startswith(action)]
    if q:
        rows = [r for r in rows if q in " ".join(
            str(r.get(k) or "") for k in ("entity_key", "run_id", "action", "target", "reason", "actor"))]
    actions = sorted({str(r.get("action") or "") for r in store.list_audit(None, 500)})
    return {"audit": rows[:limit], "total": len(rows), "actions": actions}


@router.post("/api/governance/audit")
def add_audit(data: dict | None = Body(default=None)):
    data = data or {}
    try:
        store.add_audit(
            data.get("entity_key", ""), data.get("run_id", ""),
            data.get("action", "note"), data.get("target", ""),
            data.get("old_value", ""), data.get("new_value", ""),
            data.get("reason", ""), data.get("actor", "业务用户"),
        )
    except Exception as e:
        raise HTTPException(500, f"留痕失败：{e}")
    return {"ok": True, "note": "已写入审计日志，原值保留不覆盖"}
