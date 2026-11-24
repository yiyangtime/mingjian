"""公告覆盖对照：公开披露的事项是否在同一轮资讯里出现过。

公告是法定披露，资讯是媒体报道，两者不是同一集合：公告有、资讯无是常态，
小公告无人报道也不算缺陷。这里只做代理指标——给每条公告找最相近的资讯，
把低分公告按分数排序列成缺口清单，供人工抽样判断“检索词或通道是否漏了”；
结果不参与门禁，也不改变任何采集行为。

评分用“公告核心词的 bigram 被资讯覆盖的比例”，分母是公告而不是资讯：
公告标题短、资讯正文长，用交集除以资讯长度会被长文稀释，容易把真覆盖判成缺口。
"""
from __future__ import annotations

import re

# 公告标题的框架词：去掉后剩下的才是事项本身。长词在前，避免被短词先切掉。
_FRAME = ("提示性公告", "更正公告", "补充公告", "关于", "公告", "的")
_PUNCT = re.compile(r"[\s\u3000\-—－_/、，,。.；;：:（）()\[\]【】“”\"'《》<>]+")

MATCH_THRESHOLD = 0.4   # 公告 bigram 覆盖比例达到这里算“有资讯提及”
MIN_CORE_LEN = 3        # 归一后太短的标题（如“年报”）不参与对照


def _norm(text: str) -> str:
    t = _PUNCT.sub("", str(text or ""))
    for word in _FRAME:
        t = t.replace(word, "")
    return t


def _bigrams(text: str) -> set[str]:
    return {text[i:i + 2] for i in range(len(text) - 1)} if len(text) > 1 else set()


def _score(core: str, news_text: str) -> float:
    """公告核心 bigram 被资讯覆盖的比例，取值 0—1。"""
    a, b = _bigrams(core), _bigrams(news_text)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a)


def announcement_gap(announcements: list[dict], news: list[dict]) -> dict:
    """逐条公告找最相近的资讯，返回覆盖统计与明细。

    `rows` 按分数升序（最可能漏采的在前）；`covered` 为分数达到阈值的条数。
    `best` 为空 dict 表示没有任何资讯可对照，不代表“确认漏采”。
    """
    texts = [(_norm(n.get("title") or "") + _norm(n.get("body") or ""), n) for n in news]
    rows: list[dict] = []
    skipped = 0
    for ann in announcements:
        core = _norm(ann.get("title") or "")
        if len(core) < MIN_CORE_LEN:
            skipped += 1
            continue
        best_score, best = 0.0, None
        for text, item in texts:
            s = _score(core, text)
            if s > best_score:
                best_score, best = s, item
        rows.append({
            "id": ann.get("id") or "",
            "title": ann.get("title") or "",
            "published_at": ann.get("published_at") or "",
            "url": ann.get("url") or "",
            "score": round(best_score, 3),
            "covered": best_score >= MATCH_THRESHOLD,
            "best": ({"title": best.get("title") or "", "url": best.get("url") or ""}
                     if best else {}),
        })
    rows.sort(key=lambda r: r["score"])
    total = len(rows)
    covered = sum(1 for r in rows if r["covered"])
    return {
        "announcements": total,
        "covered": covered,
        "gap": total - covered,
        "ratio": round(covered / total, 4) if total else 0.0,
        "threshold": MATCH_THRESHOLD,
        "skipped": skipped,
        "rows": rows,
    }