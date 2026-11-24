"""
变化比对：本次 vs 上次。

情报的价值大半在"变化"，不在"现状"。
单看一周只能看到"签了合同"，对比上周才知道"这是本月第三单、节奏在加快"。

只在同一实体、上一次快照存在时才有输出；首次运行会明确标注"无历史基线"，
不允许用"新增"来冒充"变化"。
"""
from __future__ import annotations

from ..core.textproc import similarity

MATCH_THRESHOLD = 0.52
ESCALATE_DELTA = 0.06


def compare(current: list[dict], previous: list[dict] | None) -> dict:
    if not previous:
        return {
            "has_baseline": False,
            "new": [f for f in current],
            "escalated": [],
            "faded": [],
            "unchanged": 0,
            "baseline_count": 0,
        }

    new, escalated, unchanged = [], [], 0
    used_prev: set[int] = set()

    for f in current:
        best_i, best_sim = -1, 0.0
        for i, p in enumerate(previous):
            if i in used_prev:
                continue
            if (p.get("type") or "") != (f.get("type") or "") and "other" not in (p.get("type"), f.get("type")):
                continue
            s = similarity(f.get("statement", ""), p.get("statement", ""))
            if s > best_sim:
                best_i, best_sim = i, s
        if best_i >= 0 and best_sim >= MATCH_THRESHOLD:
            used_prev.add(best_i)
            prev = previous[best_i]
            delta = (f.get("confidence") or 0) - (prev.get("confidence") or 0)
            if delta >= ESCALATE_DELTA:
                escalated.append({**f, "prev_confidence": prev.get("confidence"), "delta": round(delta, 2)})
            else:
                unchanged += 1
        else:
            new.append(f)

    faded = []
    for i, p in enumerate(previous):
        if i in used_prev:
            continue
        # 上次有、这次没有再出现——只作为"本轮未见"提示，不断言事件消失
        faded.append(p)

    return {
        "has_baseline": True,
        "new": new,
        "escalated": escalated,
        "faded": faded,
        "unchanged": unchanged,
        "baseline_count": len(previous),
    }
