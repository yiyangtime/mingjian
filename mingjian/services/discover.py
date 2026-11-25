"""
竞品线索发现：从产品词出发，找出"可能在同一张牌桌上"的企业。

非上市企业最常见的困境不是"查不到竞品动态"，而是"不知道该盯谁"——
没有证券代码，也就没有行业分类标签可用。所以这里反过来做：
用自己产品的品类词去公开资讯里找"谁一直在被提到"，产出候选名单，
再由人确认是不是真竞品。

产出的是**线索**，不是结论：
  · 每条候选必须带一篇能点开的原文，说明它是从哪句话里出来的；
  · 计数口径是"独立事件数"，不是"文章数"——同一件事被十家媒体转发只算一起；
  · 名单里会标出哪些是上市主体——上市的可以立刻进竞品追踪，非上市的只能靠公示类信号盯。

已知的坑（都是实测踩出来的，别再踩）：
  1. 品类词会被检索接口按词切分：查「直线导轨」会命中「直线拉升/涨停」这类行情稿。
     所以标题必须过 NOISE_NEWS，行情稿与早报/快报一律不进。
  2. 公告汇总类稿件（"重大事项公告最新快递"）一篇里几十家公司，
     抽出来全是无关主体 —— 按"单篇主体数 ≥ 5"直接判为聚合稿丢弃。
  3. 财经标题里对手常用**简称**（如「某某精密」「某工业品平台」），不带「有限公司」后缀，
     只按后缀抽取会把真对手全部漏掉、只捞到通稿里的并购标的。故补一路"标题主体位"抽取。
"""
from __future__ import annotations

import re
import time

from ..core.textproc import similarity
from ..infra.sources import cninfo, eastmoney
from .extract import NOISE_NEWS

# 企业名后缀：只认这几种，避免把"工业和信息化部""某某协会"抓进来
_SUFFIX = r"(?:集团股份有限公司|股份有限公司|有限责任公司|有限公司|集团)"
_NAME_RE = re.compile(r"[\u4e00-\u9fa5A-Za-z0-9（）()·]{2,20}" + _SUFFIX)

# 明显不是企业主体的词
_NOISE = ("该公司", "上述公司", "标的公司", "项目公司", "控股子公司", "全资子公司",
          "股份有限公司", "有限责任公司", "有限公司", "集团有限公司")

# 名字前面常粘着动作词：正则从后缀往前抓到 20 字，会把"拟以现金方式收购"一起吃进来。
# 这类词一旦出现，说明真正的名字在它之后——从最右一个动作词之后截断。
_CUT_WORDS = ("收购", "并购", "购买", "受让", "增资", "投资", "入股", "控股", "参股",
              "中标", "竞得", "供货", "配套", "关于", "旗下", "下属", "股东",
              "全资子公司", "控股子公司", "参股公司", "分公司", "子公司",
              "合伙企业", "持有的")

# 全角字母数字 → 半角：不转换的话"某某宏Ａ"与"某某宏A"会被判成两家公司
_FW = {c: c - 0xFEE0 for c in range(0xFF01, 0xFF5F)}
_FW[0x3000] = 0x20

# 标题里主体位之后的动作词：用来确认"冒号/竖线前面那个词"确实是个主语，不是栏目名
_ACTION = "拟发宣获签中推赴完启投扩募融回建成立"
# 主体位可能带全角字母（"某某宏Ａ"），漏了会让一类标题全部抽不到
_NM = r"[\u4e00-\u9fa5A-Za-z0-9*\uff21-\uff3a\uff41-\uff5a]"
_TITLE_HEAD = re.compile(r"^(" + _NM + r"{2,8})\s*[：:]")
_TITLE_SEG = re.compile(r"^(" + _NM + r"{2,8})(?=[" + _ACTION + r"])")

# 单篇抽出多少个主体以上就判定为"公告汇总稿"
_AGG_MAX = 5
# 标题相似度到这个程度就算同一件事
_SAME_EVENT = 0.72


def _clean_name(s: str) -> str:
    s = re.sub(r"[（(][^）)]*[）)]", "", s or "")
    s = s.strip().translate(_FW)
    # 1) 从最右一个动作词处截断，剩下才是名字本体（截断后不足 6 字则不截，避免把名字截残）
    cut = -1
    for w in _CUT_WORDS:
        i = s.rfind(w)
        if i >= 0 and len(s) - (i + len(w)) >= 6:
            cut = max(cut, i + len(w))
    if cut > 0:
        s = s[cut:]
    # 2) 剥掉开头的金额、虚词（"5亿元""的""为""与"）
    s = re.sub(r"^[\d.]+\s*(?:亿|万)?\s*元?", "", s).strip()
    # 3) 名字前面常粘着上下文（"投资方包括××集团""标的公司为××股份公司"）。
    # 只剥多字虚词与明确的引导字，不剥单字"和/中/新"这类可能是公司名首字的字。
    leads = ("本轮投资方", "投资方包括", "投资方", "标的公司", "标的", "该公司", "公司",
             "其中包括", "包括", "其中", "此外", "同时", "以及", "还有", "另有", "另据",
             "据介绍", "公告显示", "记者", "来源", "分别", "主要")
    changed = True
    while changed:
        changed = False
        for w in leads:
            if s.startswith(w) and len(s) - len(w) >= 6:
                s = s[len(w):]
                changed = True
                break
    # "为/是/由/向/对/被"只在后面还够长时剥，避免把"和而泰"这类名字剥坏
    while len(s) >= 7 and s[0] in "为是由向对被与及":
        s = s[1:]
    return s


def extract_names(text: str) -> list[str]:
    """从一段文字里抽出带公司后缀的名字（工商全称）。"""
    out = []
    for m in _NAME_RE.finditer(text or ""):
        n = _clean_name(m.group(0))
        if len(n) < 4 or n in _NOISE:
            continue
        if any(n == x or n in x for x in _NOISE):
            continue
        out.append(n)
    return out


# 主体位上也会出现机构名（"证监会：…"），它们不是企业
_ORG_WORDS = ("央行", "证监会", "国资委", "发改委", "工信部", "财政部", "统计局", "商务部",
              "国务院", "交易所", "上交所", "深交所", "北交所", "金融监管", "市场监管",
              "税务总局", "海关总署", "能源局", "科技部", "住建部", "生态环境部")


def title_subjects(title: str) -> list[str]:
    """从标题的主体位抽简称。

    财经标题有固定句式：「某某精密：2026年上半年净利…」「某某控股：拟以…」
    「IPO雷达丨某工业品平台赴港上市…」。冒号/竖线前的那个词就是主体，
    而它往往是不带「有限公司」的简称——只按后缀抽取会全部漏掉。
    """
    t = (title or "").strip().translate(_FW)
    out: list[str] = []
    m = _TITLE_HEAD.match(t)
    if m:
        out.append(m.group(1))
    for seg in re.split(r"[｜|丨]", t)[1:]:
        seg = seg.strip()
        m2 = _TITLE_SEG.match(seg)
        if m2:
            out.append(m2.group(1))
    # 去掉机构与纯数字代码；简称常常只有三四个字，长度下限不能设成 4
    return [x for x in out
            if not re.fullmatch(r"\d+", x) and not any(w in x for w in _ORG_WORDS)]


def _quote(blob: str, name: str, width: int = 70) -> str:
    """抽一句"它为什么在这里"的原话——没有这句，候选名单就只是一串公司名。"""
    i = blob.find(name)
    if i < 0:
        return ""
    s = max(blob.rfind(c, 0, i) for c in "。！？；\n")
    s = 0 if s < 0 else s + 1
    seg = blob[s:s + width * 2]
    end = len(seg)
    for c in "。！？；\n":
        j = seg.find(c)
        if 0 < j < end:
            end = j
    return re.sub(r"\s+", " ", seg[:end]).strip()[:width]


def _terms(products: str, region: str) -> list[str]:
    """产品字段（逗号/顿号分隔）+ 地区 → 检索词组合。产品词优先，最多 3 组。"""
    ps = [p.strip() for p in re.split(r"[，,、/;；\s]+", products or "") if len(p.strip()) >= 2]
    ps = ps[:3]
    qs = []
    for p in ps:
        qs.append(p)
        if region:
            qs.append(f"{p} {region}")
    return qs or ([region] if region else [])


def _resolve(name: str) -> dict | None:
    core = re.sub(_SUFFIX + "$", "", name)
    try:
        return cninfo.resolve(core) or cninfo.resolve(name)
    except Exception:
        return None


def find(products: str, region: str = "", days: int = 180, limit: int = 12,
         exclude: list[str] | None = None, on_event=None, max_docs: int = 22) -> dict:
    """产品词 → 候选竞品名单。失败降级为空名单，并在 notes 里说明原因。"""
    emit = on_event or (lambda e: None)
    qs = _terms(products, region)
    if not qs:
        return {"candidates": [], "scanned": 0, "queries": [],
                "notes": ["未填写主营产品：没有品类词就无法从公开资讯里反查同业"]}

    ex = {x.strip() for x in (exclude or []) if x and x.strip()}
    terms = [p for p in re.split(r"[，,、/;；\s]+", products or "") if len(p) >= 2]

    # 1) 检索：拿候选文章（检索接口给的是短摘要，企业名多在正文里）
    docs: list[dict] = []
    seen: set[str] = set()
    scanned = 0
    fallback = 0
    for q in qs:
        for page in (1, 2, 3):
            meta: dict = {}
            try:
                items = eastmoney.search_any(q, page=page, size=20, meta=meta)
            except Exception:
                items = []
            if meta.get("via") == "baidu":
                fallback += 1
            if not items:
                break
            scanned += len(items)
            for it in items:
                u = it.get("url") or it.get("title")
                if not u or u in seen:
                    continue
                seen.add(u)
                docs.append(it)
            time.sleep(0.2)

    # 2) 相关性 + 去噪：必须真的提到本品类词；行情稿与早报/快报这类聚合稿一律不要
    noisy = 0
    pool: list[dict] = []

    def relevant(d: dict) -> bool:
        title = d.get("title") or ""
        # 聚合稿（早报/快报/要闻盘点）一篇里几十家公司，抽出来全是无关主体
        # 行情稿（涨停/直线拉升/股价）属股市情报，不进产业竞品名单
        if NOISE_NEWS.search(title):
            return False
        blob = title + " " + (d.get("body") or "")
        return any(t and t in blob for t in terms)

    for d in docs:
        if relevant(d):
            pool.append(d)
        elif NOISE_NEWS.search(d.get("title") or ""):
            noisy += 1
    # 截断前先按相关度排：标题就带品类词的比只在摘要里带的可信得多。
    # 不排序直接截断，会把排在后面的真对手挤掉（目标常出现在第 2、3 页）。
    pool.sort(key=lambda d: 0 if any(t in (d.get("title") or "") for t in terms) else 1)
    pool = pool[:max_docs]
    emit({"type": "log", "level": "info",
          "msg": f"线索检索：{len(qs)} 组词，扫 {scanned} 篇，"
                 f"剔掉行情/聚合稿 {noisy} 篇，品类相关 {len(pool)} 篇"})

    # 3) 正文抓取：企业名几乎只在正文里出现，只看摘要会误判成「没有」
    from concurrent.futures import ThreadPoolExecutor

    from . import net
    from .textproc import extract_main, page_title

    def body_of(d: dict) -> str:
        u = d.get("url") or ""
        if not u:
            return ""
        try:
            html = net.fetch(u, timeout=12).text
            return extract_main(html, page_title(html))
        except Exception:
            return ""

    with ThreadPoolExecutor(max_workers=4) as pool_ex:
        bodies = list(pool_ex.map(body_of, pool))

    # 4) 逐篇抽主体 → 剔聚合稿 → 按事件去重
    events: list[dict] = []
    fetched, failed, agg = 0, 0, 0
    for d, body in zip(pool, bodies):
        if body:
            fetched += 1
        elif d.get("url"):
            failed += 1
        title = d.get("title") or ""
        blob = title + " " + (d.get("body") or "") + " " + body
        names = {n for n in extract_names(blob) if not any(x and (x in n or n in x) for x in ex)}
        for s in title_subjects(title):
            if any(x and (x in s or s in x) for x in ex):
                continue
            if len(s) >= 3:
                names.add(s)
        # 报道的中心主体会在标题里、或在正文反复出现；
        # 只被顺带提一次的名字（长稿里的关联方）不算线索，否则名单里全是无关公司。
        names = {n for n in names if n in title or blob.count(n) >= 2}
        if len(names) >= _AGG_MAX:          # 一篇稿子里装了一堆公司 = 公告汇总，不是报道
            agg += 1
            continue
        if not names:
            continue
        # 同一件事常有多家媒体改写标题，光看相似度会漏；共同提到两个以上主体的也算同一件事
        same = next((e for e in events
                     if similarity(title, e["title"]) >= _SAME_EVENT
                     or len(names & e["names"]) >= 2), None)
        if same:                            # 同一件事被多家转发：算一起，只累计转发数
            same["dup"] += 1
            same["names"] |= names
            continue
        events.append({"title": title, "dup": 0, "names": names, "blob": blob,
                       "evidence": {"title": title[:90], "url": d.get("url") or "",
                                    "date": (d.get("published_at") or "")[:10],
                                    "source": d.get("source") or ""}})

    # 5) 聚合：按"独立事件数"计，不按文章数
    hits: dict[str, dict] = {}
    for e in events:
        for n in e["names"]:
            rec = hits.setdefault(n, {"name": n, "count": 0, "repost": 0, "evidence": None})
            rec["count"] += 1
            rec["repost"] += e["dup"]
            if rec["evidence"] is None:
                ev = dict(e["evidence"])
                ev["quote"] = _quote(e["blob"], n)
                rec["evidence"] = ev

    ranked = sorted(hits.values(), key=lambda r: (-r["count"], r["name"]))[:limit]
    for r in ranked:
        # 上市与否决定后面能不能进竞品追踪（能进=有公告与财报可看）
        r["listed"] = False
        r["code"] = ""
        ent = _resolve(r["name"])
        if ent and ent.get("code"):
            r["listed"] = True
            r["code"] = ent.get("code")
            r["short_name"] = ent.get("short_name") or ""

    notes = [
        "计数口径是「独立事件数」：同一件事被十家媒体转发只算一起，不代表关注度高。",
        "名单只反映公开曝光，不代表规模或份额；已上市的候选可直接加入竞品追踪，未上市的先用自述档案补基线。",
        "不上财经媒体、只做配套的中小对手不会出现在这里——这是取不到，不是没有。",
    ]
    if region:
        notes.append(f"已按「{region}」加了一组地域联合检索，用来捞区域性对手；不代表地理上就只有这些。")
    if noisy:
        notes.append(f"检索命中的 {noisy} 篇行情稿/早报快报已剔除（品类词被按词切分，会撞上「直线拉升」这类说法）。")
    if fallback:
        notes.append(f"东方财富检索失败，已有 {fallback} 组词回退到百度资讯首屏；该源只给摘要，线索可能不全。")
    if agg:
        notes.append(f"{agg} 篇是公告汇总稿（单篇含 ≥{_AGG_MAX} 家公司），已整篇丢弃。")
    if failed:
        notes.append(f"{failed} 篇正文抓取失败，名单可能不完整——这是取不到，不是没有。")
    if not ranked:
        if not pool:
            notes.append("没有一篇资讯真正提到这个词：品类词可能太泛（如「零部件」），也可能这个品类在财经媒体里没有报道。")
        else:
            notes.append(f"读了 {fetched} 篇正文仍未抽到可信主体：这些报道多为行业综述，没有点名具体供应商。")
    return {"candidates": ranked, "scanned": scanned, "fetched": fetched,
            "failed": failed, "aggregated": agg, "events": len(events),
            "queries": qs, "days_hint": days, "notes": notes}


# 搜索联想会跟着输入频繁触发：同一关键词 5 分钟内不重复打资讯接口
_CLUE_TTL = 300
_clue_cache: dict[str, tuple[float, list[dict]]] = {}


def _related(name: str, kw: str) -> bool:
    """名字是否指向输入词对应的主体：简称/全称包含，或 3-gram 足够接近。"""
    if name == kw or kw in name or name in kw:
        return True
    return len(kw) >= 3 and similarity(name, kw) >= 0.62


def clues(keyword: str, limit: int = 6) -> list[dict]:
    """从公开资讯里反查企业主体，补上市公司库覆盖不到的非上市对手。

    非上市公司没有证券代码，巨潮的证券主体库天然搜不到它；只要它出现在
    财经报道里，就能从标题主体位或工商全称里被抽出来。返回的是**线索**：
    每个名字都对应一篇可点开的资讯，只说明被报道过，不代表存在性核验。
    """
    kw = (keyword or "").strip()
    if len(kw) < 2:
        return []
    hit = _clue_cache.get(kw)
    if hit and time.time() - hit[0] < _CLUE_TTL:
        return [dict(x) for x in hit[1][:limit]]
    try:
        docs = eastmoney.search_any(kw, page=1, size=20)
    except Exception:
        docs = []
    counts: dict[str, int] = {}
    proof: dict[str, dict] = {}
    for d in docs:
        title = d.get("title") or ""
        blob = title + " " + (d.get("body") or "")
        names = {n for n in extract_names(blob) if _related(n, kw)}
        names |= {s for s in title_subjects(title) if len(s) >= 3 and _related(s, kw)}
        for n in names:
            counts[n] = counts.get(n, 0) + 1
            if n not in proof:
                proof[n] = {"title": title[:90], "url": d.get("url") or "",
                            "date": (d.get("published_at") or "")[:10]}
    out = [{"name": n, "hits": counts[n],
            "hint": f"近 {counts[n]} 篇资讯提到", **proof.get(n, {})}
           for n in sorted(counts, key=lambda x: (-counts[x], x))[:limit]]
    _clue_cache[kw] = (time.time(), out)
    return [dict(x) for x in out]
