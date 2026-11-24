"""路由层共享依赖：跨路由复用的只读状态与小工具。

只放接口层复用件，不放业务规则（业务规则属于 services/）：
  · START        进程启动时间（健康检查、系统信息共用）
  · truthy       查询参数里的布尔开关解析（"1"/"true"）
  · latest_facts 取公司最近一次已完成运行的事实摘要（指标/专题共用）
  · 公司档案缓存  列表回填会重复请求同一批公司，按 TTL 缓存避免打爆上游
"""
from __future__ import annotations

import time

from ..infra import store
from ..infra.sources import cninfo

START = time.time()

PROFILE_TTL = 3600
profile_cache: dict[str, tuple[float, dict]] = {}


def truthy(v: str) -> bool:
    return str(v or "").strip().lower() in ("1", "true")


def latest_facts(company: str) -> tuple[dict, str, list[dict]]:
    """公司 → (实体, 最近一次完成的运行号, 事实摘要列表)。"""
    ent = cninfo.resolve(company) or {"short_name": company, "code": "",
                                      "market": "", "resolved_by": "none"}
    key = ent.get("code") or ent.get("short_name") or company
    rid = ""
    for r in store.list_runs(60):
        if r.get("entity_key") == key and r.get("status") == "done":
            rid = r["id"]
            break
    facts = []
    if rid:
        docs = store.docs_of_run(rid)
        by_id = {d["id"]: d for d in docs}
        for row in store.facts_of_run(rid):
            full = row.get("id") or ""
            short = full[len(rid) + 1:] if full.startswith(rid + "-") else full
            doc = by_id.get(row.get("doc_id") or "", {})
            facts.append({"id": short, "type": row.get("type"),
                          "type_label": row.get("type_label"),
                          "statement": row.get("statement"),
                          "published_at": doc.get("published_at") or "",
                          "source": doc.get("source") or "",
                          "url": doc.get("url") or ""})
    return ent, rid, facts
