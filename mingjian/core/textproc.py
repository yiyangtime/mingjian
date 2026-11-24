"""
文本处理：HTML 转正文、分句、相似度、清洗。不依赖任何第三方库。

这里的取舍：宁可少抽一点，也不要把导航栏、版权声明混进证据。
因为下游"每条事实挂原文"的承诺，一旦引用了导航文字就立刻破产。
"""
from __future__ import annotations

import html as _html
import re

# 明显是模板噪音的标签
_DROP_TAGS = ("script", "style", "noscript", "iframe", "svg", "form", "button", "title", "head",
              "select", "nav", "header", "footer", "aside", "video", "audio",
              "canvas", "template", "code", "pre")

_BOILER_PATTERNS = [
    r"版权所有", r"免责声明", r"风险提示", r"以上内容与", r"本文不构成", r"投资建议",
    r"扫码", r"下载.*APP", r"关注.*公众号", r"点击.*查看", r"责任编辑", r"编辑[:：]",
    r"登录|注册|忘记密码", r"Copyright", r"All Rights Reserved", r"ICP[备证]",
    r"客服|联系我们|意见反馈|隐私政策|用户协议", r"相关阅读|推荐阅读|热门推荐",
    r"^上一页|^下一页|^返回|^首页", r"^分享到|^收藏|^打印",
    r"^[\d\s\.\-—/年月日:：]+$", r"^[|｜·•\-\s]+$",
]
_BOILER_RE = re.compile("|".join(_BOILER_PATTERNS), re.I)

_CJK = re.compile(r"[\u4e00-\u9fff]")


def unescape(s: str) -> str:
    s = _html.unescape(s or "")
    return (s.replace("\u00a0", " ").replace("\u3000", " ")
             .replace("\r\n", "\n").replace("\r", "\n"))


def cjk_ratio(s: str) -> float:
    if not s:
        return 0.0
    return len(_CJK.findall(s)) / max(len(s), 1)


def cjk_count(s: str) -> int:
    return len(_CJK.findall(s or ""))


def strip_tags(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"[ \t]+", " ", unescape(s)).strip()


def html_to_text(html: str) -> str:
    """整页转文本，块级元素保留换行。"""
    s = html or ""
    s = re.sub(r"<!--.*?-->", " ", s, flags=re.S)
    for tag in _DROP_TAGS:
        s = re.sub(rf"<{tag}\b[^>]*>.*?</{tag}>", " ", s, flags=re.S | re.I)
        s = re.sub(rf"<{tag}\b[^>]*/?>", " ", s, flags=re.I)
    # 换行标记
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</(p|div|li|tr|h[1-6]|section|article|blockquote|td)>", "\n", s, flags=re.I)
    s = re.sub(r"<li\b[^>]*>", "\n· ", s, flags=re.I)
    s = strip_tags(s)
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in s.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def _paragraphs(html: str) -> list[str]:
    out = []
    for m in re.finditer(r"<p\b[^>]*>(.*?)</p>", html or "", re.S | re.I):
        t = strip_tags(m.group(1))
        if t:
            out.append(t)
    return out


def page_title(html: str) -> str:
    for pat in (r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']',
                r'<meta[^>]+name=["\']title["\'][^>]+content=["\'](.*?)["\']',
                r"<title[^>]*>(.*?)</title>"):
        m = re.search(pat, html or "", re.S | re.I)
        if m:
            t = strip_tags(m.group(1))
            t = re.sub(r"[_\-|｜]\s*(新浪财经|东方财富网?|证券时报|中国证券网|和讯网|同花顺.*|网易财经|搜狐财经|腾讯新闻|凤凰网.*)$", "", t).strip()
            if t:
                return t
    return ""


def is_meaningful_line(line: str) -> bool:
    if len(line) < 10:
        return bool(re.search(r"[。！？]$", line)) and len(line) >= 8
    if _BOILER_RE.search(line) and len(line) < 45:
        return False
    if cjk_ratio(line) < 0.25 and not re.search(r"\d", line):
        return False
    return True


def extract_main(html: str, title: str = "") -> str:
    """
    正文抽取：优先 <p> 聚合，其次整页去噪。
    返回纯文本（段落用 \\n 分隔）。
    """
    paras = _paragraphs(html)
    para_text = "\n".join(p for p in paras if is_meaningful_line(p))
    text = para_text if cjk_count(para_text) >= 250 else \
        "\n".join(ln for ln in html_to_text(html).split("\n") if is_meaningful_line(ln))
    return dedupe_lines(_drop_title_lines(text, title))


_SITE_SUFFIX = re.compile(r"\s*[_\-|｜]\s*[^_\-|｜]{2,12}(网|财经|新闻|证券报|时报|资讯|客户端)?\s*$")


def _drop_title_lines(text: str, title: str) -> str:
    """去掉与页面标题重复的行（抓正文时常把 <title> 一起抓进来）。"""
    if not title:
        return text
    t = norm(title)
    out = []
    for ln in text.split("\n"):
        cand = _SITE_SUFFIX.sub("", ln).strip()
        if norm(cand) and similarity(norm(cand), t, k=2) > 0.9:
            continue
        out.append(ln)
    return "\n".join(out)


def dedupe_lines(text: str) -> str:
    seen, out = set(), []
    for ln in text.split("\n"):
        k = ln.strip()
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(k)
    return "\n".join(out)


_BOUNDARY = re.compile(r"(?<=[。！？；!?;])|\n+")


def split_sents(text: str, min_len: int = 8, max_len: int = 220) -> list[str]:
    out = []
    for chunk in _BOUNDARY.split(text or ""):
        c = (chunk or "").strip()
        if not c or len(c) < min_len:
            continue
        if len(c) > max_len:
            for sub in re.split(r"(?<=[、，,])", c):
                sub = sub.strip()
                if len(sub) >= min_len:
                    out.append(sub[:max_len])
        else:
            out.append(c)
    return out


def norm(s: str) -> str:
    s = re.sub(r"[\s\u3000]+", "", s or "")
    s = re.sub(r"[，。、；：！？\"'（）()\[\]【】《》·\-—…,.;:!?]", "", s)
    return s


def shingles(s: str, k: int = 3) -> set[str]:
    n = norm(s)
    if len(n) <= k:
        return {n} if n else set()
    return {n[i:i + k] for i in range(len(n) - k + 1)}


def similarity(a: str, b: str, k: int = 3) -> float:
    A, B = shingles(a, k), shingles(b, k)
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


def best_quote(text: str, keywords: list[str], *, prefer_money: bool = False) -> str:
    """
    在正文中挑最能支撑某类事实的句子。

    要躲开的坑：PDF 和网页正文里充满了法条引用与栏目小标题，
    它们往往同时命中关键词却不是"这件事"本身。
    所以这里对"像标题的短行""根据/依据开头"的句子直接扣分。
    """
    sents = split_sents(text)
    if not sents:
        return ""
    _LEGAL = re.compile(r"^(根据|依据|按照|遵循|参照|符合|本公司|特此)|"
                        r"(保证.{0,12}(真实|准确|完整)|虚假记载|误导性陈述|重大遗漏)")
    scored = []
    for i, sent in enumerate(sents):
        if looks_tabular(sent):
            continue
        sc = 0.0
        sc += 2.0 * sum(1 for k in keywords if k and k in sent)
        if prefer_money and re.search(r"\d+(?:\.\d+)?\s*(?:亿元|万元|亿|万|吨|GWh|MWh|%|％)", sent):
            sc += 3.0
        if 24 <= len(sent) <= 120:
            sc += 1.5
        if len(sent) < 16:
            sc -= 2.0
        if _LEGAL.match(sent):
            sc -= 2.5
        if not re.search(r"[。！？；]$", sent) and len(sent) < 26:
            sc -= 1.5
        if sc > 0:
            scored.append((sc, -i, sent))
    if not scored:
        return ""
    scored.sort(reverse=True)
    return scored[0][2]


def looks_tabular(s: str) -> bool:
    """
    判断一段文字是不是"表格被拍平"的产物。

    财报、产销快报类 PDF 没有版面信息，抽出来是
    "单位：辆 产量 销量 本月 去年同月 本年累计 643 377 450..." 这种东西。
    它不能当证据句——读者看不出在说什么。
    """
    if not s or len(s) < 20:
        return False
    has_stop = "。" in s or "！" in s or "？" in s
    # 有完整句号且有一定长度的，视为正常句子（即使数字很多，如业绩表述）
    if has_stop and len(s) >= 30:
        return False
    digits = sum(ch.isdigit() for ch in s)
    if digits / len(s) > 0.45:
        return True
    if len(re.findall(r"\d+(?:\.\d+)?", s)) >= 6 and not has_stop:
        return True
    if s.count(" ") >= 10 and not has_stop:
        return True
    return False


def clip(s: str, n: int = 120) -> str:
    s = re.sub(r"\s+", " ", (s or "")).strip()
    return s if len(s) <= n else s[:n - 1] + "…"
