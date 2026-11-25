"""
成稿：把事实组织成一份可以直接引用的企业洞察报告。

结构固定五段——事实 / 变化 / 判断 / 建议 / 待验证。
这不是排版偏好，是防止"AI 把推测写成结论"的结构性约束：
判断段里每一句都必须挂事实编号，挂不上就不许写。
读者因此可以精确地反驳某一条判断，而不必推翻整篇。
"""
from __future__ import annotations

import time

from . import extract as EX

ORDER = ["capacity", "order", "price", "product", "performance", "finance",
         "policy", "personality", "personnel", "risk", "other"]
TYPE_ORDER = ["capacity", "order", "product", "price", "performance", "finance", "policy", "risk", "personnel", "other"]

CONF_LABEL = [(0.85, "高"), (0.70, "中高"), (0.60, "中"), (0.45, "中低"), (0.0, "低")]


def conf_label(v: float) -> str:
    for th, label in CONF_LABEL:
        if v >= th:
            return label
    return "低"


def judgments(facts: list[dict], profile: dict) -> list[dict]:
    """
    规则化判断。只在"同一方向上有足够多证据"时才下判断，
    否则明确写"证据不足以判断"，而不是硬凑一句听起来专业的话。
    """
    by_type: dict[str, list[dict]] = {}
    for f in facts:
        by_type.setdefault(f["type"], []).append(f)

    out: list[dict] = []

    def top(t: str, n: int = 3) -> list[dict]:
        return sorted(by_type.get(t, []), key=lambda x: -x["confidence"])[:n]

    cap, order, price = by_type.get("capacity", []), by_type.get("order", []), by_type.get("price", [])
    perf, fin, pol = by_type.get("performance", []), by_type.get("finance", []), by_type.get("policy", [])
    risk = by_type.get("risk", [])

    if len(cap) >= 2:
        ids = [f["id"] for f in top("capacity")]
        out.append({
            "text": f"产能端动作明显密集（本期 {len(cap)} 条），且集中在既有基地扩建与项目落地，"
                    f"说明扩张仍在推进而非收缩。",
            "citations": ids, "conf": 0.78 if len(cap) >= 3 else 0.66,
        })
    if len(order) >= 2:
        ids = [f["id"] for f in top("order")]
        out.append({
            "text": f"订单与合作事项连续出现（本期 {len(order)} 条），是需求端最直接的验证信号；"
                    f"但仅凭标题无法判断合同金额对收入的贡献比例。",
            "citations": ids, "conf": 0.74 if len(order) >= 3 else 0.64,
        })
    if cap and order:
        out.append({
            "text": "扩产与订单在同一窗口内同时出现，供需两端方向一致，"
                    "属于典型的产能爬坡前兆；需用下一期投产进度验证。",
            "citations": [cap[0]["id"], order[0]["id"]], "conf": 0.62,
        })
    if cap and price:
        out.append({
            "text": "同时出现扩产与价格/成本相关表述，存在产能释放压制价格的可能，"
                    "这是本期最需要跟踪的矛盾点。",
            "citations": [cap[0]["id"], price[0]["id"]], "conf": 0.55,
        })
    if perf:
        f = perf[0]
        amt = f"（涉及 {f['amount']}）" if f.get("amount") else ""
        out.append({
            "text": f"经营口径信息出现{amt}，可作为判断景气度的硬指标，"
                    f"但其口径（合并/母公司、累计/单季）需按财务定义确认后才能同比。",
            "citations": [f["id"]], "conf": 0.6,
        })
    if fin:
        out.append({
            "text": f"资本运作类事项 {len(fin)} 条，涉及股权或融资安排，"
                    f"通常领先于产能与研发投入 1—2 个季度。",
            "citations": [f["id"] for f in top("finance")], "conf": 0.66,
        })
    if pol:
        out.append({
            "text": f"出现政策相关事项 {len(pol)} 条，需判断是否可直接对应到可申报项目或补贴口径，"
                    f"否则只是背景信息，不构成经营影响。",
            "citations": [f["id"] for f in top("policy")], "conf": 0.58,
        })
    if risk:
        out.append({
            "text": "出现风险类事项，建议单独立项跟踪，不与经营进展混在同一结论里。",
            "citations": [f["id"] for f in top("risk")], "conf": 0.7,
        })

    multi = [f for f in facts if f.get("corroboration", 1) >= 2]
    if multi:
        out.append({
            "text": f"{len(multi)} 条事实获得两个及以上来源交叉印证，可视为本期确定性最高的部分。",
            "citations": [f["id"] for f in multi[:4]], "conf": 0.72,
        })

    if not out:
        out.append({
            "text": "本期未形成可下判断的证据密度（同类事件不足两条），"
                    "按规则不输出方向性结论，仅保留事实清单。",
            "citations": [f["id"] for f in sorted(facts, key=lambda x: -x["confidence"])[:2]],
            "conf": 0.4,
        })
    return out


def suggestions(jlist: list[dict], facts: list[dict], gaps: list[str]) -> list[str]:
    types = {f["type"] for f in facts}
    out: list[str] = []
    if "capacity" in types:
        out.append("核对产能类事项的投产时间表与爬坡节奏，把「签约/开工」与「实际产出」分开记账。")
    if "order" in types:
        out.append("对订单类事项补录合同金额、交付周期与客户集中度，否则无法折算为收入贡献。")
    if "price" in types:
        out.append("建立价格与成本的周度跟踪口径（同一产品、同一规格），用于验证扩产是否开始压制价格。")
    if "policy" in types:
        out.append("把政策事项逐条比对可申报条件，标注「可申报/不适用/待确认」，避免把背景信息当成机会。")
    if "risk" in types:
        out.append("风险事项单独成表并指定责任人，不并入经营进展的结论。")
    if any("无历史基线" in g for g in gaps):
        out.append("连续运行两期后再看变化比对——单期数据无法判断节奏加快或放缓。")
    if any("摘要" in g for g in gaps):
        out.append("对仅有摘要级证据的事项，回链原文补齐证据句后再引用进正式汇报。")
    return out[:6]


def gaps(facts: list[dict], docs: list[dict], changes: dict, entity: dict) -> list[str]:
    out: list[str] = []
    if not changes.get("has_baseline"):
        out.append("无历史基线：本次为首次运行，变化比对不可用，所有条目均按「首次纳入」处理。")
    kinds = [d.get("kind") for d in docs]
    if "announcement" not in kinds:
        out.append("未取得交易所公告：事实来源以媒体为主，缺少一手文件，建议核查公告抓取是否被限流。")
    if not any(f["type"] == "performance" for f in facts):
        out.append("缺少经营业绩类信息：无法判断增长质量，需接入财务数据或补充财报检索。")
    if not any(f["type"] == "policy" for f in facts):
        out.append("未检索到适用政策：可能确实无相关政策，也可能是检索词未覆盖，需人工确认一次。")
    full = [d for d in docs if d.get("evidence_level") == "full"]
    if docs and len(full) / max(len(docs), 1) < 0.4:
        out.append(f"证据等级偏低：{len(full)}/{len(docs)} 份材料取得正文，其余仅有标题或摘要，"
                   f"引用前需回看原文。")
    img_pdf = [d for d in docs if d.get("evidence_level") == "title" and (d.get("url") or "").lower().endswith(".pdf")]
    if img_pdf:
        out.append(f"{len(img_pdf)} 份公告 PDF 为图片型（无文本层），程序无法读取，已保留链接供人工查阅。")
    if not entity.get("code"):
        out.append("未匹配到上市主体：非上市公司或名称不精确，公告与行情数据缺失。")
    return out


def render_markdown(*, company: str, entity: dict, profile: dict, quote: dict,
                    facts: list[dict], docs: list[dict], changes: dict,
                    gaps_list: list[str], sug: list[str], judges: list[dict],
                    run_meta: dict) -> str:
    ts = run_meta.get("finished_at") or time.strftime("%Y-%m-%d %H:%M")
    L: list[str] = []
    A = L.append

    A(f"# {company} · 企业洞察报告")
    A("")
    A(f"> 生成时间：{ts} ｜ 覆盖窗口：近 {run_meta.get('days', 30)} 天 ｜ "
      f"材料：{len(docs)} 份（公告 {sum(1 for d in docs if d['kind'] == 'announcement')}，"
      f"资讯 {sum(1 for d in docs if d['kind'] == 'news')}） ｜ 事实：{len(facts)} 条")
    A(f"> 数据来源：巨潮资讯（公告）、东方财富（资讯/公司档案/行情） ｜ "
      f"事实可回溯率：{sum(1 for f in facts if f.get('doc_id'))}/{len(facts)}")
    if run_meta.get("model_used"):
        A(f"> 模型：{run_meta['model_used']}（仅用于润色判断段，事实与引用由程序生成并校验）")
    else:
        A("> 模型：未配置，判断段由规则引擎生成（结论仍逐条挂事实编号）")
    A("")

    # 一、速览
    A("## 一、公司速览")
    A("")
    A("| 项目 | 内容 |")
    A("|---|---|")
    if entity.get("code"):
        A(f"| 证券代码 | {entity['code']}（{entity.get('market') or '—'}） |")
    A(f"| 公司全称 | {profile.get('full_name') or '—'} |")
    A(f"| 所属行业 | {profile.get('industry') or '—'} |")
    if profile.get("main_business"):
        A(f"| 主营业务 | {profile['main_business'][:110]} |")
    if profile.get("address"):
        A(f"| 注册地 | {profile['address'][:60]} |")
    # 不写股价与市值：这是产业情报，不是行情报告
    A(f"| 本期事实 | {len(facts)} 条，其中高置信 {sum(1 for f in facts if f['confidence'] >= 0.85)} 条 |")
    A("")

    # 二、要点
    A("## 二、本期要点")
    A("")
    if judges:
        for i, j in enumerate(judges[:4], 1):
            cites = "、".join(j["citations"])
            A(f"{i}. {j['text']}（依据 {cites}，置信度 {conf_label(j['conf'])}）")
    else:
        A("（本期无足够证据形成要点）")
    A("")

    # 三、事实
    A("## 三、事实清单")
    A("")
    A("> 每条事实挂来源编号，正文中引用请保留编号，便于回查原文。")
    A("")
    groups: dict[str, list[dict]] = {}
    for f in facts:
        groups.setdefault(f["type"], []).append(f)
    for t in TYPE_ORDER:
        if t not in groups:
            continue
        A(f"### {EX.LABEL.get(t, t)}")
        A("")
        for f in sorted(groups[t], key=lambda x: -x["confidence"]):
            badge = f" · 佐证 {f['corroboration']} 源" if f.get("corroboration", 1) > 1 else ""
            amt = f" ｜ {f['amount']}" if f.get("amount") else ""
            A(f"- **[{f['id']}]** {f['statement']}{amt}")
            A(f"  - 证据：{f['evidence_quote']}")
            A(f"  - 来源：{f.get('source') or '—'}，{f.get('published_at') or '—'}，"
              f"证据等级 {f.get('evidence_level')}，置信度 {f['confidence']:.2f}{badge}")
            if f.get("url"):
                A(f"  - 原文：<{f['url']}>")
        A("")

    # 四、变化
    A("## 四、变化对比")
    A("")
    if not changes.get("has_baseline"):
        A("首次运行，无历史基线。本期全部条目按「首次纳入」处理，不做「新增/升温」判断。")
    else:
        A(f"对比基线：上次运行 {changes.get('baseline_run_at', '—')} 的 {changes['baseline_count']} 条事实。")
        A("")
        if changes["escalated"]:
            A("**升温（置信度上升，说明获得更多佐证）**")
            for f in changes["escalated"]:
                A(f"- [{f['id']}] {f['statement']}（置信度 {f['prev_confidence']:.2f} → {f['confidence']:.2f}）")
            A("")
        if changes["new"]:
            A(f"**本期新增 {len(changes['new'])} 条**")
            for f in changes["new"][:8]:
                A(f"- [{f['id']}] {f['statement']}")
            A("")
        if changes["faded"]:
            A(f"**本轮未见（上次出现、本次窗口内未再检索到，共 {len(changes['faded'])} 条）**")
            for p in changes["faded"][:5]:
                A(f"- {p.get('statement', '')}")
            A("")
        if not (changes["escalated"] or changes["new"] or changes["faded"]):
            A("无变化。")
    A("")

    # 五、判断
    A("## 五、判断")
    A("")
    A("> 判断为推断，不等同事实。每条标注依据的事实编号与置信度，可被单独质疑与推翻。")
    A("")
    for j in judges:
        A(f"- {j['text']}")
        A(f"  - 依据：{'、'.join(j['citations'])} ｜ 置信度：{conf_label(j['conf'])}（{j['conf']:.2f}）"
          f"{' ｜ 由模型润色，引用已校验' if j.get('source') == 'llm' else ''}")
    A("")

    # 六、建议
    A("## 六、建议动作")
    A("")
    if sug:
        for i, s in enumerate(sug, 1):
            A(f"{i}. {s}")
    else:
        A("本期无需特别动作。")
    A("")

    # 七、待验证
    A("## 七、待验证与数据缺口")
    A("")
    if gaps_list:
        for g in gaps_list:
            A(f"- {g}")
    else:
        A("- 本期未发现明显缺口。")
    A("")

    # 八、方法
    A("## 八、方法与口径")
    A("")
    A("**证据等级**")
    A("")
    A("| 等级 | 含义 | 置信度基线 |")
    A("|---|---|---|")
    A("| full | 取得原文正文（公告 PDF 文本层 / 资讯正文） | 公告 0.90 / 资讯 0.72 |")
    A("| snippet / title | 仅有检索摘要或标题（图片型 PDF、正文抓取失败） | 公告 0.70 / 资讯 0.55 |")
    A("| 其它 | 非公告、非资讯材料 | 0.52 |")
    A("")
    A("**置信度构成**：证据等级基线 + 0.04 × 事件判定强度 + 含金额 0.03 + 公告全文长文 0.02"
      " + 多源佐证加分；上限 0.98，低于 0.45 只进缺口、不进事实区。")
    A("")
    A("**已知局限**")
    A("")
    A("- 事实来自公开信息，不含内部经营数据，不能替代财务口径。")
    A("- 事件分类基于关键词规则，对反讽、否定句（如「并未扩产」）可能误判，需人工抽检。")
    A("- 图片型公告 PDF 无法读取文本，仅保留链接。")
    A("- 资讯检索受搜索引擎收录与限流影响，同一查询在不同时间可能返回不同集合。")
    A("")
    A("---")
    A("")
    A(f"*本报告由 Agent 自动生成。运行编号 {run_meta.get('run_id', '')}。"
      f"所有事实可在运行记录中按编号回查原文。*")
    return "\n".join(L)
