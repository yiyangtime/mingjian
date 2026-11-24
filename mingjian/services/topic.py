"""专题研究：把指标与情报组织成一份可回溯的专题报告。

一件事：输入公司，自动提出当期最值得追的产业问题，并用公开披露数据给出
「事实—判断—建议—待验证」四段结果。判断必须挂到具体事实编号上，不做无依据结论。

当前模板：
  margin  毛利率变动归因（按产品结构拆解，区分业务拖累与结构效应）
  growth  收入增长来源（按产品/地区看谁在拉动）
  default 综合体检（指标 + 主营结构 + 关联情报）

不做的：不给目标价、不给买卖建议、不预测股价——这是产业情报，不是股市情报。
"""
from __future__ import annotations

from . import metrics

# 毛利率同比变动超过这个幅度（百分点）才值得做归因，否则看收入增长来源。
MARGIN_THRESHOLD = 0.5

# 分析类型 → 界面上显示的名称。名称即实际做的事，不带版本号。
TEMPLATE_NAME = {
    "margin": "毛利率变动归因",
    "growth": "收入增长来源",
}


def _fmt(v, digits=2, unit=""):
    if v is None:
        return "—"
    return f"{v:,.{digits}f}{unit}"


def _pct(v):
    return "—" if v is None else f"{v:+.2f} 个百分点"


def _delta(rows_now, rows_prev, key_name="name"):
    """按名称对齐两期，返回 [{name, gm, gm_prev, dgm, ratio, dratio, income}]。"""
    prev = {r[key_name]: r for r in (rows_prev or [])}
    out = []
    for r in rows_now or []:
        p = prev.get(r[key_name])
        out.append({
            "name": r["name"],
            "income": r.get("income"),
            "income_prev": (p or {}).get("income"),
            "dincome": ((r.get("income") - p["income"])
                        if p and r.get("income") is not None and p.get("income") is not None else None),
            "ratio": r.get("ratio"),
            "gm": r.get("gm"),
            "gm_prev": (p or {}).get("gm"),
            "dgm": (r.get("gm") - p["gm"]) if p and r.get("gm") is not None and p.get("gm") is not None else None,
            "ratio_prev": (p or {}).get("ratio"),
            "dratio": (r.get("ratio") - p["ratio"]) if p and r.get("ratio") is not None and p.get("ratio") is not None else None,
        })
    return out


def _decompose(segs_now, segs_prev):
    """毛利率变动拆成两块：业务毛利率变动（价格/单位成本合成）与结构变动（占比迁移）。"""
    prev = {s["name"]: s for s in segs_prev or []}
    margin_effect = mix_effect = 0.0
    detail = []
    for s in segs_now or []:
        p = prev.get(s["name"])
        if not p or s.get("gm") is None or p.get("gm") is None:
            continue
        w = (s.get("ratio") or 0) / 100
        wp = (p.get("ratio") or 0) / 100
        me = w * (s["gm"] - p["gm"]) / 100
        xe = (w - wp) * p["gm"] / 100
        margin_effect += me
        mix_effect += xe
        detail.append({"name": s["name"], "margin_effect": me * 100, "mix_effect": xe * 100,
                       "dgm": s["gm"] - p["gm"], "dratio": (w - wp) * 100})
    return margin_effect * 100, mix_effect * 100, sorted(detail, key=lambda x: x["margin_effect"])


def build(company: str, entity: dict, facts: list[dict] | None = None, kind: str = "") -> dict:
    facts = facts or []
    m = metrics.build(company, entity, facts)
    inds = {i["k"]: i for i in (m.get("indicators") or [])}
    st = m.get("structure") or {}
    latest = st.get("latest") or {}
    prev = st.get("prev") or {}

    gm = inds.get("gm") or {}
    rev = inds.get("revenue") or {}
    gpk = inds.get("gp") or {}
    period = gm.get("period") or rev.get("period") or ""

    prods_now, prods_prev = latest.get("按产品") or [], prev.get("按产品") or []
    regs_now, regs_prev = latest.get("按地区") or [], prev.get("按地区") or []
    prod_diff = _delta(prods_now, prods_prev)
    reg_diff = _delta(regs_now, regs_prev)

    # 选分析类型：毛利率变动足够大才做归因，否则看增长来源。
    # 阈值与选中理由都写进结果，避免「模板」变成一个不可解释的标签。
    kind_reason = ""
    if not kind:
        gmv = gm.get("yoy")
        if gmv is None:
            kind, kind_reason = "growth", "毛利率同比不可得，转看收入增长来源"
        elif abs(gmv) >= MARGIN_THRESHOLD:
            kind, kind_reason = "margin", f"毛利率同比 {gmv:+.2f} 个百分点，超过 ±{MARGIN_THRESHOLD} 的归因阈值"
        else:
            kind, kind_reason = "growth", f"毛利率同比仅 {gmv:+.2f} 个百分点，未过 ±{MARGIN_THRESHOLD} 阈值，转看收入增长来源"
    else:
        kind_reason = "由调用方指定"

    # ---------------- 事实（每条都挂来源；能定位到原文的带链接） ----------------
    F: list[dict] = []

    def add(text, src, tag="", url=""):
        F.append({"id": "T%02d" % (len(F) + 1), "text": text, "src": src,
                  "tag": tag, "url": url or ""})

    if gm.get("val") is not None:
        add(f"{period} 综合毛利率 {_fmt(gm['val'])}%，同比 {_pct(gm.get('yoy'))}",
            gm.get("src", ""), "指标", gm.get("url", ""))
    if prof := inds.get("profit"):
        add(f"{period} 归母净利润 {_fmt(prof['val'])} 亿元，同比 "
            f"{_fmt(prof.get('yoy'), 2, '%') if prof.get('yoy') is not None else '—'}",
            prof.get("src", ""), "指标", prof.get("url", ""))
    if rev.get("val") is not None:
        add(f"{period} 营业收入 {_fmt(rev['val'])} 亿元，同比 "
            f"{_fmt(rev.get('yoy'), 2, '%') if rev.get('yoy') is not None else '—'}",
            rev.get("src", ""), "指标", rev.get("url", ""))
    if gpk.get("val") is not None:
        add(f"{period} 毛利额 {_fmt(gpk['val'])} 亿元，同比 "
            f"{_fmt(gpk.get('yoy'), 2, '%') if gpk.get('yoy') is not None else '—'}",
            gpk.get("src", ""), "指标", gpk.get("url", ""))

    st_src = f"定期报告「主营业务构成」（{st.get('latest_date', '—')}）"
    st_url = st.get("url", "")
    prod_id: dict[str, str] = {}
    for d in sorted(prod_diff, key=lambda x: -(x.get("ratio") or 0))[:4]:
        if d["gm"] is None:
            continue
        txt = f"{d['name']}：收入 {_fmt(d['income'])} 亿元、占比 {_fmt(d['ratio'])}%、毛利率 {_fmt(d['gm'])}%"
        if d["dgm"] is not None:
            txt += f"，毛利率同比 {_pct(d['dgm'])}"
        if d["dratio"] is not None:
            txt += f"，收入占比 {_pct(d['dratio'])}"
        add(txt, st_src, "结构 · 产品", st_url)
        prod_id[d["name"]] = F[-1]["id"]
    reg_id: dict[str, str] = {}
    for d in sorted(reg_diff, key=lambda x: -(x.get("ratio") or 0))[:3]:
        if d["gm"] is None:
            continue
        txt = f"{d['name']}：收入 {_fmt(d['income'])} 亿元、占比 {_fmt(d['ratio'])}%、毛利率 {_fmt(d['gm'])}%"
        if d["dgm"] is not None:
            txt += f"，毛利率同比 {_pct(d['dgm'])}"
        add(txt, st_src, "结构 · 地区", st_url)
        reg_id[d["name"]] = F[-1]["id"]

    _ev = m.get("events") or {}
    linked = [dict(v, id=k) for k, v in _ev.items()][:4] if isinstance(_ev, dict) else list(_ev)[:4]
    # 资本市场的动向（回购、增减持、员工持股、北向）不属于产业情报，剔出专题正文
    CAP_NOISE = ("回购", "增持", "减持", "员工持股", "股权激励", "北向", "融资融券",
                 "龙虎榜", "限售解禁", "股权质押", "股价")
    linked = [x for x in linked if not any(k in (x.get("t") or "") for k in CAP_NOISE)]
    for e in linked:
        add(f"关联情报：{e.get('t', '')[:60]}（{e.get('d', '')}）",
            e.get("src", "") + ("（无原文链接）" if not e.get("url") else ""),
            "情报", e.get("url", ""))

    # ---------------- 判断（挂事实编号） ----------------
    J: list[dict] = []
    margins_ok = bool(prod_diff) and any(d["dgm"] is not None for d in prod_diff)
    me = xe = None
    if kind == "margin" and margins_ok and gm.get("yoy") is not None:
        me, xe, detail = _decompose(prods_now, prods_prev)
        # 残差类「其他」不参与归因结论与建议，避免把 4% 的杂项当成主因
        detail = [x for x in detail if "其他" not in x["name"]]
        worst = min(detail, key=lambda x: x["margin_effect"], default=None)
        best = max(detail, key=lambda x: x["margin_effect"], default=None)
        if worst and worst["margin_effect"] < 0:
            J.append({
                "text": f"毛利率下滑主要由「{worst['name']}」的单位盈利变化贡献：该业务毛利率 "
                        f"{_pct(worst['dgm'])}，按当期收入占比折算，拖累综合毛利率约 "
                        f"{worst['margin_effect']:.2f} 个百分点。",
                "basis": "、".join(x for x in ("T01", prod_id.get(worst["name"])) if x) or "T01",
                "conf": "中高",
                "note": "「毛利率变动」是价格与单位成本合成的结果，公开披露无法进一步拆分两者"})
        if best and best["margin_effect"] > 0 and best is not worst:
            J.append({
                "text": f"「{best['name']}」对毛利率形成正向支撑：毛利率 {_pct(best['dgm'])}，"
                        f"贡献约 {best['margin_effect']:+.2f} 个百分点，削弱了整体降幅。",
                "basis": prod_id.get(best["name"], "T01"), "conf": "中", "note": ""})
        if abs(xe) >= 0.3:
            J.append({
                "text": f"结构效应（{'+' if xe >= 0 else ''}{xe:.2f} 个百分点）：收入在业务间的占比迁移"
                        f"本身对综合毛利率{'有抬升' if xe >= 0 else '有拖累'}作用，与单位盈利变化方向"
                        f"{'一致' if xe * (me or 0) >= 0 else '相反'}，说明"
                        f"{'量价同向' if xe * (me or 0) >= 0 else '结构改善部分对冲了单位盈利下滑'}。",
                "basis": "T05、T06", "conf": "中", "note": "占比按当期收入计算，未剔除内部交易"})
    if kind == "growth":
        # 增长来源：按收入增量排序，说清增量从哪来、占比挪了多少
        grow = [d for d in prod_diff
                if d.get("dincome") is not None and "其他" not in d["name"]]
        grow.sort(key=lambda x: -(x["dincome"] or 0))
        if grow:
            top = grow[0]
            tot = sum(x["dincome"] or 0 for x in grow if (x["dincome"] or 0) > 0)
            txt = f"收入增量主要来自「{top['name']}」：本期 {_fmt(top['income'])} 亿元"
            if top.get("income_prev"):
                yoy = (top["dincome"] / abs(top["income_prev"]) * 100)
                txt += f"，上年同期 {_fmt(top['income_prev'])} 亿元（{yoy:+.2f}%）"
            if tot and top["dincome"] > 0:
                txt += f"，占已拆分业务增量合计的 {top['dincome'] / tot * 100:.0f}%"
            J.append({"text": txt + "。",
                      "basis": prod_id.get(top["name"], "T01"), "conf": "中高",
                      "note": "增量按主营构成口径拆分，未覆盖内部抵消与未拆分品类"})
            if len(grow) > 1 and grow[1]["dincome"] > 0:
                second = grow[1]
                J.append({"text": f"「{second['name']}」是第二增长来源：本期收入 {_fmt(second['income'])} 亿元"
                                  f"（{_fmt(second.get('dincome'))} 亿元增量），占比 {_fmt(second['ratio'])}%。",
                          "basis": prod_id.get(second["name"], "T01"), "conf": "中",
                          "note": ""})
    if kind == "margin" and gpk.get("yoy") is not None and gm.get("yoy") is not None and gm["yoy"] < 0 < gpk["yoy"]:
        J.append({
            "text": f"毛利额同比仍增长 {gpk['yoy']:.2f}%，说明规模扩张带来的绝对毛利增量覆盖了"
                    f"单位盈利能力下滑，短期矛盾在「毛利率」而不在「毛利额」。",
            "basis": "T04、T01", "conf": "中高", "note": ""})
    if reg_diff:
        dom = next((x for x in reg_diff if "境内" in x["name"] or "国内" in x["name"]), None)
        ovs = next((x for x in reg_diff if "境外" in x["name"] or "国外" in x["name"]), None)
        if dom and ovs and ovs.get("gm") is not None and dom.get("gm") is not None and ovs["gm"] > dom["gm"]:
            J.append({
                "text": f"境外业务毛利率 {_fmt(ovs['gm'])}%，高于境内 {_fmt(dom['gm'])}%；"
                        f"境外占比 {_fmt(ovs['ratio'])}%（同比 {_pct(ovs['dratio'])}），"
                        f"外销占比变化会直接改变综合毛利率的基准。",
                "basis": "、".join(x for x in (reg_id.get(ovs["name"]), reg_id.get(dom["name"])) if x) or "T01",
                "conf": "中", "note": "地区口径按披露原文，未按结算币种还原"})
    if not J:
        J.append({"text": "本期指标与结构变化均不显著，暂不给归因结论。",
                  "basis": "T01", "conf": "低", "note": "样本期内变动幅度小于阈值"})

    # ---------------- 建议（经营动作，不是投资建议） ----------------
    A: list[str] = []
    if kind == "margin" and margins_ok:
        cand = [d for d in prod_diff if d["dgm"] is not None and "其他" not in d["name"]]
        worst = min(cand, key=lambda x: x["dgm"], default=None) if cand else None
        if worst:
            A.append(f"对「{worst['name']}」按客户/项目拆报价与成本，先定位是让价还是材料/制造费用上升，"
                     f"再决定是收紧价格授权还是改工艺。")
        A.append("把高毛利业务的产能与销售资源优先级前移，用结构改善对冲单位盈利下滑。")
    elif kind == "growth":
        grow = [d for d in prod_diff
                if d.get("dincome") is not None and "其他" not in d["name"] and d["dincome"] > 0]
        if grow:
            top = max(grow, key=lambda x: x["dincome"] or 0)
            A.append(f"把「{top['name']}」的订单与排产做成月度跟踪，先确认增量是持续需求还是单季交付节奏。")
        A.append("对增量业务单独设毛利率下限：若增长靠让价换量，规模越大对整体毛利的稀释越快。")
    A.append("在下一期报告中同步跟踪毛利率与产能利用率：若产能利用率下降，则单位固定成本上升可解释部分降幅。")

    # ---------------- 待验证 ----------------
    P = [
        "价格与单位成本无法从公开披露拆分：需要销售订单表、报价单与成本归集单才能定量归因。",
        "产能利用率未披露：需 MES 工时或设备运行数据，否则无法判断固定成本摊薄的影响。",
        "客户结构与议价能力未披露：需前五大客户与年度降价条款。",
    ]
    if reg_diff:
        P.append("境外毛利率受汇率与结算方式影响，需财务提供分币种收入与汇兑损益。")

    conf = "中高" if (margins_ok and gm.get("yoy") is not None and len(F) >= 6) else "中"
    residual = None
    if me is not None and gm.get("yoy") is not None:
        residual = round(gm["yoy"] - (me + xe), 2)
        if abs(residual) >= 0.4:
            P.insert(0, f"拆解未覆盖全部变动：已拆分项合计 {me + xe:+.2f} 个百分点，"
                        f"与整体 {gm['yoy']:+.2f} 个百分点仍差 {residual:+.2f}，"
                        f"差额来自无同期可比数据的业务（如新增品类）与口径调整，需逐项核对。")
    question = (f"{period} 综合毛利率 {_fmt(gm.get('val'))}%，同比 {_pct(gm.get('yoy'))}，"
                f"哪些业务在拖累？结构变化起了多大作用？") if kind == "margin" else \
               (f"{period} 收入同比 {_fmt(rev.get('yoy'), 2, '%') if rev.get('yoy') is not None else '—'}，"
                f"增长来自哪些业务与地区？")

    return {
        "company": company,
        "kind": kind,
        "period": period,
        "question": question,
        "template": TEMPLATE_NAME.get(kind, "综合体检"),
        "template_reason": kind_reason,
        "refs": {"indicators": len(inds), "facts": len(F), "events": len(linked)},
        # 溯源覆盖度：有几条事实能直接点开原文。缺链接的照实统计，不装作都能点
        "trace": {"total": len(F), "with_url": sum(1 for f in F if f.get("url"))},
        "confidence": conf,
        "facts": F,
        "judgments": J,
        "advice": A,
        "pending": P,
        "structure": st,
        "gaps": m.get("gaps") or [],
        "indicators": m.get("indicators") or [],
        "decompose": ({"margin_effect": round(me, 2), "mix_effect": round(xe, 2),
                       "residual": residual} if me is not None else None),
        "source": "定期报告 + 巨潮/东财公告资讯",
    }
