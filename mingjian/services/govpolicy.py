"""从政府官网按产业自动获取政策与补贴公告。

取数路径（全部为官方站点，无第三方聚合）：
  中央 —— 工业和信息化部站内搜索接口（search-front-server），按"政策文件"类目检索；
  地方 —— 各省工业和信息化厅（经信厅）门户及其政策/通知栏目，按关键词筛选。

约束：
  1. 只取官方域名，结果里必须带来源站点与原文链接；
  2. 抓不到就记为 skipped 并说明原因，不构造标题、不补日期；
  3. 正文交给 policy.parse_text 统一抽字段，规则只有一份；
  4. 检索词由公司所属行业与主营描述推导，不手填。
"""
from __future__ import annotations

import json
import pathlib
import re
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from ..core import net
from ..core.log import get as _get_logger
from . import policy

LOG = _get_logger("govpolicy")

HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# ---------------------------------------------------------------- 行业 → 检索词

# 顺序即优先级；命中越靠前的行业特征，用越靠前的关键词组
INDUSTRY_KW: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
    (("电池", "储能", "锂电", "电源"),
     ("储能", "动力电池", "锂离子电池", "新型储能")),
    (("机器人", "智能装备", "数控", "机床", "装备"),
     ("智能工厂", "首台（套）", "工业母机", "智能制造", "机器人")),
    (("半导体", "集成电路", "电子", "光电", "芯片"),
     ("集成电路", "智能终端", "电子信息", "人工智能")),
    # 工厂自动化零部件（FA）必须单列：它的行业描述里带"零部件"三个字，
    # 若落到下面的汽车组，就会被当成"汽车零部件"去搜"新能源汽车"，捞回一堆整车补贴。
    (("工厂自动化", "自动化零部件", "精密传动", "工装夹具", "模具", "齿轮",
      "导轨", "丝杠", "轴承", "联轴器", "减速机"),
     ("智能制造", "产业基础再造", "工业母机", "专精特新", "首台（套）")),
    (("汽车", "整车", "汽车零部件"),
     ("新能源汽车", "智能网联汽车", "汽车零部件")),
    (("医药", "生物", "医疗", "器械"),
     ("生物医药", "医疗器械", "创新药")),
    (("化工", "材料", "新材料", "钢铁", "有色"),
     ("新材料", "化工", "绿色制造", "技术改造")),
    (("电气", "电机", "输配电", "线缆"),
     ("电力装备", "智能电网", "电气机械")),
    (("软件", "信息", "数据", "通信"),
     ("人工智能", "工业软件", "数字经济", "5G")),
    (("纺织", "服装", "轻工", "家电", "食品"),
     ("传统产业改造", "智能制造", "绿色工厂")),
]

# 任何产业都适用的政策方向（补贴、认定、技改资金多在这几类里）
GENERAL_KW = ("专精特新", "制造业单项冠军", "技术改造", "设备更新", "数字化转型")

PROVINCE_SITES: dict[str, tuple[str, str]] = {
    "北京": ("https://jxj.beijing.gov.cn/", "北京市经济和信息化局"),
    "天津": ("https://gyxxh.tj.gov.cn/", "天津市工业和信息化局"),
    "河北": ("https://gxt.hebei.gov.cn/", "河北省工业和信息化厅"),
    "山西": ("https://gxt.shanxi.gov.cn/", "山西省工业和信息化厅"),
    "内蒙古": ("https://gxt.nmg.gov.cn/", "内蒙古自治区工业和信息化厅"),
    "辽宁": ("https://gxt.ln.gov.cn/", "辽宁省工业和信息化厅"),
    "吉林": ("https://gxt.jl.gov.cn/", "吉林省工业和信息化厅"),
    "黑龙江": ("https://gxt.hlj.gov.cn/", "黑龙江省工业和信息化厅"),
    "上海": ("https://sheitc.sh.gov.cn/", "上海市经济和信息化委员会"),
    "江苏": ("https://gxt.jiangsu.gov.cn/", "江苏省工业和信息化厅"),
    "浙江": ("https://jxt.zj.gov.cn/", "浙江省经济和信息化厅"),
    "安徽": ("https://jxt.ah.gov.cn/", "安徽省经济和信息化厅"),
    "福建": ("https://gxt.fujian.gov.cn/", "福建省工业和信息化厅"),
    "江西": ("https://gxt.jiangxi.gov.cn/", "江西省工业和信息化厅"),
    "山东": ("http://gxt.shandong.gov.cn/", "山东省工业和信息化厅"),
    "河南": ("https://gxt.henan.gov.cn/", "河南省工业和信息化厅"),
    "湖北": ("https://jxt.hubei.gov.cn/", "湖北省经济和信息化厅"),
    "湖南": ("https://gxt.hunan.gov.cn/", "湖南省工业和信息化厅"),
    "广东": ("https://gdii.gd.gov.cn/", "广东省工业和信息化厅"),
    "广西": ("https://gxt.gxzf.gov.cn/", "广西壮族自治区工业和信息化厅"),
    "海南": ("https://gxt.hainan.gov.cn/", "海南省工业和信息化厅"),
    "重庆": ("https://jjxxw.cq.gov.cn/", "重庆市经济和信息化委员会"),
    "四川": ("https://jxt.sc.gov.cn/", "四川省经济和信息化厅"),
    "贵州": ("https://gxt.guizhou.gov.cn/", "贵州省工业和信息化厅"),
    "云南": ("https://gxt.yn.gov.cn/", "云南省工业和信息化厅"),
    "陕西": ("https://gxt.shaanxi.gov.cn/", "陕西省工业和信息化厅"),
    "甘肃": ("https://gxt.gansu.gov.cn/", "甘肃省工业和信息化厅"),
    "青海": ("https://gxt.qinghai.gov.cn/", "青海省工业和信息化厅"),
    "宁夏": ("https://gxt.nx.gov.cn/", "宁夏回族自治区工业和信息化厅"),
    "新疆": ("https://gxt.xinjiang.gov.cn/", "新疆维吾尔自治区工业和信息化厅"),
    "西藏": ("https://jxt.xizang.gov.cn/", "西藏自治区经济和信息化厅"),
}

# 计划单列市与工业重镇 → 省份（注册地址里常只写城市）
CITY_PROV: dict[str, str] = {
    "深圳": "广东", "广州": "广东", "东莞": "广东", "佛山": "广东", "珠海": "广东", "惠州": "广东",
    "中山": "广东", "江门": "广东", "肇庆": "广东",
    "苏州": "江苏", "无锡": "江苏", "常州": "江苏", "南通": "江苏", "徐州": "江苏", "盐城": "江苏",
    "泰州": "江苏", "扬州": "江苏", "镇江": "江苏",
    "宁波": "浙江", "温州": "浙江", "嘉兴": "浙江", "绍兴": "浙江", "台州": "浙江", "金华": "浙江",
    "杭州": "浙江", "湖州": "浙江",
    "青岛": "山东", "烟台": "山东", "潍坊": "山东", "淄博": "山东", "威海": "山东", "临沂": "山东",
    "济南": "山东",
    "厦门": "福建", "泉州": "福建", "福州": "福建", "宁德": "福建", "漳州": "福建",
    "大连": "辽宁", "沈阳": "辽宁", "长春": "吉林", "哈尔滨": "黑龙江",
    "成都": "四川", "绵阳": "四川", "德阳": "四川", "宜宾": "四川",
    "武汉": "湖北", "襄阳": "湖北", "宜昌": "湖北",
    "长沙": "湖南", "株洲": "湖南", "湘潭": "湖南",
    "合肥": "安徽", "芜湖": "安徽", "滁州": "安徽",
    "郑州": "河南", "洛阳": "河南", "新乡": "河南",
    "西安": "陕西", "宝鸡": "陕西", "咸阳": "陕西",
    "南昌": "江西", "赣州": "江西", "九江": "江西",
    "太原": "山西", "石家庄": "河北", "唐山": "河北", "保定": "河北", "廊坊": "河北",
    "昆明": "云南", "贵阳": "贵州", "南宁": "广西", "柳州": "广西", "桂林": "广西",
    "兰州": "甘肃", "银川": "宁夏", "西宁": "青海", "乌鲁木齐": "新疆",
    "呼和浩特": "内蒙古", "包头": "内蒙古", "海口": "海南", "拉萨": "西藏",
}

# 标题里出现这些词，才算政策/补贴类文件（避免把新闻稿当政策）
POLICY_HINT = ("通知", "公告", "办法", "意见", "方案", "细则", "指南", "申报", "补贴",
               "补助", "奖励", "专项", "政策", "规划", "措施", "决定", "规定", "公示",
               "认定", "征集", "遴选", "资金", "名单")
NEWS_NOISE = ("政策解读", "新闻发布会", "宣传周", "培训", "会议", "论坛", "领导", "调研",
              "解读", "图解", "访谈", "视频", "心得体会")
# 单个企业的审批/审查结果，不属于产业政策
COMPANY_NOISE = ("节能报告的审查", "环境影响报告", "安全设施设计", "竣工验收", "项目核准批复")

# 补贴/资金类措辞，命中说明与"钱"直接相关，优先级更高
MONEY_HINT = ("补贴", "补助", "奖励", "资金", "专项资金", "贴息", "奖补", "资助")

MIIT_SEARCH = "https://www.miit.gov.cn/search-front-server/api/search/info"
MIIT_PARAMS = ("websiteid=110000000000000&category=51&depth=0&scope=basic"
               "&cateid=&pos=&level=6&selectFields=title,url,deploytime,publishgroupname")
# 单靠行业词检索，返回多是新闻；叠加政策类词后命中率显著提高
MIIT_SUFFIX = ("通知", "申报", "补贴", "资金")


# ---------------------------------------------------------------- 工具

def _get(url: str, timeout: int = 20) -> bytes:
    """取页面。https 不通时回落到 http（部分省站只挂了一边，直接放弃太可惜）。"""
    try:
        return net.fetch(url, headers=HDR, timeout=timeout).content
    except Exception:
        if url.startswith("https://"):
            alt = "http://" + url[8:]
        elif url.startswith("http://"):
            alt = "https://" + url[7:]
        else:
            raise
        return net.fetch(alt, headers=HDR, timeout=timeout).content


def _kw_set(industry: str, main_business: str) -> list[str]:
    """按行业描述推出检索词。先看行业分类，命中不了再看主营业务——
    主营描述里常顺带提到别的品类（如机器人企业写"配套电池产线"），不能反过来带偏。
    """
    out: list[str] = []
    for blob in (industry or "", main_business or ""):
        for triggers, kws in INDUSTRY_KW:
            if any(t in blob for t in triggers):
                out.extend(kws)
                break
        if out:
            break
    out.extend(GENERAL_KW)
    seen, uniq = set(), []
    for k in out:
        if k not in seen:
            seen.add(k)
            uniq.append(k)
    return uniq


def province_of(address: str, issuer: str = "") -> str:
    """从注册地址/发文机关里认出省份，兼顾只写城市的情况。

    按「出现位置最靠前」取，而不是遍历顺序——地址常写成
    "常州（武进高新区）；另设哈尔滨、天津制造基地"，
    取遍历顺序会随机落到制造基地所在的省，政策就全取错了。
    """
    blob = f"{address or ''} {issuer or ''}"
    best: tuple[int, str] | None = None
    for p in PROVINCE_SITES:
        i = blob.find(p)
        if i >= 0 and (best is None or i < best[0]):
            best = (i, p)
    for c, prov in CITY_PROV.items():
        i = blob.find(c)
        if i >= 0 and (best is None or i < best[0]):
            best = (i, prov)
    return best[1] if best else ""


def _date_from_url(u: str) -> str:
    m = re.search(r"/(20\d{2})[/\-_](\d{1,2})[/\-_](\d{1,2})/", u)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"/(20\d{2})(\d{2})(\d{2})/", u)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return ""


def _looks_policy(title: str) -> bool:
    if not title or len(title) < 8:
        return False
    if any(n in title for n in NEWS_NOISE) or any(n in title for n in COMPANY_NOISE):
        return False
    return any(h in title for h in POLICY_HINT)


def _score(title: str, keywords: list[str]) -> int:
    """标题相关度：命中行业词 +2，命中补贴/资金类 +2，命中政策类 +1。"""
    s = 0
    if any(k in title for k in keywords):
        s += 2
    if any(m in title for m in MONEY_HINT):
        s += 2
    if any(h in title for h in ("申报", "征集", "认定", "公示", "名单")):
        s += 1
    return s


# ---------------------------------------------------------------- 中央：工信部

def _miit_page(query: str, pg: int = 15) -> list[dict]:
    """工信部检索单页。异常直接抛出，由调用方汇总成可见的失败原因——
    静默返回 [] 会让“接口挂了”和“没搜到”变得无法区分。"""
    q = urllib.parse.quote(query)
    url = f"{MIIT_SEARCH}?{MIIT_PARAMS}&q={q}&pg={pg}&p=1&sortFields=deploytime"
    d = json.loads(_get(url).decode("utf-8", "ignore"))
    rows = (((d.get("data") or {}).get("searchResult") or {}).get("dataResults")) or []

    out = []
    for r in rows:
        x = r.get("data") if isinstance(r.get("data"), dict) else r
        title = re.sub(r"\s+", "", (x.get("title") or ""))
        href = (x.get("url") or "").strip()
        if not title or not href or not _looks_policy(title):
            continue
        if href.startswith("/"):
            href = "https://www.miit.gov.cn" + href
        elif href.startswith("http://www.miit.gov.cn"):
            href = "https://www.miit.gov.cn" + href[len("http://www.miit.gov.cn"):]
        ts = x.get("deploytime")
        date = ""
        if ts and str(ts).isdigit():
            date = time.strftime("%Y-%m-%d", time.localtime(int(ts) / 1000))
        out.append({"title": title, "url": href, "date": date or _date_from_url(href),
                    "source": "工业和信息化部", "level": "中央"})
    return out


def search_miit(keyword: str, keywords: list[str] | None = None,
                limit: int = 6) -> tuple[list[dict], dict]:
    """工信部站内搜索：行业词叠加政策词检索，去重后按相关度+时间排序。

    返回 (条目, 诊断)。诊断里的 ok 表示“接口这次到底通没通”，
    调用方据此区分“确实没有政策”与“接口没取到”。
    """
    kws = keywords or [keyword]
    queries = [keyword] + [f"{keyword} {s}" for s in MIIT_SUFFIX]
    pool, seen = [], {}
    pages_ok, errs = 0, []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = [ex.submit(_miit_page, q) for q in queries]
        for f in futs:
            try:
                got = f.result(timeout=25)
                pages_ok += 1
            except Exception as e:
                errs.append(type(e).__name__)
                continue
            for it in got:
                key = re.sub(r"[（(].*?[)）]", "", it["title"])[:26]
                if key in seen:
                    continue
                seen[key] = 1
                it["score"] = _score(it["title"], kws) + (1 if "/zwgk/zcwj" in it["url"] else 0)
                pool.append(it)

    if not errs:
        diag = {"ok": True, "scanned": pages_ok, "reason": ""}
    elif pages_ok:
        diag = {"ok": True, "scanned": pages_ok,
                "reason": f"{len(errs)}/{len(queries)} 个检索词未返回（{'、'.join(sorted(set(errs)))}）"}
    else:
        diag = {"ok": False, "scanned": 0,
                "reason": f"接口全部失败（{'、'.join(sorted(set(errs))) or '无返回'}）"}

    # 先按时间倒序，再按相关度稳定排序：同等相关度时新的排前面
    pool.sort(key=lambda x: x["date"] or "", reverse=True)
    pool.sort(key=lambda x: -x["score"])
    return pool[:limit], diag


# ---------------------------------------------------------------- 地方：省工信厅

def list_province(prov: str, keywords: list[str],
                  limit: int = 6) -> tuple[list[dict], dict]:
    """抓省厅门户的政策类链接，并在落盘里记一次健康状态。

    返回 (条目, 诊断)。站点打不开时 diag.ok=False 并写明原因——
    不能再把“站点不可达”当成“该省没有相关政策”返回空列表。
    """
    items, diag = _list_province_raw(prov, keywords, limit)
    _health_update(prov, bool(diag.get("ok")), diag.get("reason", ""))
    return items, diag


def _list_province_raw(prov: str, keywords: list[str],
                       limit: int = 6) -> tuple[list[dict], dict]:
    site = PROVINCE_SITES.get(prov)
    if not site:
        return [], {"ok": False, "scanned": 0, "reason": f"未收录「{prov}」工信厅站点，需人工投料"}
    root, org = site
    try:
        html = _get(root).decode("utf-8", "ignore")
    except Exception as e:
        return [], {"ok": False, "scanned": 0, "reason": f"站点不可达（{type(e).__name__}）"}

    anchors = re.findall(r'<a[^>]+href="([^"#]+)"[^>]*>\s*([^<]{8,90}?)\s*</a>', html)
    pool, seen = [], set()
    for href, title in anchors:
        title = re.sub(r"\s+", "", title)
        if not _looks_policy(title):
            continue
        href = urllib.parse.urljoin(root, href)
        # 解读/问答栏目不是政策原文
        if any(x in href for x in ("/jdhy/", "/zcjd/", "/jiedu/")) or "政策在身边" in title:
            continue
        if href in seen or "gov.cn" not in href:
            continue
        seen.add(href)
        pool.append({"title": title, "url": href, "date": _date_from_url(href),
                     "source": org, "level": "地方", "province": prov,
                     "score": _score(title, keywords)})

    if not pool:
        return [], {"ok": True, "scanned": len(anchors),
                    "reason": "站点可访问，但首页没有可识别的政策类链接（可能已改版，政策在子栏目）"}

    # 只保留与产业相关（命中行业词或补贴词）的条目，避免把厅局日常通知全拉进来
    pool = [x for x in pool if x["score"] >= 2]
    if not pool:
        return [], {"ok": True, "scanned": len(anchors),
                    "reason": "首页政策类链接均与本产业无关"}
    pool.sort(key=lambda x: x["date"] or "", reverse=True)
    pool.sort(key=lambda x: -x["score"])
    return pool[:limit], {"ok": True, "scanned": len(anchors), "reason": ""}


# ---------------------------------------------------------------- 抓正文并解析

def _fetch_text(item: dict) -> tuple[str, str]:
    """返回 (正文, 说明)。"""
    try:
        raw = _get(item["url"], timeout=25)
    except Exception as e:
        return "", f"打不开原文（{type(e).__name__}）"
    pages, note = policy.read_bytes("page.html", raw)
    text = "\n".join(pages)
    if len(re.sub(r"\s", "", text)) < 200:
        return "", "正文过短，可能是栏目页或需登录"
    return text, note


def _to_doc(item: dict, idx: int) -> dict:
    text, note = _fetch_text(item)
    if not text:
        item["error"] = note
        return {}
    doc = policy.parse_text(item["title"], text, note=note, doc_id="G%02d" % idx)
    doc["url"] = item["url"]
    doc["source"] = item["source"]
    doc["level"] = item["level"]
    doc["type"] = "HTML"
    if not doc["f"].get("date") and item.get("date"):
        doc["f"]["date"] = item["date"]
        doc["s_date"] = 0.6
    if not doc["f"].get("region") and item.get("province"):
        doc["f"]["region"] = item["province"]
    return doc


def collect(company: str, *, industry: str = "", main_business: str = "",
            address: str = "", province: str = "",
            max_central: int = 5, max_local: int = 5,
            keywords: list[str] | None = None,
            on_event=None) -> dict:
    """抓取与该公司产业相关的中央+地方政策公告。"""
    def log(msg, kind="info"):
        if on_event:
            on_event({"type": "log", "msg": msg, "kind": kind})

    kws = keywords or _kw_set(industry, main_business)
    prov = province or province_of(address)
    log(f"检索词：{'、'.join(kws[:6])}｜地方：{prov or '未识别到省份'}")

    central_kw = kws[:3]

    items: list[dict] = []
    warnings: list[str] = []
    sources: list[dict] = []

    with ThreadPoolExecutor(max_workers=4) as ex:
        cent_futs = {ex.submit(search_miit, kw, kws, 5): kw for kw in central_kw}
        loc_fut = ex.submit(list_province, prov, kws, max_local * 2) if prov else None

        for f, kw in cent_futs.items():
            try:
                got, dg = f.result(timeout=40)
            except Exception as e:
                got, dg = [], {"ok": False, "scanned": 0,
                               "reason": f"检索超时或异常（{type(e).__name__}）"}
            items.extend(got)
            sources.append({"level": "中央", "name": "工业和信息化部", "keyword": kw,
                            "ok": dg["ok"], "got": len(got), "reason": dg.get("reason", "")})
            if not dg["ok"]:
                warnings.append(f"工信部检索「{kw}」未取到：{dg.get('reason', '')}")
                log(f"工信部检索「{kw}」失败：{dg.get('reason', '')}", "warn")

        if loc_fut:
            try:
                got, dg = loc_fut.result(timeout=40)
            except Exception as e:
                got, dg = [], {"ok": False, "scanned": 0,
                               "reason": f"取数超时或异常（{type(e).__name__}）"}
            items.extend(got)
            sources.append({"level": "地方", "name": PROVINCE_SITES.get(prov, ("", prov))[1],
                            "keyword": "", "ok": dg["ok"], "got": len(got),
                            "reason": dg.get("reason", "")})
            if not dg["ok"]:
                warnings.append(f"{prov}工信厅未取到：{dg.get('reason', '')}")
                log(f"{prov}工信厅取数失败：{dg.get('reason', '')}", "warn")
            elif not got:
                # 站点通但没匹配到——这也是结论，必须让人看见，不能静默当"没有政策"
                warnings.append(f"{prov}工信厅站点可访问，但{dg.get('reason') or '未匹配到本产业政策'}")

    # 去重 + 截断
    seen, picked = set(), []
    for it in items:
        u = it.get("url")
        if not u or u in seen:
            continue
        seen.add(u)
        picked.append(it)

    cent = [x for x in picked if x["level"] == "中央"][:max_central]
    loc = [x for x in picked if x["level"] == "地方"][:max_local]
    picked = cent + loc
    log(f"候选 {len(picked)} 条（中央 {len(cent)} · 地方 {len(loc)}），开始抓正文并抽字段")
    if not picked:
        log("本次没有取到候选文件，详见下方通道状态", "warn")

    docs, skipped = [], []
    for i, it in enumerate(picked, 1):
        try:
            doc = _to_doc(it, i)
        except Exception as e:
            doc = {}
            it["error"] = f"{type(e).__name__}: {e}"
        if doc:
            docs.append(doc)
            log(f"[{doc['id']}] {it['source']}｜{it['title'][:34]}｜{doc['status']} {doc['conf']}", "ok")
        else:
            skipped.append(f"{it['title'][:30]}：{it.get('error', '未取到正文')}")
            log(f"跳过 {it['title'][:30]}：{it.get('error', '未取到正文')}", "warn")
        time.sleep(0.15)

    if warnings:
        LOG.warning("政策取数 %s 有 %d 项通道告警：%s", company, len(warnings),
                    "；".join(warnings[:3]))
    LOG.info("政策取数 %s 完成：中央 %d 条 · 地方 %d 条 · 成文 %d 份 · 跳过 %d 份",
             company, len(cent), len(loc), len(docs), len(skipped))

    return {"docs": docs, "skipped": skipped, "stats": policy.stats(docs),
            "keywords": kws, "province": prov, "company": company,
            "warnings": warnings, "sources": sources}


# ---------------------------------------------------------------- 省站健康状态

HEALTH = pathlib.Path(__file__).resolve().parents[2] / "data" / "prov_health.json"
# 多个省份是并发探测的，读-改-写必须串行，否则后写的会覆盖先写的
_HEALTH_LOCK = threading.Lock()


def _health_update(prov: str, ok: bool, reason: str = "") -> None:
    """记一次站点探测结果。

    只留最近状态，不做长期统计——够判断“这台站现在能不能用”。
    注意 ok 表示“站点是否可访问”，与“是否匹配到政策”是两件事：
    站点正常但没匹配到，仍然是可用站点。
    """
    if not prov:
        return
    with _HEALTH_LOCK:
        try:
            HEALTH.parent.mkdir(parents=True, exist_ok=True)
            data = {}
            if HEALTH.exists():
                try:
                    data = json.loads(HEALTH.read_text(encoding="utf-8"))
                except Exception:
                    data = {}
            rec = data.get(prov) or {"fails": 0}
            now = time.strftime("%Y-%m-%d %H:%M")
            rec["last_try"] = now
            rec["ok"] = bool(ok)
            rec["reason"] = reason
            if ok:
                rec["last_ok"] = now
                rec["fails"] = 0
            else:
                rec["last_fail"] = now
                rec["fails"] = int(rec.get("fails") or 0) + 1
            data[prov] = rec
            HEALTH.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass


def health_snapshot() -> dict:
    """各省站点状态。available=None 表示还没探测过，不要当成不可用。"""
    try:
        data = json.loads(HEALTH.read_text(encoding="utf-8")) if HEALTH.exists() else {}
    except Exception:
        data = {}
    out = {}
    for prov in PROVINCE_SITES:
        rec = data.get(prov) or {}
        out[prov] = {
            "name": PROVINCE_SITES[prov][1],
            "last_try": rec.get("last_try", ""),
            "last_ok": rec.get("last_ok", ""),
            "fails": rec.get("fails", 0),
            "reason": rec.get("reason", ""),
            "available": rec.get("ok"),
        }
    return out


def unavailable_provinces() -> list[str]:
    """已知站点不可用的省份，连续失败次数多的排前面。"""
    snap = health_snapshot()
    bad = [(p, v) for p, v in snap.items() if v.get("available") is False]
    bad.sort(key=lambda x: -x[1].get("fails", 0))
    return [p for p, _ in bad]


# ---------------------------------------------------------------- 落盘缓存

CACHE = pathlib.Path(__file__).resolve().parents[2] / "data" / "govpolicy.json"


def save_cache(result: dict, hours: int = 24) -> None:
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        if CACHE.exists():
            try:
                data = json.loads(CACHE.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        data[result.get("company") or "-"] = {"at": time.time(), "result": result}
        CACHE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def load_cache(company: str, hours: int = 24) -> dict | None:
    if not CACHE.exists():
        return None
    try:
        d = json.loads(CACHE.read_text(encoding="utf-8")).get(company)
    except Exception:
        return None
    if not d:
        return None
    if time.time() - d.get("at", 0) > hours * 3600:
        return None
    return d.get("result")
