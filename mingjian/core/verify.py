"""判断保真校验：模型只许改写语气，不许改写事实。

引用编号只能证明「这句话引用了某条事实」，证明不了「这句话被该事实支持」。
这里补四层可机检的约束：

1. 数字保真：改写句里的金额、规模、百分比与单位，必须能在被引用事实
   （或对应的规则判断）中找到；中文数字先归一成阿拉伯数字再比，
   防止「八亿元」绕过「5亿元」的校验；
2. 否定与方向：规则判断里的「尚未 / 并未」和「上升 / 下降」不能被改写反转，
   也不能凭空引入事实里没有的方向；
3. 表述覆盖：整句与引用材料要有足够的字符二元组重合，逐个子句还要与
   原判断或引用事实对得上，防止把一句话整个替换成另一套说法；
4. 口径冲突：按「金额 / 产能 / 占比」分指标比对，而不是全句数字求交。

任何一条不过就丢弃该条判断，由调用方回退规则版本——宁可句子笨一点，
也不接受模型把 5 亿元写成 8 亿元。本模块只做纯文本比对，不依赖模型与网络。
"""
from __future__ import annotations

import re

from .textproc import norm

# 单位按最长优先排列，避免「亿元」被拆成「亿」+「元」
_UNITS = ("个百分点", "亿美元", "亿元", "万元", "万辆", "万台", "万吨",
          "GWh", "MWh", "GW", "MW", "%", "％", "亿", "万", "元", "吨", "倍")
_UNIT_RE = "|".join(re.escape(u) for u in _UNITS)
_STRONG = re.compile(r"(\d+(?:\.\d+)?)\s*(" + _UNIT_RE + r")")
_NUM = re.compile(r"\d+(?:\.\d+)?")

# 金额统一折算成「元」再比，避免 5 亿元与 50000 万元被误判成冲突
_MONEY = {"亿美元": ("美元", 1e8), "亿元": ("元", 1e8), "亿": ("元", 1e8),
          "万元": ("元", 1e4), "万": ("元", 1e4), "元": ("元", 1.0),
          "美元": ("美元", 1.0)}

# ---------------- 中文数字 ----------------

_CN = "零〇一二两三四五六七八九十百千万亿"
_CN_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNIT = {"十": 10, "百": 100, "千": 1000}
_CN_FIG = re.compile(r"((?![万亿])[" + _CN + r"]+(?:点[零〇一二三四五六七八九]+)?)"
                     r"(?=\s*(?:" + _UNIT_RE + r"))")
_CN_PCT = re.compile(r"百分之([" + _CN + r"]+(?:点[零〇一二三四五六七八九]+)?)")
_CN_CHENG = re.compile(r"([" + _CN + r"]+)成(?!不变)")
_CN_YEAR = re.compile(r"([〇零一二三四五六七八九]{4})年")


def _cn_section(s: str) -> int:
    """千以内中文数字 → 整数：十五 → 15，一百零五 → 105。"""
    total, num = 0, 0
    for ch in s:
        if ch in _CN_DIGIT:
            num = _CN_DIGIT[ch]
        elif ch in _CN_UNIT:
            total += (num or 1) * _CN_UNIT[ch]
            num = 0
    return total + num


def _cn_arabic(s: str) -> str:
    """中文数字 → 阿拉伯写法，保留亿 / 万量级：八亿 → 8亿，三千万 → 3000万。"""
    if "点" in s:
        head, tail = s.split("点", 1)
        digits = "".join(str(_CN_DIGIT.get(ch, 0)) for ch in tail)
        return f"{_cn_section(head) if head else 0}.{digits}".rstrip("0").rstrip(".")
    if "亿" in s:
        head, tail = s.split("亿", 1)
        return f"{_cn_section(head) or 1}亿" + (_cn_arabic(tail) if tail else "")
    if "万" in s:
        head, tail = s.split("万", 1)
        return f"{_cn_section(head) or 1}万" + (_cn_arabic(tail) if tail else "")
    return str(_cn_section(s))


def _normalize_cn(text: str) -> str:
    """只归一「中文数字 + 单位」与「百分之X / X成 / 中文年份」，不动普通用字。"""
    t = _CN_PCT.sub(lambda m: f"{_cn_arabic(m.group(1))}%", text)
    t = _CN_CHENG.sub(lambda m: f"{float(_cn_arabic(m.group(1))) * 10:g}%", t)
    t = _CN_YEAR.sub(lambda m: "".join(str(_CN_DIGIT[ch]) for ch in m.group(1)) + "年", t)
    return _CN_FIG.sub(lambda m: _cn_arabic(m.group(1)), t)


def _clean(text: str) -> str:
    trans = str.maketrans("０１２３４５６７８９．，", "0123456789.,")
    t = (text or "").translate(trans).replace(",", "").replace("，", "")
    return _normalize_cn(t)


def _num(v: str) -> str:
    """数字归一化：03 → 3，5.0 → 5，5.50 → 5.5。"""
    try:
        f = float(v)
    except ValueError:
        return v
    if abs(f - round(f)) < 1e-9:
        return str(int(round(f)))
    return ("%.6f" % f).rstrip("0").rstrip(".")


def _figure_key(value: str, unit: str) -> str:
    unit = unit.replace("％", "%")
    if unit in _MONEY:
        cur, factor = _MONEY[unit]
        return f"{_num(str(float(value) * factor))}{cur}"
    return f"{_num(value)}{unit}"


def figure_items(text: str) -> list[tuple[str, str]]:
    """带单位的数字：返回 (归一化键, 原文写法)，用于跨来源比对与冲突说明。"""
    out = []
    for m in _STRONG.finditer(_clean(text)):
        raw = f"{_num(m.group(1))}{m.group(2).replace('％', '%')}"
        out.append((_figure_key(m.group(1), m.group(2)), raw))
    return out


def figures(text: str) -> set[str]:
    return {k for k, _ in figure_items(text)}


def _family(key: str) -> str:
    """归一化键 → 指标族：金额（元 / 美元）、占比（%）、产能（万吨）等。"""
    suffix = re.sub(r"^[0-9.]+", "", key) or key
    return "金额" if suffix in ("元", "美元") else suffix


def figure_groups(text: str) -> dict[str, list[tuple[str, str]]]:
    """按指标族分组，供同指标口径比对：金额只和金额比，产能只和产能比。"""
    groups: dict[str, list[tuple[str, str]]] = {}
    for key, raw in figure_items(text):
        groups.setdefault(_family(key), []).append((key, raw))
    return groups


def _strip_figures(text: str) -> str:
    return _STRONG.sub(" ", _clean(text))


def numbers(text: str) -> set[str]:
    """去掉带单位数字后剩下的裸数字（年份、条数等）；中文年份已归一。"""
    return {_num(v) for v in _NUM.findall(_strip_figures(text))}


# ---------------- 否定与方向 ----------------

# 只收「事件层面」的否定，不收「无法 / 不足以」这类认识论表述：
# 后者是判断句的限定语，模型换个同义词不算改事实。
_NEG = ("尚未", "并未", "未曾", "未发生", "未出现", "未披露", "未提及",
        "没有发生", "不存在", "否认", "暂停", "终止", "取消")
_UP = ("上升", "增加", "增长", "提高", "上调", "提升", "扩大", "扩张", "加速",
       "回暖", "走强", "高于", "超过", "突破", "创新高", "翻倍", "翻番")
_DOWN = ("下降", "减少", "降低", "下调", "下滑", "回落", "收缩", "减产",
         "放缓", "走弱", "低于", "不足", "腰斩", "亏损")


def polarity(text: str) -> tuple[bool, int]:
    """返回 (是否含事件否定, 方向 +1/-1/0)。"""
    t = text or ""
    neg = any(w in t for w in _NEG)
    up = sum(1 for w in _UP if w in t)
    down = sum(1 for w in _DOWN if w in t)
    return neg, (1 if up > down else -1 if down > up else 0)


# ---------------- 表述重合 ----------------

_CLAUSE = re.compile(r"[，。；;！？!?]")


def grams(text: str, k: int = 2) -> set[str]:
    s = norm(text)
    if len(s) < k:
        return {s} if s else set()
    return {s[i:i + k] for i in range(len(s) - k + 1)}


def _coverage(text: str, ref: str) -> float:
    g = grams(text)
    return len(g & grams(ref)) / max(1, len(g))


def _clause_gap(text: str, refs: list[str], min_cov: float) -> tuple[str, float]:
    """逐子句核对：任何一句都不许和原判断 / 引用事实完全对不上。"""
    for clause in _CLAUSE.split(text or ""):
        c = clause.strip()
        if len(norm(c)) < 8:
            continue
        best = max((_coverage(c, r) for r in refs if r), default=0.0)
        if best < min_cov:
            return c, best
    return "", 0.0


def support(text: str, sources: list[str], *, baseline: str = "",
            min_coverage: float = 0.10, min_clause: float = 0.15) -> dict:
    """检查一句话是否被给定材料支持。

    sources 是被引用事实的原文（陈述 + 证据句 + 金额 + 日期）；
    baseline 是这条判断对应的规则判断原文：数字可从它取，否定与方向必须与它一致，
    子句也要与它或引用事实对得上。
    返回 {"ok", "coverage", "reason"}，reason 直接写进判断留痕供人工复核。
    """
    srcs = [s for s in sources if s]
    src = "\n".join(srcs)
    if not src:
        return {"ok": False, "coverage": 0.0, "reason": "没有可核对的事实原文"}
    whitelist = "\n".join([src, baseline])

    missing = figures(text) - figures(whitelist)
    if missing:
        labels = []
        for key, raw in figure_items(text):
            if key in missing and raw not in labels:
                labels.append(raw)
        return {"ok": False, "coverage": 0.0,
                "reason": f"出现引用事实中没有的数字：{'、'.join(labels)}"}
    bad_num = sorted(numbers(text) - numbers(whitelist))
    if bad_num:
        return {"ok": False, "coverage": 0.0,
                "reason": f"出现引用事实中没有的数字：{'、'.join(bad_num)}"}

    if baseline:
        bn, bd = polarity(baseline)
        tn, td = polarity(text)
        if bn and not tn:
            return {"ok": False, "coverage": 0.0, "reason": "改掉了原判断的否定表述"}
        if tn and not bn and not polarity(src)[0]:
            return {"ok": False, "coverage": 0.0, "reason": "引入了引用事实里没有的否定表述"}
        if bd and td and bd != td:
            return {"ok": False, "coverage": 0.0, "reason": "判断方向与原判断相反"}
        if td and not bd and polarity(src)[1] not in (td, 0):
            return {"ok": False, "coverage": 0.0, "reason": "引入了引用事实里没有的方向判断"}

    cov = _coverage(text, src)
    if cov < min_coverage:
        return {"ok": False, "coverage": round(cov, 3),
                "reason": f"与引用事实的表述重合度过低（{cov:.0%}），疑似改写过度"}
    if baseline:
        clause, cc = _clause_gap(text, [baseline, *srcs], min_clause)
        if clause:
            return {"ok": False, "coverage": round(cov, 3),
                    "reason": f"子句与原判断/引用事实对不上（{cc:.0%}）：{clause[:30]}"}
    return {"ok": True, "coverage": round(cov, 3), "reason": ""}
