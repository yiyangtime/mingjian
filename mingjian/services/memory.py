"""
跨运行记忆：结构化、可检索、可删除。

设计约束：
  · 一条记忆必须有内容与主题，能回溯到 run_id / doc_id / url 的尽量带上引用；
  · 检索用字面词命中 + 置信度 + 新鲜度，不引入向量库与外部依赖；
  · 记忆只做「口径与摘要」，不替代事实表；报告里的每条事实仍然只认 documents/facts。
"""
from __future__ import annotations

import hashlib
import json
import re
import time

from ..infra import store

KINDS = ("fact", "judgment", "caliber", "preference", "note")
_LABEL = {"fact": "事实", "judgment": "判断", "caliber": "口径",
          "preference": "偏好", "note": "备注"}

# 中文二元组里纯虚词组合没有检索价值
_STOP = set("的了着过吗呢吧啊和与及或是在有为对从把被将就都而及其之")


def _norm(s: str, n: int) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())[:n]


def _tokens(text: str) -> set[str]:
    text = (text or "").lower()
    out: set[str] = set()
    for w in re.findall(r"[a-z0-9_]{2,}", text):
        out.add(w)
    for seg in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(seg) <= 2:
            out.add(seg)
            continue
        for i in range(len(seg) - 1):
            bg = seg[i:i + 2]
            if not all(ch in _STOP for ch in bg):
                out.add(bg)
    return out


def _id(scope: str, key: str, kind: str, content: str) -> str:
    raw = f"{scope}|{key}|{kind}|{content}".encode("utf-8", "ignore")
    return "M" + hashlib.md5(raw).hexdigest()[:12]


def _row(row) -> dict:
    d = dict(row)
    try:
        d["refs"] = json.loads(d.get("refs") or "[]")
    except Exception:
        d["refs"] = []
    d["kind_label"] = _LABEL.get(d.get("kind") or "", d.get("kind") or "")
    return d


def _merge_refs(old: str, new: list) -> list:
    out, seen = [], set()
    try:
        old_items = json.loads(old or "[]")
    except Exception:
        old_items = []
    for it in list(old_items) + list(new or []):
        sig = json.dumps(it, ensure_ascii=False, sort_keys=True)
        if sig in seen:
            continue
        seen.add(sig)
        out.append(it)
    return out[:20]


def remember(scope: str = "global", key: str = "", content: str = "",
             kind: str = "note", refs: list | None = None, source: str = "",
             confidence: float | None = None) -> dict:
    """写入或合并一条记忆。同 scope/key/kind/content 视为同一条。"""
    store.init()
    scope = _norm(scope, 120) or "global"
    key = _norm(key, 120)
    content = _norm(content, 2000)
    kind = (kind or "note").strip()
    if not content:
        raise ValueError("记忆内容不能为空")
    if kind not in KINDS:
        raise ValueError(f"未知记忆类型：{kind}")
    if confidence is not None:
        try:
            confidence = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            raise ValueError("置信度必须是 0—1 之间的数字")

    refs = [r for r in (refs or []) if r][:20]
    mid = _id(scope, key, kind, content)
    ts = store.now()
    with store.tx() as c:
        row = c.execute("SELECT refs, confidence FROM memory WHERE id=?", (mid,)).fetchone()
        if row:
            merged = _merge_refs(row["refs"], refs)
            old_conf = row["confidence"] if row["confidence"] is not None else 0.0
            conf = max(old_conf, confidence or 0.0) if (row["confidence"] is not None or confidence is not None) else None
            c.execute("UPDATE memory SET refs=?, confidence=?, source=?, updated_at=? WHERE id=?",
                      (json.dumps(merged, ensure_ascii=False), conf, source[:120], ts, mid))
        else:
            c.execute(
                "INSERT INTO memory (id,scope,key,kind,content,refs,source,confidence,"
                "hits,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,0,?,?)",
                (mid, scope, key, kind, content, json.dumps(refs, ensure_ascii=False),
                 source[:120], confidence, ts, ts))
    got = get(mid)
    if not got:
        raise RuntimeError("记忆写入失败")
    return got


def get(mid: str) -> dict | None:
    store.init()
    row = store.conn().execute("SELECT * FROM memory WHERE id=?", (mid,)).fetchone()
    return _row(row) if row else None


def recall(query: str = "", scope: str = "", limit: int = 8,
           kinds: list[str] | None = None) -> list[dict]:
    """按字面词命中召回；query 为空时按最近更新返回。"""
    store.init()
    limit = max(1, min(50, int(limit or 8)))
    where, args = [], []
    scope = (scope or "").strip()
    if scope:
        where.append("scope=?")
        args.append(scope)
    kind_list = [k for k in (kinds or []) if k in KINDS]
    if kind_list:
        where.append("kind IN (%s)" % ",".join("?" for _ in kind_list))
        args.extend(kind_list)
    sql = "SELECT * FROM memory"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY updated_at DESC LIMIT 500"
    rows = [_row(r) for r in store.conn().execute(sql, args).fetchall()]

    q = _tokens(query)
    if not q:
        return rows[:limit]

    now = time.strftime("%Y-%m-%d %H:%M:%S")
    cutoff = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 30 * 86400))
    scored = []
    for r in rows:
        key, content = (r.get("key") or ""), (r.get("content") or "")
        score = 0.0
        for t in q:
            if t in key.lower():
                score += 2.0
            if t in content.lower():
                score += 1.0 + 0.2 * min(content.lower().count(t), 3)
        if score <= 0:
            continue
        score += float(r.get("confidence") or 0) * 0.5
        if (r.get("updated_at") or "") >= cutoff:
            score += 0.5
        score += min(int(r.get("hits") or 0), 10) * 0.02
        scored.append((score, r))
    scored.sort(key=lambda x: (-x[0], x[1].get("updated_at") or ""))
    hit = [r for _, r in scored[:limit]]

    if hit:
        with store.tx() as c:
            for r in hit:
                c.execute("UPDATE memory SET hits=hits+1, used_at=? WHERE id=?", (now, r["id"]))
            for r in hit:
                r["hits"] = int(r.get("hits") or 0) + 1
                r["used_at"] = now
    return hit


def listing(scope: str = "", kind: str = "", limit: int = 50) -> dict:
    store.init()
    limit = max(1, min(200, int(limit or 50)))
    where, args = [], []
    if (scope or "").strip():
        where.append("scope=?")
        args.append(scope.strip())
    if (kind or "").strip() in KINDS:
        where.append("kind=?")
        args.append(kind.strip())
    sql = "SELECT * FROM memory"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    rows = [_row(r) for r in store.conn().execute(sql, (*args, limit)).fetchall()]
    total = store.conn().execute("SELECT COUNT(*) AS n FROM memory").fetchone()["n"]
    return {"items": rows, "total": int(total), "kinds": list(KINDS)}


def forget(mid: str) -> bool:
    store.init()
    with store.tx() as c:
        cur = c.execute("DELETE FROM memory WHERE id=?", ((mid or "").strip(),))
        return cur.rowcount > 0


def stats() -> dict:
    store.init()
    rows = store.conn().execute(
        "SELECT kind, COUNT(*) AS n FROM memory GROUP BY kind").fetchall()
    total = store.conn().execute("SELECT COUNT(*) AS n FROM memory").fetchone()["n"]
    return {"total": int(total), "by_kind": {r["kind"]: int(r["n"]) for r in rows}}


def harvest_run(run_id: str, limit: int = 12) -> dict:
    """把一次已完成运行里的事实摘要沉淀为记忆，引用保留 run_id/doc_id/url。"""
    store.init()
    run = store.get_run(run_id)
    if not run:
        raise ValueError("找不到该运行")
    scope = (run.get("entity_key") or "").strip() or "global"
    key = (run.get("company") or "").strip()
    docs = {d["id"]: d for d in store.docs_of_run(run_id)}
    facts = sorted(store.facts_of_run(run_id),
                   key=lambda f: -(float(f.get("confidence") or 0)))
    written = 0
    for f in facts[:max(1, min(50, int(limit or 12)))]:
        doc = docs.get(f.get("doc_id") or "", {})
        when = f.get("event_date") or doc.get("published_at") or "时间未注明"
        content = f"{f.get('type_label') or '事实'}：{f.get('statement')}（{when}）"
        remember(scope=scope, key=key, kind="fact", content=content,
                 refs=[{"run_id": run_id, "doc_id": f.get("doc_id") or "",
                        "url": doc.get("url") or ""}],
                 source=run_id, confidence=f.get("confidence"))
        written += 1
    remember(scope=scope, key=key, kind="note",
             content=f"运行 {run_id}：{len(facts)} 条事实、{len(docs)} 份材料，状态 {run.get('status') or '—'}",
             refs=[{"run_id": run_id}], source=run_id)
    return {"run_id": run_id, "scope": scope, "written": written + 1,
            "stats": stats()}
