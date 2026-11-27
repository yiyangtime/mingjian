"""
政策与补贴公告的批量解析：文件 → 7 个关键字段 + 原文定位 + 置信度。

这里的规则与「企业洞察」那条链路是两套东西，不要混：
  · 情报链路按公司检索公开信息，产出事件事实；
  · 政策链路按批处理文件，产出结构化申报信息（发文机关、文号、支持标准、申报截止等）。

硬约束（对应甲方需求 C-02 / C-03）：
  · 抽不到就留空，不猜、不用正文里的相邻数字顶替；
  · 区间值、授权性表述（"由各市确定"）不硬收敛成单值，降置信度转人工复核；
  · 一个字都抽不到判 failed，不产出半真半假的记录。
"""
from __future__ import annotations

import os
import pathlib
import re
import zipfile

from ..core import textproc

FIELDS = [
    ("issuer", "发文机关"), ("docNo", "文号"), ("date", "发布日期"),
    ("region", "适用地区"), ("industry", "适用行业"),
    ("support", "支持标准"), ("deadline", "申报截止"),
]

PROV = ["北京", "天津", "上海", "重庆", "河北", "山西", "辽宁", "吉林", "黑龙江", "江苏",
        "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南", "广东", "海南",
        "四川", "贵州", "云南", "陕西", "甘肃", "青海", "内蒙古", "广西", "西藏", "宁夏", "新疆"]
AUTON = {"内蒙古": "内蒙古自治区", "广西": "广西壮族自治区", "西藏": "西藏自治区",
         "宁夏": "宁夏回族自治区", "新疆": "新疆维吾尔自治区"}
MUNI = ["北京", "天津", "上海", "重庆"]
IND_KEYS = ["智能制造装备", "智能装备", "工业机器人", "高档数控机床", "数控机床", "工业母机",
            "智能传感器", "工业互联网", "新能源汽车装备", "动力电池", "工程机械", "农机装备",
            "智能农机", "钢铁装备", "航空航天", "航空零部件", "机械装备", "智能工厂",
            "数字化车间", "传感器", "工业软件", "增材制造", "机器人"]
ORG_SUFFIX = ("工业和信息化厅|工业和信息化局|经济和信息化厅|经济和信息化局|经济和信息化委员会|"
              "工业和信息化委员会|发展和改革委员会|财政局|财政厅|科学技术厅|科学技术局|"
              "商务厅|商务局|市场监督管理局|人民政府|工业和信息化部")
ORG_RE = re.compile(r"[\u4e00-\u9fa5]{2,16}?(?:" + ORG_SUFFIX + ")")
NOISE = ["各地级以上市", "各地级市", "各设区市", "各省辖市", "各市（州）", "各市、州、直管市",
         "各市、县（市、区）", "各县（市、区）", "各区县", "各市州", "各市人民政府",
         "各", "市", "省", "县", "区", "州", "盟", "旗", "地"]
DATE_RE = re.compile(r"(20\d{2})\s*[年\.\-\/]\s*(\d{1,2})\s*[月\.\-\/]\s*(\d{1,2})\s*日?")

SUPPORTED = (".pdf", ".docx", ".txt", ".md", ".html", ".htm")


# ---------------------------------------------------------------- 文本读取

def norm(t: str) -> str:
    t = (t or "").replace("\r", "").replace("\u3000", " ")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _decode(raw: bytes) -> str:
    for enc in ("utf-8", "utf-8-sig", "gb18030", "utf-16"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


def _strip_html(t: str) -> str:
    t = re.sub(r"<script[\s\S]*?</script>", " ", t, flags=re.I)
    t = re.sub(r"<style[\s\S]*?</style>", " ", t, flags=re.I)
    t = re.sub(r"<br\s*/?>", "\n", t, flags=re.I)
    t = re.sub(r"</(p|div|tr|h\d|li)>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    # 先删标签再解实体：页面面包屑里的 &gt; 等要还原成字符，否则会原样显示在原文定位里
    return textproc.unescape(t)


# 政府站正文容器（TRS 模板多见 id="zoom"，也有 article-content 一类命名）
_BODY_RE = re.compile(
    r'<(div|section|article)\b[^>]*(?:id|class)\s*=\s*["\'][^"\']*'
    r'(?:zoom|TRS_Editor|article[-_]?content|content[-_]?body|view[-_]?content'
    r'|detail[-_]?content|news[-_]?content)'
    r'[^"\']*["\'][^>]*>',
    re.I)


def _cut_element(html: str, start: int, tag: str) -> str:
    """从 start（容器开始标签之后）截到同标签闭合，简单按开合计数。"""
    depth, pat = 1, re.compile(rf"<(/?){tag}\b[^>]*>", re.I)
    for m in pat.finditer(html, start):
        if m.group(0).endswith("/>"):
            continue
        if m.group(1):
            depth -= 1
            if depth <= 0:
                return html[start:m.start()]
        else:
            depth += 1
    return html[start:]


def _body_text(html: str) -> str:
    """优先取正文容器文本，只用于「原文定位」展示，不参与字段抽取。

    文号、成文日期常在正文容器外（标题下方），抽字段必须用整页文本；
    展示则相反，带导航与「字体/收藏」控件会淹掉正文。取不到或过短就返回空串，回退整页。
    """
    for m in _BODY_RE.finditer(html):
        t = norm(_strip_html(_cut_element(html, m.end(), m.group(1))))
        if len(re.sub(r"\s", "", t)) >= 120:
            return t
    return ""


def _read_docx(data: bytes) -> str:
    """标准库解析 docx：word/document.xml 的 <w:t> 拼回正文，段落按 <w:p> 换行。"""
    import io
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
    except Exception:
        return ""
    out = []
    for p in re.split(r"</w:p>", xml):
        texts = re.findall(r"<w:t[^>]*>([\s\S]*?)</w:t>", p)
        if texts:
            line = "".join(texts)
            for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"')):
                line = line.replace(a, b)
            if line.strip():
                out.append(line)
    return "\n".join(out)


def _read_pdf(data: bytes) -> tuple[list[str], str]:
    """PDF 走自解析（ToUnicode CMap）。图片型 PDF 会返回空文本并如实标注。"""
    from ..core import pdftext
    text = pdftext.extract_text(data)
    if text.strip():
        return [text], "自解析（ToUnicode）"
    return [""], "未取到文本层（疑似扫描件）"


def read_bytes(name: str, data: bytes) -> tuple[list[str], str]:
    pages, note, _ = read_bytes_full(name, data)
    return pages, note


def read_bytes_full(name: str, data: bytes) -> tuple[list[str], str, str]:
    """返回 (分页文本, 说明, 展示文本)。展示文本仅 HTML 有，供原文抽屉使用。"""
    ext = os.path.splitext(name)[1].lower()
    if ext == ".pdf":
        pages, note = _read_pdf(data)
        return pages, note, ""
    if ext == ".docx":
        return [_read_docx(data)], "zipfile", ""
    if ext in (".html", ".htm"):
        html = _decode(data)
        return [_strip_html(html)], "html", _body_text(html)
    return [_decode(data)], "text", ""


# ---------------------------------------------------------------- 规则引擎

def sents(text: str):
    out, buf, start = [], "", 0
    for i, c in enumerate(text):
        buf += c
        if c in "。；;\n":
            if buf.strip():
                out.append((buf.strip(), start))
            buf, start = "", i + 1
    if buf.strip():
        out.append((buf.strip(), start))
    return out


def clean_org(name: str) -> str:
    n = name.strip()
    # 正文里的"寄送至XX厅""报送至XX局"不是发文机关，先剥掉引导词
    n = re.sub(r"^.{0,6}?(?:寄送至|报送至|提交至|送至|发至|抄送|递交至|递送至|邮寄至"
               r"|寄送|报送|提交|发送|邮寄|联系|咨询)", "", n)
    for _ in range(6):
        hit = next((x for x in NOISE if n.startswith(x) and len(n) - len(x) >= 4), None)
        if not hit:
            break
        n = n[len(hit):]
    return n


def pick_issuer(text: str):
    cand = {}
    for m in ORG_RE.finditer(text):
        name = clean_org(m.group(0))
        if len(name) < 4:
            continue
        c = cand.setdefault(name, {"name": name, "n": 0, "at": m.start()})
        c["n"] += 1
        c["at"] = m.start()
    if not cand:
        return None
    lst = list(cand.values())
    for c in lst:
        s = c["n"]
        if any(c["name"].startswith(p) for p in PROV):
            s += 3
        before = text[max(0, c["at"] - 1):c["at"]]
        if c["at"] == 0 or before.isspace() or before == "":
            s += 2
        if len(c["name"]) >= 6:
            s += 1
        # 红头文件的发文机关在开头，靠后的多半是"报送至××"或落款转发
        if c["at"] < 600:
            s += 2
        if re.search(r"各|有关|相关|所属", c["name"]):
            s -= 2
        prev2 = text[max(0, c["at"] - 2):c["at"]]
        if re.search(r"[至给到送往及]", prev2):
            s -= 3
        c["score"] = s
    lst.sort(key=lambda c: (-c["score"], -c["at"]))
    best = lst[0]
    return {"val": best["name"], "ev": best["name"],
            "score": min(0.95, 0.72 + best["score"] * 0.04)}


def pick_docno(text: str):
    m = re.search(r"([\u4e00-\u9fa5]{2,10})?\s*[〔\[【(（]\s*(20\d{2})\s*[〕\]】)）]\s*第?\s*(\d{1,4})\s*号", text)
    if m:
        pre = clean_org(m.group(1)) if m.group(1) else ""
        return {"val": "%s〔%s〕%s号" % (pre, m.group(2), m.group(3)),
                "ev": m.group(0).strip(), "score": 0.97}
    m2 = re.search(r"(20\d{2})\s*年度?\s*第\s*(\d{1,4})\s*号", text)
    if m2:
        return {"val": "%s年第%s号" % (m2.group(1), m2.group(2)), "ev": m2.group(0), "score": 0.8}
    return None


def find_dates(text: str):
    out = []
    for m in DATE_RE.finditer(text):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            out.append({"iso": "%04d-%02d-%02d" % (y, mo, d), "raw": m.group(0), "at": m.start()})
    return out


def pick_date(dates, text: str = "", docno: str = ""):
    """选发布日期。

    政务页面里常同时出现成文日期、有效期截止、申报截止等多种日期；
    取"最后一个"会把有效期误当成发文日期。优先级：
      ① 「印发/成文/发布/签发」附近 60 字内的日期；
      ② 文号同一行或其后 160 字内的日期（红头文件排版惯例）；
      ③ 正文中最早出现的日期；
    凡紧邻"截止/有效期/之前"等字样的日期，一律先排除——那是截止日，不是发文日。
    """
    if not dates:
        return None

    bad_at: set[int] = set()
    if text:
        for kw in ("截止", "有效期", "之前", "前将", "申报时间", "受理时间", "止于", "过期",
                   "今天是", "现在时间", "当前时间"):
            for m in re.finditer(kw, text):
                for d in dates:
                    if -40 <= d["at"] - m.start() <= 12:
                        bad_at.add(d["at"])
    good = [d for d in dates if d["at"] not in bad_at] or dates

    # ① 文号附近：红头文件里文号与成文日期通常同一行，比"印发"字样更可靠
    if text and docno:
        i = _docno_pos(text, docno)
        if i >= 0:
            cand = [d for d in good if 0 <= d["at"] - i <= 160]
            if cand:
                d = cand[0]
                return {"val": d["iso"], "ev": d["raw"], "score": 0.94, "at": d["at"]}

    # ② 发布/印发时间：政务网站详情页几乎都有「发布时间：」，比正文里的日期可靠
    if text:
        for kw in ("发布时间", "发布日期", "印发日期", "成文日期", "签发日期", "印发"):
            for m in re.finditer(kw, text):
                cand = [d for d in good if abs(d["at"] - m.start()) <= 60]
                if cand:
                    d = min(cand, key=lambda x: abs(x["at"] - m.start()))
                    return {"val": d["iso"], "ev": d["raw"], "score": 0.93, "at": d["at"]}

    # ③ 最早日期（红头文件标题下方即为成文日期）
    first = good[0]
    return {"val": first["iso"], "ev": first["raw"], "score": 0.85, "at": first["at"]}


def _docno_pos(text: str, docno: str) -> int:
    """定位文号。网页常把〔〕写成 [] 或【】，做一次括号归一后再找。"""
    i = text.find(docno)
    if i >= 0:
        return i
    core = re.sub(r"[〔\[【(（].*?[〕\]】)）]", "§", docno)
    m = re.search(core.replace("§", r"[〔\[【(（].{0,8}?[〕\]】)）]"), text)
    return m.start() if m else -1


def pick_region(text: str, issuer: str | None):
    if issuer:
        p = next((x for x in PROV if x in issuer), None)
        if p:
            label = AUTON.get(p) or (p + "市" if p in MUNI else p + "省")
            return {"val": label, "ev": issuer, "score": 0.92}
        if re.search(r"部$|总局|国务院", issuer):
            return {"val": "全国", "ev": issuer, "score": 0.85}
    for p in PROV:
        m = re.search(r"(?:在|面向|限于|覆盖)?%s(?:省|市|自治区)?(?:境内|范围内)?注册" % p, text)
        if m:
            return {"val": AUTON.get(p) or p + "省", "ev": m.group(0), "score": 0.78}
    return None


def pick_industry(text: str):
    cnt = {}
    for k in IND_KEYS:
        n = text.count(k)
        if n:
            cnt[k] = n
    if not cnt:
        return None
    top_n = max(cnt.values())
    top = [k for k, v in cnt.items() if v == top_n][:2]
    return {"val": "、".join(top), "ev": top[0], "score": 0.82}


def pick_support(text: str):
    seg = [s for s, _ in sents(text) if re.search(r"补助|补贴|奖励|奖补|资助|贴息|返还", s)]
    if not seg:
        return None

    def sc(s):
        v = 0
        if re.search(r"支持标准|补助标准|奖励标准|补贴标准|资助标准|资金支持|支持措施|奖补标准", s):
            v += 3
        if re.search(r"万元|亿元", s):
            v += 2
        if re.search(r"%|％|百分之", s):
            v += 1
        if re.search(r"另行|由各|各市确定|原则上|具体比例|区间", s):
            v -= 2
        if len(s) > 120:
            v -= 1
        return v

    best = max(seg, key=sc)
    # 政务网页里标题常被拼进正文："…关于××的通知"且没有任何金额/比例数字，不是支持标准
    if re.search(r"关于.{4,80}(通知|公告|方案|办法|公示|名单)", best) \
            and not re.search(r"万元|亿元|%|％|百分之", best):
        return None
    val = re.sub(r"^[一二三四五六七八九十]+[、\.]\s*", "", best).rstrip("。；")
    if len(val) > 90:
        m = re.search(r".{0,45}(?:万元|亿元|%|％|百分之).{0,25}", val)
        if m:
            val = m.group(0)
    vague = bool(re.search(r"另行|由各|各市确定|具体比例|区间|\d+\s*[%％]\s*[—\-~至]\s*\d+\s*[%％]", val))
    return {"val": val.strip(), "ev": best, "score": 0.62 if vague else 0.88, "vague": vague}


def pick_deadline(text: str):
    lst = sents(text)
    KW = re.compile(r"截止|截至|申报时间|申报时限|申报期限|报送时间|时间安排|受理截止|前完成|前提交|前报送|前申报|前将|不晚于")
    hits = [i for i, (s, _) in enumerate(lst) if KW.search(s)]
    if not hits:
        return {"miss": "全文中未出现“截止/申报时间”类表述"}
    for i in hits:
        for k in range(i, min(len(lst), i + 3)):
            sd = find_dates(lst[k][0])
            if sd:
                last = sd[-1]
                ctx = " ".join(s for s, _ in lst[i:k + 1])
                strong = bool(re.search(r"截止|截至|不晚于|前完成|前提交|前报送|前申报|前将", ctx))
                return {"val": last["iso"], "ev": lst[i][0], "score": 0.9 if strong else 0.74}
    return {"miss": "出现“申报时间”表述，但其中未写明具体日期（可能为常年受理或另行通知）"}


def _page_of(pages: list[str], needle: str) -> int:
    if len(pages) <= 1 or not needle:
        return 1
    for i, p in enumerate(pages):
        if needle[:20] in p:
            return i + 1
    return 1


# ---------------------------------------------------------------- 主入口

def parse_text(name: str, text: str, *, pages: list[str] | None = None,
               note: str = "文本读取", doc_id: str = "", page_text: str = "") -> dict:
    """把一份公告文本解析成前端直接可用的记录。

    page_text 只影响「原文定位」的展示（HTML 正文容器），字段抽取始终用全文 text。
    """
    pages = pages or [text]
    text = norm("\n".join(pages))
    doc = {
        "id": doc_id, "name": name,
        "type": os.path.splitext(name)[1].lstrip(".").upper() or "TXT",
        "pages": max(1, len([p for p in pages if p.strip()])),
        "real": True,
        "f": {k: None for k, _ in FIELDS},
        "hl": {},
    }

    if len(re.sub(r"\s", "", text)) < 60:
        doc.update({"status": "failed", "conf": 0,
                    "issue": "未取得有效文本（%s），未做任何字段推测" % note,
                    "page": page_text or text or "［无文本内容］", "pageLabel": "第 1 页"})
        return doc

    dates = find_dates(text)
    issuer = pick_issuer(text)
    region = pick_region(text, issuer["val"] if issuer else None)
    support = pick_support(text)
    deadline = pick_deadline(text)
    docno = pick_docno(text)

    for key, r in (("issuer", issuer), ("docNo", docno),
                   ("date", pick_date(dates, text, (docno or {}).get("val") or "")),
                   ("region", region), ("industry", pick_industry(text)),
                   ("support", support), ("deadline", deadline)):
        if r and r.get("val"):
            doc["f"][key] = r["val"]
            ev = r.get("ev") or r["val"]
            doc["hl"][key] = ev if ev in text else r["val"]
            doc["s_" + key] = r.get("score", 0.8)

    filled = sum(1 for k, _ in FIELDS if doc["f"][k])
    qs = [doc["s_" + k] for k, _ in FIELDS if ("s_" + k) in doc]
    avgq = sum(qs) / len(qs) if qs else 0
    conf = 0.20 + filled / len(FIELDS) * 0.5 + avgq * 0.3
    if support and support.get("vague"):
        conf -= 0.05
    doc["conf"] = round(max(0.34, min(0.97, conf)), 2)

    low = []
    if support and support.get("vague"):
        low.append("support")
    if deadline and deadline.get("score", 1) < 0.8:
        low.append("deadline")
    if low:
        doc["lowFields"] = low

    notes = []
    if not doc["f"]["deadline"]:
        notes.append(deadline.get("miss") or "未识别到申报截止时间，字段留空")
    if support and support.get("vague"):
        notes.append("支持标准为区间或授权性表述，无法收敛为单一数值")
    missing = [lb for k, lb in FIELDS if not doc["f"][k]]
    if missing and not notes:
        notes.append("未识别字段：" + "、".join(missing))
    doc["issue"] = "；".join(notes)

    if filled == 0:
        doc.update({"status": "failed", "conf": 0,
                    "issue": "未识别到任何关键字段，请确认该文本是否为政策公告正文"})
        doc["page"] = text[:6000] + ("…" if len(text) > 6000 else "")
        doc["pageLabel"] = "第 1 页"
        return _clean(doc)

    doc["status"] = "ok" if (filled >= 6 and not low) else "partial"
    anchor = doc["hl"].get("issuer") or doc["hl"].get("support") or ""
    shown = page_text or text
    doc["page"] = shown[:6000] + ("…" if len(shown) > 6000 else "")
    doc["pageLabel"] = "第 %d 页" % _page_of(pages, anchor)
    return _clean(doc)


def _clean(doc: dict) -> dict:
    for k in list(doc):
        if k.startswith("s_"):
            del doc[k]
    return doc


def parse_bytes(name: str, data: bytes, *, doc_id: str = "") -> dict:
    pages, note, display = read_bytes_full(name, data)
    if not any(p.strip() for p in pages):
        return parse_text(name, "", pages=[], note=note, doc_id=doc_id, page_text=display)
    return parse_text(name, "\n".join(pages), pages=pages, note=note, doc_id=doc_id,
                      page_text=display)


def scan_dir(dirpath: str | pathlib.Path) -> tuple[list[dict], list[str]]:
    """扫描目录下所有支持的文件，返回 (记录列表, 跳过说明)。"""
    p = pathlib.Path(dirpath)
    if not p.exists():
        return [], [f"目录不存在：{p}"]
    # README 是本目录的说明文件，不是待解析材料；否则会被当成一份"字段缺失的公告"
    files = [f for f in sorted(p.rglob("*")) if f.is_file()
             and f.suffix.lower() in SUPPORTED and not f.name.startswith(".")
             and not f.name.lower().startswith("readme")]
    docs, skipped = [], []
    for i, f in enumerate(files, 1):
        try:
            docs.append(parse_bytes(f.name, f.read_bytes(), doc_id="D%02d" % i))
        except Exception as e:
            skipped.append(f"{f.name}：{type(e).__name__}: {e}")
    return docs, skipped


def stats(docs: list[dict]) -> dict:
    ok = sum(1 for d in docs if d.get("status") == "ok")
    partial = sum(1 for d in docs if d.get("status") == "partial")
    failed = sum(1 for d in docs if d.get("status") == "failed")
    fields = sum(1 for d in docs for k, _ in FIELDS if d["f"].get(k))
    locatable = sum(1 for d in docs for k, _ in FIELDS if d["f"].get(k) and d["hl"].get(k))
    confs = [d["conf"] for d in docs if d.get("status") != "failed"]
    return {
        "docs": len(docs), "ok": ok, "partial": partial, "failed": failed,
        "fields": fields, "locatable": locatable,
        "avg_conf": round(sum(confs) / len(confs), 2) if confs else 0,
        "review": len([d for d in docs if d.get("status") != "ok" or d.get("lowFields")]),
    }
