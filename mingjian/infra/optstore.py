"""离线优化的本地版本存储。

候选配置、评测报告与上线记录都属于本机运行产物，放 `data/optimizer/`，
不入库、不进 Git。这里只负责读写文件，不判断规则是否合理——校验与门禁在
`services/optimizer.py`，避免把业务判断塞进存储层。

目录约定：

    data/optimizer/
      active_event_rules.json       当前生效版本（唯一指针）
      versions/<version>.json       每次通过门禁的完整规则，可回滚
      evaluations/<version>-*.json  一次离线评测的完整结果
      audit.jsonl                   只追加的评测 / 上线 / 回滚记录
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import time
import uuid

from ..config import DATA_DIR

OPT_DIR = DATA_DIR / "optimizer"
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _root() -> pathlib.Path:
    OPT_DIR.mkdir(parents=True, exist_ok=True)
    return OPT_DIR


def active_path() -> pathlib.Path:
    return _root() / "active_event_rules.json"


def versions_dir() -> pathlib.Path:
    p = _root() / "versions"
    p.mkdir(parents=True, exist_ok=True)
    return p


def evaluations_dir() -> pathlib.Path:
    p = _root() / "evaluations"
    p.mkdir(parents=True, exist_ok=True)
    return p


def audit_path() -> pathlib.Path:
    return _root() / "audit.jsonl"


def safe_version(version: str) -> str:
    """版本号同时是文件名；拒绝路径分隔符与空版本。"""
    v = str(version or "").strip()
    if not _VERSION_RE.fullmatch(v):
        raise ValueError("版本号只能包含字母、数字、点、下划线和短横线，长度 3—64")
    return v


def read_json(path: pathlib.Path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_json(path: pathlib.Path, payload: dict) -> None:
    """先写临时文件再原子替换，避免进程中断留下半个 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def load_active() -> dict | None:
    data = read_json(active_path())
    return data if isinstance(data, dict) else None


def save_active(payload: dict) -> None:
    write_json(active_path(), payload)


def archive_version(version: str, payload: dict) -> pathlib.Path:
    path = versions_dir() / f"{safe_version(version)}.json"
    if path.exists():
        raise FileExistsError(f"版本已存在，不能覆盖：{version}")
    write_json(path, payload)
    return path


def load_version(version: str) -> dict | None:
    try:
        path = versions_dir() / f"{safe_version(version)}.json"
    except ValueError:
        return None
    data = read_json(path)
    return data if isinstance(data, dict) else None


def list_versions() -> list[dict]:
    out = []
    for path in sorted(versions_dir().glob("*.json")):
        data = read_json(path)
        if not isinstance(data, dict):
            continue
        out.append({
            "version": data.get("version") or path.stem,
            "base_version": data.get("base_version") or "",
            "promoted_at": data.get("promoted_at") or "",
            "actor": data.get("actor") or "",
        })
    return out


def save_evaluation(version: str, report: dict) -> pathlib.Path:
    stamp = time.strftime("%Y%m%d%H%M%S")
    name = f"{safe_version(version)}-{stamp}-{uuid.uuid4().hex[:6]}.json"
    path = evaluations_dir() / name
    write_json(path, report)
    return path


def append_audit(event: dict) -> None:
    row = {"ts": now(), **event}
    with audit_path().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_audit(limit: int = 20) -> list[dict]:
    path = audit_path()
    if not path.exists():
        return []
    rows: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict):
                rows.append(item)
    except OSError:
        return []
    return rows[-max(1, int(limit)):]