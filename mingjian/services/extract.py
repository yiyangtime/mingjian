"""
事实抽取引擎。

这是整套系统里最需要"谨慎"的一层。原则三条：

1. 只抽能落到一句原文的事实。抽不到证据句，就不产事实。
2. 不确定就降置信度，不猜。缺失字段留空，绝不用"推测值"填充。
3. 区分"硬事实"与"软信号"：公告（有发文主体）> 媒体报道正文 > 检索摘要。
   三者置信度基线不同，报告里也必须能看出来。

事件类型集合直接对应情报工作的实际阅读习惯——
读一份周报时，人其实在找的就是这几类变化。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..core.textproc import best_quote, cjk_count, clip, looks_tabular, norm, similarity, strip_tags
from ..infra import optstore

# ---------------- 事件类型 ----------------

EVENT_TYPES: list[tuple[str, str, tuple[str, ...]]] = [
    ("capacity", "产能建设", ("扩产", "投产", "产能", "基地", "开工", "建成", "产线", "扩建",
                          "试产", "达产", "工厂", "项目开工", "新增产能", "产能爬坡")),
    ("order", "订单合作", ("订单", "中标", "签约", "合同", "供货", "合作", "协议", "框架",
                        "定点", "配套", "采购", "战略合作", "长期供应")),
    ("price", "价格成本", ("降价", "涨价", "调价", "报价", "价格", "成本", "毛利率", "碳酸锂",
                        "让利", "提价", "跌幅", "涨幅")),
    ("product", "产品技术", ("发布", "推出", "量产", "新品", "研发", "专利", "首发",
                          "固态电池", "钠离子", "新车型", "认证", "突破")),
    ("finance", "资本运作", ("定增", "回购", "增持", "减持", "分红", "可转债", "融资", "并购",
                          "收购", "募资", "配股", "股权激励", "员工持股", "H股", "上市",
                          "质押", "解押", "解除质押", "股份变动", "股东大会", "股东会",
                          "权益变动", "募集资金", "现金管理", "闲置资金")),
    ("performance", "经营业绩", ("营收", "净利润", "毛利", "业绩", "财报", "预增", "扭亏",
                             "同比增长", "季报", "年报", "半年度", "季度报告", "年度报告",
                             "营业收入", "亏损", "盈利")),
    ("policy", "政策法规", ("政策", "补贴", "标准", "规划", "工信部", "发改委", "通知", "办法",
                         "指导意见", "试点", "目录", "关税", "出口管制", "征求意见")),
    ("risk", "风险事件", ("诉讼", "处罚", "召回", "事故", "火灾", "停产", "调查", "退市",
                       "亏损", "仲裁", "冻结", "违规", "警示", "问询")),
    ("personnel", "人事变动", ("辞职", "任命", "董事", "高管", "换届", "总经理", "总裁",
                            "离任", "聘任", "监事会", "逝世", "去世")),
]

LABEL = {k: v for k, v, _ in EVENT_TYPES}
KW = {k: v for k, _, v in EVENT_TYPES}


def default_rules() -> list[dict]:
    """内置规则的可序列化副本；离线评测与版本回滚都以它为基线。"""
    return [{"type": key, "label": label, "keywords": list(kws)}
            for key, label, kws in EVENT_TYPES]


def normalize_rules(rules) -> list[dict] | None:
    """校验并归一化规则。

    只接受完整的事件类型集合，避免一个候选漏掉某类后把该类永久判成 other。
    关键词按首次出现去重；标签固定用内置值，避免报告里的类型名与事实类型脱节。
    """
    if not isinstance(rules, (list, tuple)) or not rules:
        return None
    by_type: dict[str, dict] = {}
    for item in rules:
        if not isinstance(item, dict):
            return None
        key = str(item.get("type") or "").strip()
        if key not in LABEL or key in by_type:
            return None
        raw = item.get("keywords")
        if not isinstance(raw, (list, tuple)):
            return None
        kws = []
        for word in raw:
            w = str(word or "").strip()
            if w and w not in kws:
                kws.append(w)
        if not kws or len(kws) > 200:
            return None
        by_type[key] = {"type": key, "label": LABEL[key], "keywords": kws}
    if set(by_type) != set(LABEL):
        return None
    return [by_type[key] for key, _, _ in EVENT_TYPES]


def active_version() -> str:
    """当前生效版本；没有有效配置时视为内置基线。"""
    payload = optstore.load_active()
    if isinstance(payload, dict) and normalize_rules(payload.get("rules")):
        return str(payload.get("version") or "baseline")
    return "baseline"


def active_rules() -> list[dict]:
    """当前生效规则；文件损坏或校验失败时回退到内置规则，不阻断运行。"""
    payload = optstore.load_active()
    if isinstance(payload, dict):
        rules = normalize_rules(payload.get("rules"))
        if rules:
            return rules
    return default_rules()


def rules_for_version(version: str) -> list[dict]:
    """按运行开始时记录的版本取规则，续跑时不被中途上线的新版本串味。"""
    if version and version != "baseline":
        payload = optstore.load_version(version)
        if isinstance(payload, dict):
            rules = normalize_rules(payload.get("rules"))
            if rules:
                return rules
    return default_rules()

# 例行公告没有情报价值，直接跳过（否则报告会被"翌日披露报表"淹没）
ROUTINE = re.compile(
    r"翌日披露报表|证券变动月报表|月报表|会议召开日期|持续关连交易|"
    r"关于召开.*股东大会.*通知|独立董事.*候选人|章程修正案|"
    r"法律意见书|审计报告|内部控制|会计师事务所|保荐|核查意见|"
    r"募集资金.*存放|投资者关系活动"
)

# 资讯噪音：基金持仓、指数成分、股价异动这类内容提到公司，但不是公司事件
NOISE_NEWS = re.compile(
    r"ETF|基金|指数|重仓|持仓|成分股|概念股|涨停|跌停|龙虎榜|融资余额|机构调研|"
    r"个股|盘前|早报|早参|早读|晚报|收评|午评|异动|研报|评级|目标价|增持计划进展|"
    r"速览|一览|要闻|盘点|周报|月报|十大|榜单|热搜|快报|直击|资讯汇总|公告汇总|"
    r"早知道|快递|盘后|公告精选|"
    r"大宗交易|折价成交|龙虎榜|融资买入|"
    # 行情类：本系统是产业情报，不做行情终端，这类标题不该变成"产业事实"
    r"股价|平均股价|百元股|换手率|成交额|涨超|跌超|涨近|跌近|翻红|飘红|飘绿|"
    r"盘中|收盘|开盘|主力资金|北向|两融|融资净买入|涨幅|跌幅|"
    r"地天板|一字板|封单|集合竞价|涨停板"
)

# 标题本身不携带事件信息的公告（正文可读时仍有价值，仅标题时价值很低）
LOW_INFO_TITLE = re.compile(
    r"^(关于)?(第.{1,3}届)?(董事会|监事会|股东会|股东大会).{0,8}(决议|通知)?公告$|"
    r"^\d{4}年(第?.{1,2}季度|半年度|年度)报告$|^关于.{0,10}进展的公告$|"
    r".{0,20}(股东会|股东大会|董事会|监事会).{0,10}(决议|通知)公告$|"
    r"^.{0,30}\d{4}年(第?.{1,2}季度|半年度|年度)报告$"
)


@dataclass
class Fact:
    type: str
    type_label: str
    statement: str
    evidence_quote: str
    amount: str
    event_date: str
    confidence: float
    dedupe_key: str
    doc_id: str = ""
    corroboration: int = 1

    def as_dict(self) -> dict:
        return {
            "type": self.type, "type_label": self.type_label,
            "statement": self.statement, "evidence_quote": self.evidence_quote,
            "amount": self.amount, "event_date": self.event_date,
            "confidence": round(self.confidence, 2), "dedupe_key": self.dedupe_key,
            "doc_id": self.doc_id, "corroboration": self.corroboration,
        }


_MONEY = re.compile(
    r"(\d+(?:\.\d+)?)\s*(亿元|万元|亿|万|元|美元|亿美元|GWh|MWh|GW|MW|吨|万吨|万辆|万台|%|％|个百分点)")
_DATE_CN = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_DATE_ISO = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})")
_DATE_MD = re.compile(r"(?<![\d年])(\d{1,2})\s*月\s*(\d{1,2})\s*日")


def classify(title: str, body: str = "", rules: list[dict] | None = None) -> tuple[str, float]:
    """
    按关键词加权判类。标题权重 5，正文只看前 1200 字（权重 1）。

    为什么强调标题：公告标题是撰写方对事件的定性，比正文里随手提到的词可靠得多。
    之前把正文全量计入，导致"董事会决议公告"因为正文引用了股票上市规则被判成资本运作。
    """
    t = title or ""
    b = (body or "")[:1200]
    rules = normalize_rules(rules) or default_rules()
    best, best_score, best_title = "other", 0.0, 0
    for rule in rules:
        kws = rule["keywords"]
        ts = sum(5 for k in kws if k in t)
        score = ts + sum(1 for k in kws if k in b)
        if score > best_score:
            best, best_score, best_title = rule["type"], score, ts
    # 标题一个字都没命中时，正文零星出现一个词不足以为事件定性：
    # 公告正文几乎必提"董事会"，会让任何公告都被判成人事变动。
    if best_score == 0 or (best_title == 0 and best_score < 3):
        return "other", 0.0
    return best, min(1.0, best_score / 6.0)


def money_spans(text: str) -> list[str]:
    out = []
    for m in _MONEY.finditer(text or ""):
        v = f"{m.group(1)}{m.group(2)}"
        if v not in out:
            out.append(v)
        if len(out) >= 6:
            break
    return out


def dates_in(text: str, default_year: int | None = None) -> list[str]:
    out: list[str] = []
    for m in _DATE_CN.finditer(text or ""):
        out.append(f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}")
    for m in _DATE_ISO.finditer(text or ""):
        out.append(f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}")
    if not out and default_year:
        for m in _DATE_MD.finditer(text or ""):
            out.append(f"{default_year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}")
    return list(dict.fromkeys(out))


def subject_ok(text: str, names: set[str]) -> bool:
    return any(n and n in (text or "") for n in names)


def base_confidence(doc: dict) -> float:
    """
    证据等级决定基线。标题级公告只给 0.70——
    标题能证明"这件事发生了"，但证明不了细节，不应被当成高置信事实。
    """
    kind = doc.get("kind")
    level = doc.get("evidence_level")
    if kind == "announcement":
        return 0.90 if level == "full" else 0.70
    if kind == "news":
        return 0.72 if level == "full" else 0.55
    return 0.52


def build_fact(doc: dict, names: set[str], default_year: int | None = None,
               rules: list[dict] | None = None) -> Fact | None:
    """单份材料 → 一条事实（或 None）。

    `rules` 由运行时显式传入；直接调用本函数时使用内置规则，避免测试与离线
    回放被本机当前生效版本影响。
    """
    rules = normalize_rules(rules) or default_rules()
    title = strip_tags(doc.get("title") or "")
    body = doc.get("body") or ""
    if not title:
        return None
    if doc.get("kind") == "announcement" and ROUTINE.search(title):
        return None
    if doc.get("kind") == "news" and NOISE_NEWS.search(title):
        return None
    # 标题无事件信息（决议公告、季度报告、法律意见书）：抽出来只是占位，不进事实区。
    # 这类材料不是没用，而是需要人工展开——会记进报告的"待验证与数据缺口"。
    if LOW_INFO_TITLE.match(title):
        return None

    ftype, strength = classify(title, body, rules)
    blob = f"{title}\n{body}"
    kw = {rule["type"]: rule["keywords"] for rule in rules}
    labels = {rule["type"]: rule["label"] for rule in rules}

    # 主体核验：既不在标题也不在正文提到目标公司，则不认（防止检索串味）
    if not subject_ok(blob, names):
        if doc.get("kind") == "news":
            return None

    # 证据句：优先正文中最能支撑该类事件的句子，其次摘要，最后标题
    quote = ""
    if body and cjk_count(body) > 60:
        quote = best_quote(body, list(kw.get(ftype, ())), prefer_money=(ftype in ("order", "capacity", "performance", "finance")))
    if not quote and doc.get("evidence_level") == "snippet" and body:
        quote = clip(body, 200)
    if not quote or looks_tabular(quote):
        # 拿不到像样的句子就退回标题，并明确降低置信度——不假装读懂了全文
        quote = title
        conf_penalty = 0.06
    else:
        conf_penalty = 0.0
    quote = re.sub(r"^(?:原标题|摘要|导语|编者按)[:：]\s*", "", quote).strip()

    # 主体归属：新闻的**标题**必须提到目标公司。
    # 事实陈述取的就是标题，标题讲的是别家、正文的行业综述里顺带列了一句"同行还有某某"，
    # 抽出来就是张冠李戴——这类材料只能当线索，不进事实区。
    if doc.get("kind") == "news" and not subject_ok(title, names):
        return None

    conf = base_confidence(doc)
    if ftype == "other":
        conf -= 0.12
    else:
        conf += 0.04 * strength
    if money_spans(blob):
        conf += 0.03
    if doc.get("kind") == "announcement" and doc.get("evidence_level") == "full" and cjk_count(body) > 300:
        conf += 0.02
    conf = max(0.30, min(0.98, conf - conf_penalty))

    amount = "、".join(money_spans(f"{title} {quote}")[:2])
    edate = (dates_in(quote) or dates_in(title) or [doc.get("published_at", "")[:10]])[0]
    if not edate:
        edate = doc.get("published_at", "")[:10]

    return Fact(
        type=ftype, type_label=labels.get(ftype, LABEL.get(ftype, "其他")),
        statement=clip(title, 160), evidence_quote=clip(quote, 300),
        amount=amount, event_date=edate, confidence=conf,
        dedupe_key=norm(title)[:40], doc_id=doc.get("id", ""),
    )


def merge_facts(facts: list[Fact], threshold: float = 0.46) -> list[Fact]:
    """
    跨源合并：同一事件被多家报道时，保留置信度高的一条，并把佐证数累加。
    佐证数本身是信号——单源消息和五家媒体同时报，可信度不同。
    """
    kept: list[Fact] = []
    for f in sorted(facts, key=lambda x: -x.confidence):
        dup = None
        for k in kept:
            if k.type != f.type and "other" not in (k.type, f.type):
                continue
            if similarity(k.statement, f.statement) >= threshold:
                dup = k
                break
        if dup is None:
            for k in kept:
                if k.type != f.type:
                    continue
                if _shared_numbers(k.statement, f.statement) >= 2:
                    dup = k
                    break
        if dup:
            dup.corroboration += 1
            if f.confidence > dup.confidence:
                dup.confidence = min(0.98, f.confidence + 0.02 * (dup.corroboration - 1))
                dup.evidence_quote = f.evidence_quote or dup.evidence_quote
                dup.doc_id = f.doc_id or dup.doc_id
            else:
                dup.confidence = min(0.98, dup.confidence + 0.015 * (dup.corroboration - 1))
        else:
            kept.append(f)
    return kept


_NUM = re.compile(r"\d+(?:\.\d+)?")


def _shared_numbers(a: str, b: str) -> int:
    """两条陈述共有的数字个数。用于识别"同一件事、两种说法"。"""
    A = set(_NUM.findall(a or ""))
    B = set(_NUM.findall(b or ""))
    A.discard("")
    B.discard("")
    return len(A & B)
