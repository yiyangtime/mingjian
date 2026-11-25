"""
竞品追踪报告：一次输入多家公司，逐家跑完整链路，再横向对比成稿。

和「企业洞察」的区别不在采集，在成稿口径：
  · 单家报告回答"这家发生了什么"；
  · 竞品追踪回答"同一件事上，谁在动、动得多快、我方处在什么位置"。

所以这里的判断必须挂在跨公司的事实编号上（如 H2-F03），不能只做词频统计。
"""
from __future__ import annotations

import time

from ..config import CFG
from ..infra import store
from ..services import subject as SUB
from . import pipeline

# 竞品追踪关注的维度，按事件类型对齐，避免各家分类不同导致不可比
DIMENSIONS = [
    ("capacity", "产能与基地"),
    ("order", "订单与合作"),
    ("product", "产品与新品"),
    ("price", "价格与商务政策"),
    ("finance", "资本运作"),
    ("performance", "经营业绩"),
    ("policy", "政策与补贴"),
    ("rd", "研发与技术"),
]


def _recent_run(company: str, max_age_hours: int = 24) -> dict | None:
    """24 小时内跑过就复用，避免同一批数据被反复抓取。"""
    try:
        runs = store.list_runs(60)
    except Exception:
        return None
    now = time.time()
    for r in runs:
        if r.get("company") != company or r.get("status") != "done":
            continue
        try:
            ts = time.mktime(time.strptime(r["started_at"], "%Y-%m-%d %H:%M:%S"))
        except Exception:
            continue
        if now - ts <= max_age_hours * 3600:
            facts = store.facts_of_run(r["id"])
            if not facts:
                continue
            return {"run_id": r["id"], "facts_rows": facts, "reused": True}
    return None


def _facts_from_rows(rid: str, rows: list[dict], docs: list[dict]) -> list[dict]:
    by_id = {d["id"]: d for d in docs}
    out = []
    for row in rows:
        full = row.get("id") or ""
        short = full[len(rid) + 1:] if full.startswith(rid + "-") else full
        doc = by_id.get(row.get("doc_id") or "", {})
        out.append({
            "id": short, "type": row.get("type"), "type_label": row.get("type_label"),
            "statement": row.get("statement"), "evidence_quote": row.get("evidence_quote") or "",
            "confidence": float(row.get("confidence") or 0),
            "corroboration": int(row.get("corroboration") or 1),
            "amount": row.get("amount") or "", "event_date": row.get("event_date") or "",
            "source": doc.get("source") or "", "url": doc.get("url") or "",
            "published_at": doc.get("published_at") or "",
            "evidence_level": doc.get("evidence_level") or "",
        })
    return out


def run(companies: list[str], cfg=None, on_event=None, reuse_hours: int = 24,
        self_key: str = "", cancel_check=None) -> dict:
    cfg = cfg or CFG
    emit = on_event or (lambda e: None)
    companies = [c.strip() for c in companies if c and c.strip()][:cfg.max_companies]
    if not companies:
        raise ValueError("至少输入一家公司")

    # 我方基线：非上市企业没有股票代码，只能靠自述档案参与对比。
    # 没有档案时 self_pack 为空，矩阵保持原样（不凭空造一列"我方"）。
    self_pack = _self_pack(self_key, emit)

    started = time.time()
    packs: list[dict] = []

    for i, name in enumerate(companies, 1):
        if cancel_check and cancel_check():
            raise pipeline.Cancelled("用户取消")
        emit({"type": "step", "idx": 1, "name": f"采集 {name}",
              "status": "start", "detail": f"{i}/{len(companies)}"})
        run_id = ""
        try:
            cached = _recent_run(name, reuse_hours)
            if cached:
                rid = cached["run_id"]
                docs = store.docs_of_run(rid)
                facts = _facts_from_rows(rid, cached["facts_rows"], docs)
                entity = store.get_entity(
                    next((d["entity_key"] for d in docs), "") or "") or {}
                emit({"type": "log", "level": "ok",
                      "msg": f"{name}：复用 {rid} 的结果（24 小时内），{len(facts)} 条事实"})
                packs.append({"company": name, "run_id": rid, "facts": facts,
                              "entity": entity, "docs": len(docs), "reused": True})
            else:
                res = pipeline.run(name, cfg=cfg, on_event=lambda e: _forward(e, emit),
                                   cancel_check=cancel_check)
                packs.append({"company": name, "run_id": res["run_id"], "facts": res["facts"],
                              "entity": res.get("entity") or {}, "docs": len(res.get("docs") or []),
                              "reused": False, "stats": res.get("stats") or {}})
        except pipeline.Cancelled:
            # 取消不是「这家采集失败」：必须向上抛，否则最后一家取消会被当成正常结束
            raise
        except Exception as e:
            emit({"type": "log", "level": "error",
                  "msg": f"{name} 采集失败：{type(e).__name__}: {e}"})
            packs.append({"company": name, "run_id": run_id, "facts": [], "entity": {},
                          "docs": 0, "error": f"{type(e).__name__}: {e}"})

    # 统一编号：H1-F01 便于跨公司引用
    for hi, p in enumerate(packs, 1):
        p["tag"] = f"H{hi}"
        for f in p["facts"]:
            f["ref"] = f"{p['tag']}-{f['id']}"

    emit({"type": "step", "idx": 1, "name": "逐家采集与解析", "status": "done",
          "detail": f"{len(packs)} 家，共 {sum(len(p['facts']) for p in packs)} 条事实"})
    emit({"type": "step", "idx": 2, "name": "事实归并与引用", "status": "done",
          "detail": "统一编号为 H家-序号，后续判断只引用编号"})

    matrix = _matrix(packs, self_pack)
    emit({"type": "step", "idx": 3, "name": "横向对比", "status": "done",
          "detail": f"{len(matrix)} 个有事实的维度对齐"})
    judges = _judgments(packs, matrix, self_pack)
    emit({"type": "step", "idx": 4, "name": "判断与建议", "status": "done",
          "detail": f"{len(judges)} 条推断，均挂依据与置信度"})
    report_md = _render(packs, matrix, judges, cfg, self_pack)

    total = sum(len(p["facts"]) for p in packs)
    stats = {
        "companies": len(packs),
        "facts": total,
        "docs": sum(p["docs"] for p in packs),
        "high_conf": sum(1 for p in packs for f in p["facts"] if f["confidence"] >= 0.85),
        "locatable": sum(1 for p in packs for f in p["facts"] if f.get("url")),
        "elapsed_sec": round(time.time() - started, 1),
        "reused": sum(1 for p in packs if p.get("reused")),
        "self_dims": SUB.coverage(self_pack.get("subject") or {}, DIMENSIONS)["filled"] if self_pack else 0,
    }
    emit({"type": "step", "idx": 5, "name": "成稿与校验", "status": "done",
          "detail": f"追踪报告 {len(report_md)} 字，判断 {len(judges)} 条，全部挂事实编号"})
    return {"companies": packs, "matrix": matrix, "judgments": judges,
            "report": report_md, "stats": stats,
            "self": ({"company": self_pack["company"], "facts": self_pack["facts"],
                      "coverage": SUB.coverage(self_pack.get("subject") or {}, DIMENSIONS)}
                     if self_pack else None)}


def _self_pack(key: str, emit) -> dict | None:
    """读取我方自述档案，转成一列可参与矩阵的条目。空档案返回 None。"""
    if not (key or "").strip():
        return None
    try:
        sub = SUB.get(key)
    except Exception:
        sub = {}
    if not sub:
        emit({"type": "log", "level": "warn",
              "msg": f"未找到「{key}」的自述档案：本次不生成「我方」列"})
        return None
    facts = SUB.baseline(sub)
    cov = SUB.coverage(sub, DIMENSIONS)
    emit({"type": "log", "level": "info",
          "msg": f"我方基线：{sub.get('name')}，覆盖 {cov['filled']}/{cov['total']} 个对比维度"
                 f"（企业自述，未经外部核验）"})
    dim_label = dict(DIMENSIONS)
    for f in facts:
        f["type_label"] = dim_label.get(f["type"], f["type"])
    return {"company": sub.get("name") or key, "tag": "我方", "self": True,
            "facts": facts, "docs": 0, "reused": False, "subject": sub}


def _forward(e: dict, emit) -> None:
    """把单家流水线的事件降级成追踪报告进度，避免前端出现嵌套步骤条。"""
    if e.get("type") == "step":
        return
    if e.get("type") == "log" and e.get("level") in ("warn", "error", "ok"):
        emit(e)


def _matrix(packs: list[dict], self_pack: dict | None = None) -> list[dict]:
    cols = ([self_pack] if self_pack else []) + packs
    rows = []
    for key, label in DIMENSIONS:
        cells = {}
        for p in cols:
            hit = [f for f in p["facts"] if f["type"] == key]
            cells[p["company"]] = {"n": len(hit),
                                   "ids": [(f.get("ref") or f["id"]) for f in hit][:4],
                                   "top": (hit[0]["statement"][:46] if hit else ""),
                                   "declared": bool(hit and hit[0].get("declared"))}
        if any(c["n"] for c in cells.values()):
            rows.append({"key": key, "label": label, "cells": cells})
    return rows


def _judgments(packs: list[dict], matrix: list[dict], self_pack: dict | None = None) -> list[dict]:
    """
    判断只做三件事：谁在密集动作、哪个维度出现同向信号、哪里证据不足。
    每条必须能指回具体事实编号；指不回去就不写。
    我方是自述基线，不参与"谁最活跃"的排序——自述多少条不代表动作多少。
    """
    out = []
    if self_pack is not None:
        cov = SUB.coverage(self_pack.get("subject") or {}, DIMENSIONS)
        blank = [label for _, label in cov["blank"]]
        text = (f"我方基线仅覆盖 {cov['filled']}/{cov['total']} 个维度，属于企业自述、未经外部核验；"
                f"本期只能回答「我方在某维度有没有说法」，不能回答「我方在做这件事时对手在不在做」。")
        if blank:
            text += f"未填维度：{'、'.join(blank[:4])}{' 等' if len(blank) > 4 else ''}。"
        out.append({"id": "J00", "text": text, "basis": "我方档案", "conf": "高"})

    active = sorted(packs, key=lambda p: -len(p["facts"]))
    if active and active[0]["facts"]:
        top = active[0]
        ids = [(f.get("ref") or f["id"]) for f in top["facts"][:4]]
        kinds = {}
        for f in top["facts"]:
            tl = f.get("type_label") or f.get("type")
            kinds[tl] = kinds.get(tl, 0) + 1
        main = max(kinds.items(), key=lambda x: x[1])[0] if kinds else "—"
        out.append({
            "id": "J01",
            "text": f"{top['company']}本期公开信息最密集（{len(top['facts'])} 条），"
                    f"以「{main}」为主，说明其对外沟通节奏快于同组其他标的。",
            "basis": "、".join(ids), "conf": "中",
        })

    # 我方是自述，不算"多家同时动作"里的那一票
    skip = self_pack["company"] if self_pack else ""

    def real_cells(r: dict) -> dict:
        return {k: v for k, v in r["cells"].items() if k != skip}

    crowded = [r for r in matrix if sum(c["n"] > 0 for c in real_cells(r).values()) >= 2]
    if crowded:
        r = max(crowded, key=lambda x: sum(c["n"] for c in real_cells(x).values()))
        cc = real_cells(r)
        who = "、".join(f"{k} {v['n']} 条" for k, v in cc.items() if v["n"])
        ids = [i for v in cc.values() for i in v["ids"]][:4]
        out.append({
            "id": "J02",
            "text": f"「{r['label']}」是本期多家同时动作的维度（{who}），"
                    f"属于行业性同向信号，不宜简单解读为某一家企业的单独策略。",
            "basis": "、".join(ids), "conf": "中",
        })

    weak = [p for p in packs if len(p["facts"]) < 3]
    if weak:
        names = "、".join(p["company"] for p in weak)
        out.append({
            "id": "J03",
            "text": f"{names} 本期有效事实不足 3 条，可能确实无重大动作，也可能是公开信息覆盖不足；"
                    f"在补齐前不对其做趋势判断。",
            "basis": "—", "conf": "低",
        })
    return out


def _render(packs: list[dict], matrix: list[dict], judges: list[dict], cfg,
            self_pack: dict | None = None) -> str:
    total = sum(len(p["facts"]) for p in packs)
    cols = ([self_pack] if self_pack else []) + packs
    L = []
    L.append("# 竞品追踪报告")
    L.append("")
    L.append(f"> 监测对象：{' · '.join(p['company'] for p in packs)}　|　"
             f"生成时间：{time.strftime('%Y-%m-%d %H:%M')}　|　"
             f"事实 {total} 条　|　全部事实可回溯")
    L.append("> 数据来源：巨潮资讯（公告）、东方财富（资讯/公司档案）　|　"
             f"回溯窗口：近 {cfg.days} 天")
    if self_pack:
        L.append(f"> 我方基线：{self_pack['company']}（企业自述，{len(self_pack['facts'])} 项，"
                 "未经外部核验，不可作为对外披露口径）")
    L.append("")

    L.append("## 一、本期事实")
    L.append("")
    for p in packs:
        L.append(f"### {p['company']}（{p['tag']}，{len(p['facts'])} 条）")
        L.append("")
        if p.get("error"):
            L.append(f"- 采集失败：{p['error']}")
        elif not p["facts"]:
            L.append("- 本期未取得有效事实，见文末「待验证与数据缺口」。")
        else:
            for f in p["facts"][:8]:
                lvl = {"full": "全文", "snippet": "摘要", "declared": "自述"}.get(
                    f.get("evidence_level") or "", "标题")
                src = f"（{f['source']}，{lvl}）" if f.get("source") else ""
                L.append(f"- **{f['ref']}**〔{f['type_label']}〕{f['statement']}{src}")
        L.append("")

    L.append("## 二、横向对比")
    L.append("")
    head = "| 维度 | " + " | ".join(p["company"] for p in cols) + " |"
    L.append(head)
    L.append("|" + "---|" * (len(cols) + 1))
    for r in matrix:
        cells = []
        for p in cols:
            c = r["cells"].get(p["company"]) or {"n": 0, "declared": False}
            if p.get("self"):
                cells.append("有自述" if c["n"] else "—")
            else:
                cells.append(f"{c['n']} 条" if c["n"] else "—")
        L.append(f"| {r['label']} | " + " | ".join(cells) + " |")
    L.append("")
    if self_pack:
        L.append("> 「我方」列取自企业自述档案，不是公开事实，也未做同行核验；"
                 "其余列为公开信息自动采集，可点原文回溯。两列口径不同，不能直接比大小。")
        L.append("")

    L.append("## 三、判断")
    L.append("")
    L.append("> 以下为基于上述事实的推断，不是既成事实。每条标注依据，可被单独推翻。")
    L.append("")
    for j in judges:
        L.append(f"- **推断 {j['id']}**　{j['text']}　"
                 f"（依据 {j['basis']}，置信度 {j['conf']}）")
    L.append("")

    L.append("## 四、建议")
    L.append("")
    if judges:
        L.append("- **建议 R01**　对「本期动作最密集」的标的建立周度跟踪卡片，"
                 "固定记录其价格、渠道、产能三类公开口径，避免每周重新判断。")
        L.append("- **建议 R02**　对多家同时动作的维度，交由产品或定价口径负责人判断是否构成行业性变化，"
                 "不要由情报岗单方面定性。")
    else:
        L.append("- 本期无足够事实支撑建议，暂不输出。")
    L.append("")

    L.append("## 五、待验证与数据缺口")
    L.append("")
    gaps = []
    for p in packs:
        if p.get("error"):
            gaps.append(f"{p['company']} 采集失败：{p['error']}")
        elif not p["facts"]:
            continue      # 由下面的「取不到 ≠ 没动作」统一说明，避免同一件事报两遍
        elif len(p["facts"]) < 3:
            gaps.append(f"{p['company']} 仅 {len(p['facts'])} 条事实，不足以判断趋势")
    no_url = sum(1 for p in packs for f in p["facts"] if not f.get("url"))
    if no_url:
        gaps.append(f"{no_url} 条事实缺少原文链接，已降级为标题级证据")
    if self_pack:
        cov = SUB.coverage(self_pack.get("subject") or {}, DIMENSIONS)
        if cov["blank"]:
            gaps.append(f"我方档案未填：{'、'.join(label for _, label in cov['blank'])}"
                        "——这几格无法判断我方有无对应动作")
        gaps.append("我方基线来自企业自述，没有外部出处；要用于对外口径或融资材料时需另行取证")
    silent = [p["company"] for p in packs if not p.get("facts") and not p.get("error")]
    if silent:
        gaps.append(f"{'、'.join(silent)} 在公开渠道一条事实都没有：这类主体多为非上市或长尾企业，"
                    "财经媒体与交易所披露都不覆盖——这是「取不到」，不是「没动作」")
    if not gaps:
        gaps.append("本期无显著缺口；跨周趋势仍需连续运行 3 期以上才能判断")
    for g in gaps:
        L.append(f"- {g}")
    L.append("")
    return "\n".join(L)
