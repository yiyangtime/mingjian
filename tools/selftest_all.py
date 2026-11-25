"""全量回归自测：把改一处坏一处的风险点固化成断言。

跑法：python tools/selftest_all.py

覆盖：
  架构边界      —— 复用 selftest_arch.py：后端分层依赖、包根白名单、路由唯一性、前端功能隔离、视图体积棘轮
  政策解析      —— 复用 selftest_policy.py 的 4 个样本（子进程跑，保持原脚本可独立使用）
  指标拆分      —— 累计数转单季、缺期不出点、报告期名称映射
  专题归因      —— 毛利变动的单位盈利效应与结构效应、按名称对齐的同比
  政策取数      —— 行业词优先级、省份识别、政策标题判定、相关度打分
  竞品矩阵      —— 维度×公司对齐，空维度不占行
  技能与记忆    —— 注册表拦截、参数校验、记忆去重/召回/删除、上下文预算截断
  调度与落库    —— 每天/每周下次触发时间、事件 seq、检查点、定时任务读写
  离线优化      —— 候选校验、基线对照、门禁拒绝、草案抽取、待标注采集、上线与回滚

纯函数为主，不依赖网络；联网链路由 API 冒烟另行验证。
"""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mingjian.core import context  # noqa: E402
from mingjian.infra import llmcfg, scheduler, store  # noqa: E402
from mingjian.infra.sources import cninfo  # noqa: E402
from mingjian.runtime import compare  # noqa: E402
from mingjian.services import discover, extract, govpolicy, memory, metrics, optimizer, skills, subject, topic  # noqa: E402

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


# ---------------------------------------------------------------- 政策解析

@case("架构边界（子进程）")
def _arch_gate():
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "selftest_arch.py")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0, (r.stdout or "")[-1200:]


@case("政策解析 4 个样本（子进程）")
def _policy_samples():
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "selftest_policy.py")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0, (r.stdout or "")[-1200:]


# ---------------------------------------------------------------- 指标拆分

@case("累计数拆单季：逐期差分正确")
def _quarters_basic():
    cm = {(2026, 1): 100.0, (2026, 2): 250.0, (2026, 3): 420.0, (2026, 4): 600.0}
    q = dict(metrics._quarters(cm))
    assert q["2026一季报"] == 100.0, q
    assert q["2026中报"] == 150.0, q
    assert q["2026三季报"] == 170.0, q
    assert q["2026年报"] == 180.0, q


@case("累计数拆单季：缺上一期就不出点")
def _quarters_gap():
    q = dict(metrics._quarters({(2026, 1): 100.0, (2026, 3): 420.0}))
    assert "2026三季报" not in q, q


@case("报告期名称映射")
def _period_name():
    assert metrics.period_name_of("2026-03-31") == "2026一季报"
    assert metrics.period_name_of("2026-06-30") == "2026中报"
    assert metrics.period_name_of("2026-09-30") == "2026三季报"
    assert metrics.period_name_of("2026-12-31") == "2026年报"
    assert metrics.period_name_of("2026-07-15") == ""
    assert metrics.period_name_of("") == ""


@case("报告期分类覆盖四类定期报告")
def _report_cat():
    for k in ("一季报", "中报", "三季报", "年报"):
        assert k in cninfo._REPORT_CAT, k
        assert k in cninfo._REPORT_WINDOW, k
        assert cninfo._REPORT_NAME_RE.search(f"2026{k}"), k


# ---------------------------------------------------------------- 专题归因

@case("归因：单一业务毛利率下降 5 点")
def _decompose_margin():
    me, xe, _ = topic._decompose(
        [{"name": "A", "ratio": 100.0, "gm": 20.0}],
        [{"name": "A", "ratio": 100.0, "gm": 25.0}])
    assert abs(me - (-5.0)) < 1e-9, me
    assert abs(xe) < 1e-9, xe


@case("归因：结构迁移单独贡献 -10 点")
def _decompose_mix():
    # 期初全在高毛利业务（30%），期末一半挪去低毛利业务（10%），综合毛利率 30% → 20%
    me, xe, _ = topic._decompose(
        [{"name": "A", "ratio": 50.0, "gm": 30.0}, {"name": "B", "ratio": 50.0, "gm": 10.0}],
        [{"name": "A", "ratio": 100.0, "gm": 30.0}, {"name": "B", "ratio": 0.0, "gm": 10.0}])
    assert abs(me) < 1e-9, me
    assert abs(xe - (-10.0)) < 1e-9, xe


@case("按名称对齐两期：同比与增量")
def _delta_align():
    out = topic._delta(
        [{"name": "A", "income": 100.0, "ratio": 60.0, "gm": 20.0}],
        [{"name": "A", "income": 80.0, "ratio": 55.0, "gm": 22.0}])
    d = out[0]
    assert abs(d["dgm"] - (-2.0)) < 1e-9, d
    assert abs(d["dratio"] - 5.0) < 1e-9, d
    assert abs(d["dincome"] - 20.0) < 1e-9, d
    assert d["income_prev"] == 80.0, d


@case("按名称对齐两期：新增业务无同比时留空不猜")
def _delta_new():
    out = topic._delta([{"name": "新业务", "income": 10.0, "ratio": 5.0, "gm": 8.0}], [])
    d = out[0]
    assert d["dgm"] is None and d["dratio"] is None and d["dincome"] is None, d


# ---------------------------------------------------------------- 政策取数

@case("行业词：行业分类优先，不被主营描述带偏")
def _kw_priority():
    kws = govpolicy._kw_set("机械设备-机器人-工业机器人", "配套电池产线")
    assert kws[0] == "智能工厂", kws
    assert "储能" not in kws[:5], kws


@case("省份识别：城市能正确归省")
def _province():
    assert govpolicy.province_of("深圳市龙华区观湖街道") == "广东"
    assert govpolicy.province_of("福建省宁德市蕉城区") == "福建"
    assert govpolicy.province_of("江苏省苏州市工业园区") == "江苏"
    assert govpolicy.province_of("") == ""
    assert govpolicy.province_of("地址不详") == ""


@case("政策标题判定：排除新闻稿与企业审批")
def _looks_policy():
    assert govpolicy._looks_policy("关于组织申报2026年专精特新企业的通知")
    assert not govpolicy._looks_policy("工业和信息化部召开新闻发布会")
    assert not govpolicy._looks_policy("某公司节能报告的审查意见")
    assert not govpolicy._looks_policy("短标题")
    assert not govpolicy._looks_policy("")


@case("相关度打分：行业词与资金词都加权")
def _score():
    hi = govpolicy._score("2026年储能专项资金申报通知", ["储能"])
    lo = govpolicy._score("2026年机关工会活动安排", ["储能"])
    assert hi >= 4, hi
    assert lo == 0, lo
    assert hi > lo


@case("通道诊断：未收录省份必须报不可用")
def _unknown_province():
    items, diag = govpolicy.list_province("火星", ["储能"], 3)
    assert items == []
    assert diag["ok"] is False and "未收录" in diag["reason"], diag


# ---------------------------------------------------------------- 竞品矩阵

@case("竞品矩阵：维度对齐，空维度不占行")
def _matrix():
    packs = [
        {"company": "A", "facts": [
            {"type": "capacity", "ref": "A-01", "statement": "扩产"},
            {"type": "order", "ref": "A-02", "statement": "中标"}]},
        {"company": "B", "facts": [
            {"type": "capacity", "ref": "B-01", "statement": "投产"}]},
    ]
    rows = compare._matrix(packs)
    assert [r["key"] for r in rows] == ["capacity", "order"], [r["key"] for r in rows]
    assert rows[0]["cells"]["A"]["n"] == 1
    assert rows[0]["cells"]["B"]["n"] == 1
    assert rows[1]["cells"]["B"]["n"] == 0
    assert rows[0]["cells"]["A"]["ids"] == ["A-01"]


# ---------------------------------------------------------------- 非上市通道

@case("自述档案：只生成填过的维度，没填的不造")
def _subject_baseline():
    sub = subject.build({"name": "苏州某精密传动有限公司", "products": "谐波减速器",
                         "capacity": "年产 12 万台", "region": "苏州"})
    facts = subject.baseline(sub)
    dims = [f["type"] for f in facts]
    assert dims == ["product", "capacity"], dims          # 地区不属于对比维度
    assert all(f["declared"] and f["source"] == "企业自述" for f in facts)
    assert any("年产 12 万台" in f["statement"] for f in facts), facts


@case("自述档案：覆盖度按竞品维度算，未填的维度要报出来")
def _subject_coverage():
    sub = subject.build({"name": "某公司", "products": "减速器", "capacity": "1 万台",
                         "customers": "机器人厂"})
    cov = subject.coverage(sub, compare.DIMENSIONS)
    assert cov["total"] == 8, cov
    assert cov["filled"] == 3, cov
    blank = [k for k, _ in cov["blank"]]
    assert "policy" in blank and "finance" in blank, blank


@case("竞品矩阵：我方列标自述，且不算「多家同时动作」")
def _matrix_self():
    sub = subject.build({"name": "我方公司", "products": "减速器", "capacity": "1 万台"})
    self_pack = {"company": "我方公司", "tag": "我方", "self": True,
                 "facts": subject.baseline(sub), "subject": sub}
    packs = [{"company": "A", "facts": [{"id": "F01", "type": "product", "statement": "新品"}]},
             {"company": "B", "facts": [{"id": "F02", "type": "product", "statement": "新品"}]}]
    rows = compare._matrix(packs, self_pack)
    r = next(x for x in rows if x["key"] == "product")
    assert r["cells"]["我方公司"]["declared"] is True
    js = compare._judgments(packs, rows, self_pack)
    j02 = [j for j in js if j["id"] == "J02"]
    assert j02, "两家同时动作，应当有 J02"
    assert "我方公司" not in j02[0]["who" if "who" in j02[0] else "text"], j02[0]
    assert not any(i.startswith("S") for i in j02[0]["basis"].replace("、", ",").split(",")), j02[0]


@case("事件分类：以标题定性，正文顺手提到的词不能定性")
def _classify_title_first():
    # 公告正文几乎必提"董事会"，按正文判会把所有公告都算成人事变动
    assert extract.classify("关于使用暂时闲置募集资金和自有资金进行现金管理进展的公告",
                            "经公司董事会审议通过。")[0] == "finance"
    assert extract.classify("关于公司核心技术人员逝世的公告", "董事会公告。")[0] == "personnel"
    assert extract.classify("关于持股5%以上股东权益变动触及1%整数倍的公告",
                            "本次变动系股东减持。")[0] == "finance"
    # 标题不带信息、正文只零星提到一个词：不硬判，留给人工
    assert extract.classify("关于近期经营情况的公告", "董事会审议。")[0] == "other"


@case("线索发现：从正文抽企业名，丢掉非主体词")
def _discover_names():
    txt = "本轮投资方包括某某投资集团，标的公司为北京某某科技股份有限公司，" \
          "该公司与工业和信息化部对接，另有某某激光科技产业集团股份有限公司参与。"
    names = discover.extract_names(txt)
    assert "北京某某科技股份有限公司" in names, names
    assert "某某激光科技产业集团股份有限公司" in names, names
    assert not any(n.startswith("该公司") or "工业和信息化部" in n for n in names), names


@case("线索发现：动作词不能粘进企业名")
def _discover_name_prefix():
    # 正则从后缀往前抓 20 字，会把"拟以现金方式收购"整句当名字，名单就没法看了
    for raw, want in [
        ("拟以现金方式收购深圳市某某机电有限公司100%股权", "深圳市某某机电有限公司"),
        ("5亿元现金收购深圳市某某机电有限公司", "深圳市某某机电有限公司"),
        ("某某投资合伙企业持有的深圳市某某机电有限公司", "深圳市某某机电有限公司"),
        ("股东广东某某集团有限公司持股1.27亿股", "广东某某集团有限公司"),
    ]:
        names = discover.extract_names(raw)
        assert names and names[0] == want, (raw, names)


@case("线索发现：标题主体位能抽到不带「有限公司」的简称")
def _discover_short_names():
    # 财经标题的对手常是简称，只按工商全称抽会全部漏掉，反而只剩通稿里的并购标的
    assert discover.title_subjects("某某精密：2026年上半年净利润同比增长12.97%") == ["某某精密"]
    assert discover.title_subjects("IPO雷达丨某工业品平台赴港上市遭证监会点名") == ["某工业品平台"]
    # 全角字母必须归一，否则"某某宏Ａ"与"某某宏A"会被判成两家公司
    assert discover.title_subjects("某某宏Ａ：拟5.5亿元收购某某机电") == ["某某宏A"]
    # 栏目名不是主体
    assert discover.title_subjects("上证早知道｜000000，前三季净利大增") == []


@case("线索发现：行情稿与公告汇总稿不进名单")
def _discover_noise_gate():
    for t in ["5.5亿！近300%增值率！跨界并购，000000“一字”涨停",
              "11月11日晚间沪深上市公司重大事项公告最新快递",
              "AI漫剧营收占比仅为2.57% 16天9板短剧概念牛股提示风险|盘后公告",
              "直线拉升！000000涨停，封单超150万手"]:
        assert extract.NOISE_NEWS.search(t), t


@case("事实归属：新闻标题没提这家公司就不产事实")
def _news_subject_gate():
    doc = {"kind": "news", "title": "某某传动加码机器人赛道，多品类减速器研发推进",
           "body": "某某谐波也在扩产。", "evidence_level": "full", "id": "D1",
           "published_at": "2026-11-19"}
    assert extract.build_fact(doc, {"某某谐波"}) is None
    doc2 = dict(doc, title="某某谐波前三季业绩高增")
    assert extract.build_fact(doc2, {"某某谐波"}) is not None


@case("事实归属：行情类标题不进产业事实")
def _market_noise_gate():
    doc = {"kind": "news", "title": "某某谐波大涨，科创板平均股价创年内新高",
           "body": "", "evidence_level": "snippet", "id": "D2", "published_at": "2026-11-19"}
    assert extract.build_fact(doc, {"某某谐波"}) is None


# ---------------------------------------------------------------- 技能与记忆

@case("技能注册表：未知技能、内部技能与参数校验都拒绝")
def _skills_guard():
    pub = {s["name"]: s for s in skills.public()}
    assert "run.pipeline" in pub and pub["run.pipeline"]["available"] is False, pub.keys()
    assert "memory.remember" in pub and pub["memory.remember"]["available"] is True
    bad = [("nope", {}),
           ("run.pipeline", {"company": "测试"}),
           ("entity.resolve", {}),
           ("entity.resolve", {"company": "测试", "zzz": 1}),
           ("metric.query", {"company": "测试", "metric": "not-in-list"})]
    for name, args in bad:
        try:
            skills.run(name, args)
        except ValueError:
            continue
        raise AssertionError(f"应拒绝：{name} {args}")
    assert skills.run("memory.recall", {"query": "", "limit": 2})["ok"] is True


@case("记忆：同内容去重合并引用，召回命中，删除生效")
def _memory_roundtrip():
    import shutil
    import tempfile
    tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="mingjian_mem_"))
    old_path, old_url, old_conn = store.DB_PATH, store.DB_URL, getattr(store._local, "conn", None)
    store.DB_PATH = tmpdir / "t.db"
    store.DB_URL = ""
    store._local.conn = None
    try:
        store.init()
        a = memory.remember("000000", "某某科技", "动力电池产能扩张，海外基地投产",
                            "fact", refs=["R1"], confidence=0.9)
        b = memory.remember("000000", "某某科技", "动力电池产能扩张，海外基地投产",
                            "fact", refs=["R2"], confidence=0.8)
        assert a["id"] == b["id"], (a, b)
        assert len(b["refs"]) == 2 and b["confidence"] == 0.9, b
        memory.remember("global", "口径", "毛利率按合并口径计算", "caliber")
        hit = memory.recall("海外产能", "000000", limit=5)
        assert hit and hit[0]["id"] == a["id"], hit
        assert memory.stats()["total"] == 2, memory.stats()
        assert memory.forget(a["id"]) and memory.get(a["id"]) is None
        assert memory.stats()["total"] == 1
    finally:
        if getattr(store._local, "conn", None) is not None:
            store._local.conn.close()
        store._local.conn = old_conn
        store.DB_PATH = old_path
        store.DB_URL = old_url
        shutil.rmtree(tmpdir, ignore_errors=True)


@case("记忆沉淀：从运行事实生成带引用的记忆")
def _memory_harvest():
    import shutil
    import tempfile
    tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="mingjian_hv_"))
    old_path, old_url, old_conn = store.DB_PATH, store.DB_URL, getattr(store._local, "conn", None)
    store.DB_PATH = tmpdir / "t.db"
    store.DB_URL = ""
    store._local.conn = None
    try:
        store.init()
        rid = "T20260101000000-9001"
        store.create_run(rid, "000000", "某某科技", {})
        store.add_document({"id": f"{rid}-D01", "run_id": rid, "entity_key": "000000",
                            "kind": "announcement", "title": "公告",
                            "url": "https://example.com/a", "source": "巨潮",
                            "published_at": "2026-11-20", "body": "正文",
                            "evidence_level": "full"})
        store.save_facts(rid, "000000", [
            {"id": "F01", "doc_id": f"{rid}-D01", "type": "capacity", "type_label": "产能",
             "statement": "新增产能 10 万台", "confidence": 0.9, "event_date": "2026-11-20"},
            {"id": "F02", "doc_id": f"{rid}-D01", "type": "order", "type_label": "订单",
             "statement": "中标某项目", "confidence": 0.7}])
        out = memory.harvest_run(rid)
        assert out["written"] == 3, out
        items = memory.recall("新增产能", "000000", limit=5)
        assert items and items[0]["kind"] == "fact", items
        assert any(isinstance(r, dict) and r.get("url") == "https://example.com/a"
                   for r in items[0]["refs"]), items[0]["refs"]
    finally:
        if getattr(store._local, "conn", None) is not None:
            store._local.conn.close()
        store._local.conn = old_conn
        store.DB_PATH = old_path
        store.DB_URL = old_url
        shutil.rmtree(tmpdir, ignore_errors=True)


@case("上下文预算：超限只截尾部并保留事实编号")
def _context_budget():
    facts = [{"id": f"F{i:02d}", "type_label": "产能", "statement": "扩产" * 40}
             for i in range(1, 21)]
    text, meta = context.facts_block(facts, budget=60)
    assert meta["truncated"] and meta["facts"] < 20, meta
    assert text.startswith("[F01]") and "按上下文预算截断" in text, text[:80]
    assert context.estimate("中文") >= 2


# ---------------------------------------------------------------- 调度与落库

@case("定时调度：每天/每周的下次触发时间")
def _sched_next():
    from datetime import datetime
    mon = datetime(2026, 11, 23, 10, 30)          # 周一
    assert scheduler.next_run_text({"freq": "daily", "time": "09:00"}, mon) == "2026-11-24 09:00:00"
    assert scheduler.next_run_text({"freq": "daily", "time": "18:00"}, mon) == "2026-11-23 18:00:00"
    assert scheduler.next_run_text({"freq": "weekly", "weekday": 5, "time": "16:00"}, mon) == "2026-11-27 16:00:00"
    assert scheduler.next_run_text({"freq": "weekly", "weekday": 1, "time": "09:00"}, mon) == "2026-11-30 09:00:00"
    assert scheduler.describe({"freq": "weekly", "weekday": 5, "time": "16:00"}) == "每周五 16:00"
    for bad in ({"freq": "monthly", "time": "09:00"},
                {"freq": "daily", "time": "25:00"},
                {"freq": "weekly", "weekday": 8, "time": "09:00"}):
        try:
            scheduler.validate(bad)
        except ValueError:
            continue
        raise AssertionError("应拒绝非法调度规则：" + str(bad))


@case("事件、检查点与定时任务落库")
def _store_events():
    import shutil
    import tempfile
    tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="mingjian_test_"))
    old_path, old_url, old_conn = store.DB_PATH, store.DB_URL, getattr(store._local, "conn", None)
    store.DB_PATH = tmpdir / "t.db"
    store.DB_URL = ""
    store._local.conn = None
    try:
        store.init()
        rid = "T20260101000000-0001"
        store.append_event(rid, 1, {"type": "step", "idx": 1, "name": "实体解析", "status": "start"})
        store.append_event(rid, 2, {"type": "log", "msg": "开始采集"})
        assert store.max_event_seq(rid) == 2
        evs = store.events_of_run(rid, 2)
        assert len(evs) == 1 and evs[0]["msg"] == "开始采集" and evs[0]["seq"] == 2

        store.save_checkpoint(rid, 2, {"entity_key": "000000"})
        store.save_checkpoint(rid, 4, {"entity_key": "000000"})
        cp = store.last_checkpoint(rid)
        assert cp and cp["step"] == 4 and cp["payload"]["entity_key"] == "000000"

        sid = "STEST-0001"
        store.upsert_schedule({"id": sid, "name": "测试任务", "kind": "run",
                               "params": {"company": "某某科技"},
                               "spec": {"freq": "daily", "time": "09:00"},
                               "enabled": True, "next_run_at": "2026-11-24 09:00:00"})
        s = store.get_schedule(sid)
        assert s["spec"]["freq"] == "daily" and s["params"]["company"] == "某某科技"
        assert [x["id"] for x in store.due_schedules("2026-11-24 09:00:00")] == [sid]
        store.set_schedule_enabled(sid, False)
        assert store.due_schedules("2026-11-24 09:00:00") == []
    finally:
        if getattr(store._local, "conn", None) is not None:
            store._local.conn.close()
        store._local.conn = old_conn
        store.DB_PATH = old_path
        store.DB_URL = old_url
        shutil.rmtree(tmpdir, ignore_errors=True)


@case("服务事件推送：落库后能重建步骤条")
def _server_events():
    import shutil
    import tempfile

    from mingjian import server

    tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="mingjian_srv_"))
    old_path, old_url, old_conn = store.DB_PATH, store.DB_URL, getattr(store._local, "conn", None)
    store.DB_PATH = tmpdir / "t.db"
    store.DB_URL = ""
    store._local.conn = None
    try:
        store.init()
        rid = "T20260101000000-0002"
        job = {"id": rid, "company": "测试股份", "status": "queued", "steps": {},
               "events": [], "result": None, "error": None, "seq": 0}
        server._job_push(job, {"type": "step", "idx": 1, "name": "实体解析", "status": "start"})
        server._job_push(job, {"type": "log", "level": "info", "msg": "开始采集"})
        server._job_push(job, {"type": "step", "idx": 1, "name": "实体解析",
                               "status": "done", "detail": "000000"})
        steps = server._steps_of(rid)
        assert len(steps) == 1 and steps[0]["status"] == "done" and steps[0]["detail"] == "000000"
        evs = store.events_of_run(rid, 2)
        assert len(evs) == 2 and evs[0]["msg"] == "开始采集" and evs[1]["seq"] == 3
        assert store.max_event_seq(rid) == 3
    finally:
        if getattr(store._local, "conn", None) is not None:
            store._local.conn.close()
        store._local.conn = old_conn
        store.DB_PATH = old_path
        store.DB_URL = old_url
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------- 数据库方言

@case("数据库方言：占位符转译、upsert 语句、密码隐去")
def _db_dialects():
    from mingjian.config import mask_db_url
    from mingjian.infra import db as dbmod

    assert dbmod.dialect_of("") == "sqlite"
    assert dbmod.dialect_of("sqlite:///data/x.db") == "sqlite"
    assert dbmod.dialect_of("postgresql://u:p@h:5432/d") == "postgres"
    assert dbmod.dialect_of("mysql://u:p@h:3306/d") == "mysql"

    # 字符串字面量里的 ? 不能被替换；引号内的 '' 转义要正确跳过
    assert dbmod.translate_sql("SELECT '?' , ? FROM t", "postgres") == "SELECT '?' , %s FROM t"
    assert dbmod.translate_sql("SELECT 'it''s ?', ?", "mysql") == "SELECT 'it''s ?', %s"
    assert dbmod.translate_sql("SELECT ?", "sqlite") == "SELECT ?"

    # MySQL 保留字 key 加反引号；按完整标识符匹配，不误伤 dedupe_key / entity_key
    assert (dbmod.translate_sql("SELECT key FROM subjects WHERE key=?", "mysql")
            == "SELECT `key` FROM subjects WHERE `key`=%s")
    assert (dbmod.translate_sql("SELECT dedupe_key FROM facts WHERE entity_key=?", "mysql")
            == "SELECT dedupe_key FROM facts WHERE entity_key=%s")
    assert (dbmod.translate_sql("SELECT key FROM subjects WHERE key=?", "postgres")
            == "SELECT key FROM subjects WHERE key=%s")

    # 字面量百分号要转义，否则 PyMySQL / psycopg 会当成参数格式符
    assert (dbmod.translate_sql("SELECT * FROM runs WHERE company LIKE '%科技%' AND id=?", "mysql")
            == "SELECT * FROM runs WHERE company LIKE '%%科技%%' AND id=%s")
    assert dbmod.like_op("postgres") == "ILIKE"
    assert dbmod.like_op("sqlite") == dbmod.like_op("mysql") == "LIKE"

    cols = ("id", "run_id", "seq")
    pg = dbmod.upsert_sql("run_events", cols, ("run_id", "seq"), "postgres")
    assert pg.startswith("INSERT INTO run_events"), pg
    assert "ON CONFLICT (run_id, seq) DO UPDATE SET" in pg, pg
    assert "id=EXCLUDED.id" in pg and "seq=EXCLUDED.seq" not in pg, pg
    assert dbmod.upsert_sql("t", cols, ("run_id", "seq"), "sqlite").startswith("INSERT OR REPLACE INTO")
    assert dbmod.upsert_sql("t", cols, ("run_id", "seq"), "mysql").startswith("REPLACE INTO")

    assert mask_db_url("postgresql://u:secret@h:5432/d") == "postgresql://u:***@h:5432/d"
    assert mask_db_url("sqlite:///data/x.db") == "sqlite:///data/x.db"


@case("数据库初始化：重复调用不重复建表，建表后读写正常")
def _db_init_twice():
    import shutil
    import tempfile
    tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="mingjian_init_"))
    old_path, old_url, old_conn = store.DB_PATH, store.DB_URL, getattr(store._local, "conn", None)
    store.DB_PATH = tmpdir / "t.db"
    store.DB_URL = ""
    store._local.conn = None
    try:
        store.init()
        store.init()               # 缓存命中，不应重复执行 DDL 或报错
        store.init(force=True)     # 显式重建也应幂等
        assert store.table_counts()["runs"] == 0
        store.create_run("T20260101000000-0001", "000000", "某某科技", {})
        assert store.count_runs() == 1
        assert store.dialect() == "sqlite" and store.db_size() > 0
    finally:
        if getattr(store._local, "conn", None) is not None:
            store._local.conn.close()
        store._local.conn = old_conn
        store.DB_PATH = old_path
        store.DB_URL = old_url
        shutil.rmtree(tmpdir, ignore_errors=True)


@case("模型配置：落库、重启恢复与停用优先")
def _llm_settings():
    import shutil
    import tempfile

    from mingjian.config import CFG

    tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="mingjian_llm_"))
    old_path, old_url, old_conn = store.DB_PATH, store.DB_URL, getattr(store._local, "conn", None)
    old_cfg = (CFG.llm_base, CFG.llm_key, CFG.llm_model, CFG.llm_timeout)
    store.DB_PATH = tmpdir / "t.db"
    store.DB_URL = ""
    store._local.conn = None
    try:
        out = llmcfg.save_profile({"base": "https://example.invalid/v1",
                                   "key": "sk-test-1234", "model": "test-model"})
        assert out["active"] and out["model"] == "test-model", out
        assert out["profiles"][0]["key_hint"].endswith("1234"), out
        assert "key" not in out["profiles"][0], out        # 页面不回传明文
        row = store.get_setting("llm")
        assert row["profiles"][0]["key"] == "sk-test-1234", row

        # 模拟重启：内存清空后从库里恢复
        CFG.llm_base = CFG.llm_key = CFG.llm_model = ""
        llmcfg.bootstrap()
        assert CFG.llm_enabled and CFG.llm_model == "test-model", CFG.to_dict()

        # 停用落库：环境变量再兜底也不能把模型顶回来
        llmcfg.clear()
        CFG.llm_base, CFG.llm_key, CFG.llm_model = "https://env.invalid/v1", "sk-env", "env-model"
        llmcfg.bootstrap()
        assert not CFG.llm_enabled, CFG.to_dict()

        # 停用不删配置，重新启用即可恢复
        out = llmcfg.activate(out["profiles"][0]["id"])
        assert out["active"] and CFG.llm_model == "test-model", out
    finally:
        if getattr(store._local, "conn", None) is not None:
            store._local.conn.close()
        store._local.conn = old_conn
        store.DB_PATH = old_path
        store.DB_URL = old_url
        CFG.llm_base, CFG.llm_key, CFG.llm_model, CFG.llm_timeout = old_cfg
        shutil.rmtree(tmpdir, ignore_errors=True)


@case("离线优化：候选门禁、上线、版本固定与回滚")
def _optimizer_gate():
    import json
    import shutil
    import tempfile

    from mingjian.infra import optstore

    tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="mingjian_opt_"))
    old_opt = optstore.OPT_DIR
    cases_path = ROOT / "tools" / "optimizer" / "event_cases.jsonl"
    candidate_path = ROOT / "tools" / "optimizer" / "candidate.example.json"
    optstore.OPT_DIR = tmpdir
    try:
        cases = optimizer.load_cases(cases_path)
        report = optimizer.evaluate_file(candidate_path, cases_path)
        assert report["gate"]["passed"], report["gate"]
        assert report["candidate_metrics"]["accuracy"] > report["baseline"]["metrics"]["accuracy"]

        result = optimizer.promote(candidate_path, cases_path, actor="selftest")
        assert result["version"] == "cand-20261124-01"
        assert extract.active_version() == "cand-20261124-01"
        rules = extract.active_rules()
        assert extract.classify("某某企业签订联合研发协议", rules=rules)[0] == "product"
        assert optimizer.status()["active"]["version"] == "cand-20261124-01"

        optimizer.rollback("baseline", actor="selftest")
        assert extract.active_version() == "baseline"
        assert extract.classify("某某企业签订联合研发协议", rules=extract.active_rules())[0] == "order"

        bad = {"kind": "event_rules", "version": "cand-bad-01", "base_version": "baseline",
               "add_keywords": {"risk": ["企业"]}}
        bad_path = tmpdir / "bad.json"
        bad_path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
        bad_report = optimizer.evaluate_file(bad_path, cases_path)
        assert not bad_report["gate"]["passed"], bad_report["gate"]
        try:
            optimizer.promote(bad_path, cases_path, actor="selftest")
        except ValueError:
            pass
        else:
            raise AssertionError("门禁未通过的候选不应上线")
        assert extract.active_version() == "baseline"
    finally:
        optstore.OPT_DIR = old_opt
        shutil.rmtree(tmpdir, ignore_errors=True)


@case("离线优化草案：误判样本抽词、不上线、非法候选被拒")
def _optimizer_draft():
    import shutil
    import tempfile

    from mingjian.infra import optstore

    tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="mingjian_draft_"))
    old_opt = optstore.OPT_DIR
    cases_path = ROOT / "tools" / "optimizer" / "event_cases.jsonl"
    optstore.OPT_DIR = tmpdir / "opt"
    try:
        result = optimizer.draft(cases_path, tmpdir / "drafts")
        path = pathlib.Path(result["path"])
        assert path.exists() and path.parent == tmpdir / "drafts", result["path"]
        cand = optimizer.load_candidate(path)
        assert "联合研发" in cand["add_keywords"].get("product", []), cand
        assert "战略投资" in cand["add_keywords"].get("finance", []), cand
        reviewed = {row["id"]: row["word"] for row in result["reviewed"]}
        assert reviewed.get("C17") == "联合研发", reviewed
        assert reviewed.get("C18") == "战略投资", reviewed

        report = optimizer.evaluate(cand, optimizer.load_cases(cases_path),
                                    base_rules=extract.default_rules(),
                                    base_version="baseline")
        assert report["gate"]["passed"], report["gate"]
        assert not report["errors"]["new"], report["errors"]
        assert extract.active_version() == "baseline", "草案不应改变生效规则"

        try:
            optimizer.draft(cases_path, tmpdir / "drafts", version=cand["version"])
        except ValueError:
            pass
        else:
            raise AssertionError("同名草案不应覆盖已有文件")

        bad_candidates = [
            {"kind": "other_rules", "version": "draft-bad-01",
             "add_keywords": {"product": ["联合研发"]}},
            {"kind": "event_rules", "version": "draft-bad-02",
             "add_keywords": {"other": ["联合研发"]}},
            {"kind": "event_rules", "version": "draft-bad-03",
             "add_keywords": {"product": ["联合研发"]}, "rules": []},
            {"kind": "event_rules", "version": "draft-bad-04",
             "add_keywords": {"product": ["联合\n研发"]}},
            {"kind": "event_rules", "version": "../draft-bad-05",
             "add_keywords": {"product": ["联合研发"]}},
        ]
        for item in bad_candidates:
            try:
                optimizer.validate_candidate(item)
            except ValueError:
                continue
            raise AssertionError(f"非法候选应被拒绝：{item}")
    finally:
        optstore.OPT_DIR = old_opt
        shutil.rmtree(tmpdir, ignore_errors=True)


@case("离线优化采集：运行事实导出待标注样本，不自动打标")
def _optimizer_harvest():
    import json
    import shutil
    import tempfile

    from mingjian.infra import optstore

    tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="mingjian_harvest_"))
    old_opt, old_path, old_url = optstore.OPT_DIR, store.DB_PATH, store.DB_URL
    old_conn = getattr(store._local, "conn", None)
    optstore.OPT_DIR = tmpdir / "opt"
    store.DB_PATH = tmpdir / "h.db"
    store.DB_URL = ""
    store._local.conn = None
    cases_path = ROOT / "tools" / "optimizer" / "event_cases.jsonl"
    try:
        store.init()
        store.create_run("R1", "某某企业", "某某企业", {})
        store.add_document({"id": "D1", "run_id": "R1", "entity_key": "某某企业",
                            "kind": "news", "title": "某某企业发布新型控制器",
                            "url": "https://example.invalid/a", "body": "正文摘要",
                            "evidence_level": "full"})
        store.add_document({"id": "D2", "run_id": "R1", "entity_key": "某某企业",
                            "kind": "announcement", "title": "某某企业翌日披露报表",
                            "url": "https://example.invalid/b", "body": "",
                            "evidence_level": "full"})
        store.save_facts("R1", "某某企业", [
            {"id": "F1", "doc_id": "D1", "type": "product", "statement": "发布新品"},
            {"id": "F2", "doc_id": "D2", "type": "other", "statement": "例行披露"},
        ])

        result = optimizer.harvest(tmpdir / "cases", limit=10, cases_path=cases_path)
        assert result["count"] == 1, result
        assert result["skipped"]["routine"] == 1, result["skipped"]
        lines = pathlib.Path(result["path"]).read_text(encoding="utf-8").splitlines()
        item = json.loads(lines[0])
        assert item["expected"] == "", item
        assert item["title"] == "某某企业发布新型控制器", item
        assert item["split"] == "dev", item
        assert item["hint"]["predicted"] == "product", item
        assert item["hint"]["url"].startswith("https://"), item

        # 待标注样本 expected 为空，不能直接当评测集跑
        try:
            optimizer.load_cases(result["path"])
        except ValueError:
            pass
        else:
            raise AssertionError("待标注样本不应直接当作评测集")

        # 再采一次：同一标题已在上一份产物里，不应重复导出
        again = optimizer.harvest(tmpdir / "cases", limit=10, cases_path=result["path"])
        assert again["count"] == 0, again
        assert again["skipped"]["duplicate"] == 1, again["skipped"]
    finally:
        if getattr(store._local, "conn", None) is not None:
            store._local.conn.close()
        store._local.conn = old_conn
        store.DB_PATH = old_path
        store.DB_URL = old_url
        optstore.OPT_DIR = old_opt
        shutil.rmtree(tmpdir, ignore_errors=True)


@case("离线优化门禁：预测分布偏移拦截结构漂移")
def _optimizer_shift():
    base = {"per_label": {"order": {"predicted": 6}, "product": {"predicted": 4}}}
    same = {"per_label": {"order": {"predicted": 5}, "product": {"predicted": 5}}}
    wild = {"per_label": {"order": {"predicted": 1}, "product": {"predicted": 9}}}
    assert optimizer._label_shift(base, same, 24) == []
    moved = optimizer._label_shift(base, wild, 24)
    assert {row["label"] for row in moved} == {"order", "product"}, moved
    # 小评测集上允许条数不得降到 1，避免正常修正被误杀
    assert optimizer._label_shift(base, same, 12) == []


@case("离线召回评测：文档盲区导出与漏检/误报统计")
def _optimizer_recall():
    import json
    import shutil
    import tempfile

    from mingjian.infra import optstore

    tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="mingjian_recall_"))
    old_opt, old_path, old_url = optstore.OPT_DIR, store.DB_PATH, store.DB_URL
    old_conn = getattr(store._local, "conn", None)
    optstore.OPT_DIR = tmpdir / "opt"
    store.DB_PATH = tmpdir / "r.db"
    store.DB_URL = ""
    store._local.conn = None
    try:
        store.init()
        store.create_run("R1", "某某企业", "某某企业", {})
        long_body = "某某企业近日披露了相关经营进展，" + "正文内容。" * 30
        store.add_document({"id": "D1", "run_id": "R1", "entity_key": "某某企业",
                            "kind": "news", "title": "某某企业发布新型控制器",
                            "url": "https://example.invalid/a", "body": long_body,
                            "evidence_level": "full",
                            "extra": {"query": "某某企业 订单 合作 中标"}})
        store.add_document({"id": "D2", "run_id": "R1", "entity_key": "某某企业",
                            "kind": "announcement", "title": "某某企业翌日披露报表",
                            "url": "https://example.invalid/b", "body": long_body,
                            "evidence_level": "full"})
        store.add_document({"id": "D3", "run_id": "R1", "entity_key": "某某企业",
                            "kind": "news", "title": "某某企业签订大额订单",
                            "url": "https://example.invalid/c", "body": long_body,
                            "evidence_level": "full"})
        store.add_document({"id": "D4", "run_id": "R1", "entity_key": "某某企业",
                            "kind": "news", "title": "某某企业新品发布",
                            "url": "https://example.invalid/d", "body": "太短",
                            "evidence_level": "title"})
        store.save_facts("R1", "某某企业", [
            {"id": "F1", "doc_id": "D3", "type": "order", "statement": "签订订单"},
        ])

        result = optimizer.harvest_docs(tmpdir / "cases", limit=10)
        assert result["count"] == 1, result
        assert result["skipped"]["routine"] == 1, result["skipped"]
        assert result["skipped"]["no_body"] == 1, result["skipped"]
        item = json.loads(pathlib.Path(result["path"]).read_text(encoding="utf-8").splitlines()[0])
        assert item["id"] == "D0001", item
        assert item["expected"] == "", item
        assert item["hint"]["predicted"] == "none", item
        assert item["hint"]["fact_count"] == 0, item
        assert item["hint"]["names"] == ["某某企业"], item
        assert item["hint"]["query"] == "某某企业 订单 合作 中标", item

        # 待标注召回样本 expected 为空，不能直接当评测集跑
        try:
            optimizer.load_doc_cases(result["path"])
        except ValueError:
            pass
        else:
            raise AssertionError("待标注召回样本不应直接当作评测集")

        again = optimizer.harvest_docs(tmpdir / "cases", limit=10, cases_path=result["path"])
        assert again["count"] == 0, again
        assert again["skipped"]["duplicate"] == 1, again["skipped"]

        covered = optimizer.harvest_docs(tmpdir / "cases2", limit=10, include_covered=True)
        assert covered["count"] == 2, covered
        rows = [json.loads(line) for line in
                pathlib.Path(covered["path"]).read_text(encoding="utf-8").splitlines()]
        preds = {row["title"]: row["hint"]["predicted"] for row in rows}
        assert preds["某某企业签订大额订单"] == "fact", preds
        assert preds["某某企业发布新型控制器"] == "none", preds

        # 评测：R1 正确；R2 强信号但标 none → 误报；R3 主体名不符 → 漏检
        body = "某某企业近日与客户签订5亿元订单。" + "订单交付与验收按合同执行。" * 20
        cases = [
            {"id": "R1", "title": "某某企业签订5亿元订单", "body": body,
             "expected": "fact", "split": "dev",
             "hint": {"kind": "announcement", "evidence_level": "full",
                      "names": ["某某企业"], "url": "https://example.invalid/1"}},
            {"id": "R2", "title": "某某企业新增年产10万吨产能项目", "body": body,
             "expected": "none", "split": "dev",
             "hint": {"kind": "announcement", "evidence_level": "full",
                      "names": ["某某企业"], "url": "https://example.invalid/2"}},
            {"id": "R3", "title": "某某企业发布业绩预告",
             "body": "某某企业预计净利润同比增长。",
             "expected": "fact", "split": "dev",
             "hint": {"kind": "news", "names": ["甲公司"],
                      "url": "https://example.invalid/3"}},
        ]
        report = optimizer.recall(cases)
        assert report["cases"] == 3, report
        assert [row["id"] for row in report["missed"]] == ["R3"], report["missed"]
        assert [row["id"] for row in report["false_positive"]] == ["R2"], report["false_positive"]
        assert report["accuracy"] == 0.3333, report["accuracy"]
        assert report["min_conf"] > 0, report
        assert pathlib.Path(report["evaluation_file"]).name.startswith("recall-"), report
    finally:
        if getattr(store._local, "conn", None) is not None:
            store._local.conn.close()
        store._local.conn = old_conn
        store.DB_PATH = old_path
        store.DB_URL = old_url
        optstore.OPT_DIR = old_opt
        shutil.rmtree(tmpdir, ignore_errors=True)


@case("检索词版本：文件覆盖、非法回退与查询构造")
def _searchterms_version():
    import inspect
    import json
    import shutil
    import tempfile

    from mingjian.infra import optstore
    from mingjian.infra.sources import eastmoney
    from mingjian.services import searchterms, skills

    tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="mingjian_terms_"))
    old_opt = optstore.OPT_DIR
    optstore.OPT_DIR = tmpdir
    try:
        assert searchterms.DEFAULT_TERMS == eastmoney.DEFAULT_TERMS
        base = searchterms.load()
        assert base["version"] == "baseline", base
        assert base["terms"] == list(eastmoney.DEFAULT_TERMS), base

        path = tmpdir / "search_terms.json"
        payload = {"version": "terms-20261124-01",
                   "terms": ["扩产 产能", "固态电池", "扩产 产能"]}
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        loaded = searchterms.load()
        assert loaded["version"] == "terms-20261124-01", loaded
        assert loaded["terms"] == ["扩产 产能", "固态电池"], loaded
        assert searchterms.active_version() == "terms-20261124-01"
        qs = eastmoney.queries_for("某某企业", loaded["terms"])
        assert qs == ["某某企业", "某某企业 扩产 产能", "某某企业 固态电池"], qs
        assert eastmoney.queries_for("  ") == []

        for bad in ({"version": "../terms", "terms": ["扩产 产能", "固态电池"]},
                    {"version": "terms-bad-1", "terms": ["扩产 产能"]},
                    {"version": "terms-bad-2", "terms": ["扩产 产能", "x" * 41]}):
            path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
            assert searchterms.load()["version"] == "baseline", bad

        # 技能里的采集导入曾写成 from .sources，运行时才会报错；这里做静态防回归
        assert "from .sources" not in inspect.getsource(skills), \
            "技能里的采集导入必须指向 infra.sources"
    finally:
        optstore.OPT_DIR = old_opt
        shutil.rmtree(tmpdir, ignore_errors=True)


@case("采集留痕：每路统计、被拒条目与运行可回看")
def _collect_trace():
    import json
    import shutil
    import tempfile

    from mingjian.infra.sources import eastmoney
    from mingjian.services import optimizer

    # 1) news_bundle 留痕：替换网络检索，构造一入一异一重
    def fake_search(keyword, page=1, size=10, meta=None):
        if meta is not None:
            meta.update({"status": "ok", "error": "", "ms": 1, "raw": 3})
        if page > 1:
            return []
        return [
            {"kind": "news", "title": "某某企业扩产项目投产",
             "url": "https://example.invalid/1", "source": "测试源",
             "published_at": "2026-11-20", "body": "某某企业新增产线", "extra": {}},
            {"kind": "news", "title": "行业景气度回升",
             "url": "https://example.invalid/2", "source": "测试源",
             "published_at": "2026-11-20", "body": "行业整体回暖", "extra": {}},
            {"kind": "news", "title": "某某企业扩产项目投产",
             "url": "https://example.invalid/3", "source": "测试源",
             "published_at": "2026-11-20", "body": "", "extra": {}},
        ]

    old_search = eastmoney.search
    eastmoney.search = fake_search
    try:
        trace: dict = {}
        items = eastmoney.news_bundle("某某企业", "某某企业",
                                      terms=["扩产 产能"], trace=trace)
    finally:
        eastmoney.search = old_search
    assert [it["url"] for it in items] == ["https://example.invalid/1"], items
    stats = {q["query"]: q for q in trace["queries"]}
    first = stats["某某企业"]
    assert (first["raw"], first["kept"]) == (3, 1), first
    assert (first["duplicate"], first["irrelevant"]) == (1, 1), first
    assert first["status"] == "ok", first
    assert first["ms"] >= 0, first
    second = stats["某某企业 扩产 产能"]
    assert (second["raw"], second["kept"]) == (3, 0), second
    assert (second["duplicate"], second["irrelevant"]) == (2, 1), second
    assert second["status"] == "ok", second
    reasons = [row["reason"] for row in trace["rejected"]]
    assert reasons.count("duplicate") == 3, reasons
    assert reasons.count("irrelevant") == 2, reasons
    assert all(row["query"] and row["title"] for row in trace["rejected"])

    # 2) collect_report 读回：统计在库里，明细在 output/collect/
    tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="mingjian_collect_"))
    old_path, old_url = store.DB_PATH, store.DB_URL
    old_conn = getattr(store._local, "conn", None)
    old_out = optimizer.OUT_DIR
    store.DB_PATH = tmpdir / "c.db"
    store.DB_URL = ""
    store._local.conn = None
    optimizer.OUT_DIR = tmpdir / "out"
    try:
        store.init()
        store.create_run("R1", "某某企业", "某某企业", {})
        store.finish_run("R1", "done", {"collect": {"queries": trace["queries"],
                                                    "rejected": 2, "failed": 0,
                                                    "empty": 0, "log": "R1.jsonl"},
                                        "coverage": {"announcements": 2, "covered": 1,
                                                     "gap": 1, "ratio": 0.5,
                                                     "threshold": 0.4, "skipped": 0,
                                                     "log": "R1-gap.jsonl"}})
        log_dir = tmpdir / "out" / "collect"
        log_dir.mkdir(parents=True)
        lines = [json.dumps({"run_id": "R1", **row}, ensure_ascii=False)
                 for row in trace["rejected"][:2]]
        (log_dir / "R1.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        (log_dir / "R1-gap.jsonl").write_text(
            json.dumps({"run_id": "R1", "id": "D9", "title": "关于修订章程的公告",
                        "score": 0.12, "covered": False}, ensure_ascii=False) + "\n",
            encoding="utf-8")

        one = optimizer.collect_report("R1", limit=1)
        assert one["run_id"] == "R1", one
        assert len(one["queries"]) == 2, one
        assert one["rejected"] == 2, one
        assert (one["failed"], one["empty"]) == (0, 0), one
        assert one["log"] == "R1.jsonl", one
        assert len(one["rows"]) == 1, one
        assert one["rows"][0]["run_id"] == "R1", one["rows"]
        assert one["rows"][0]["reason"] in ("duplicate", "irrelevant"), one["rows"]
        assert one["coverage"]["covered"] == 1, one
        assert one["gap_log"] == "R1-gap.jsonl", one
        assert len(one["gap_rows"]) == 1, one
        assert one["gap_rows"][0]["id"] == "D9", one["gap_rows"]
        assert len(optimizer.collect_report("R1", limit=20)["rows"]) == 2

        # 旧版本运行没有 collect 统计：返回空而不是报错
        store.create_run("R2", "甲公司", "甲公司", {})
        empty = optimizer.collect_report("R2")
        assert empty["queries"] == [] and empty["rows"] == [], empty

        for bad in ("", "R404"):
            try:
                optimizer.collect_report(bad)
            except ValueError:
                pass
            else:
                raise AssertionError("无效运行编号应报错")
    finally:
        if getattr(store._local, "conn", None) is not None:
            store._local.conn.close()
        store._local.conn = old_conn
        store.DB_PATH = old_path
        store.DB_URL = old_url
        optimizer.OUT_DIR = old_out
        shutil.rmtree(tmpdir, ignore_errors=True)


@case("检索状态：成功、空结果与失败可区分")
def _search_status():
    from mingjian.core import net
    from mingjian.infra.sources import eastmoney

    class _Resp:
        def __init__(self, text):
            self.text = text

    ok_body = ('cb({"result":{"cmsArticleWebOld":[{"title":"<em>某某</em>企业扩产",'
               '"url":"https://example.invalid/a","mediaName":"测试源",'
               '"date":"2026-11-20 10:00:00","content":"正文","code":"1"}]}})')
    old_fetch = net.fetch
    try:
        net.fetch = lambda url, headers=None: _Resp(ok_body)
        meta: dict = {}
        items = eastmoney.search("某某企业", meta=meta)
        assert len(items) == 1 and items[0]["title"] == "某某企业扩产", items
        assert meta["status"] == "ok", meta
        assert meta["raw"] == 1 and meta["ms"] >= 0 and not meta["error"], meta

        net.fetch = lambda url, headers=None: _Resp('cb({"result":{"cmsArticleWebOld":[]}})')
        meta = {}
        assert eastmoney.search("某某企业", meta=meta) == []
        assert meta["status"] == "empty", meta

        # result 空对象也是“确实没结果”，不能误报成上游风控
        net.fetch = lambda url, headers=None: _Resp('cb({"result":{}})')
        meta = {}
        assert eastmoney.search("某某企业", meta=meta) == []
        assert meta["status"] == "empty", meta

        # 上游回调名会从 cb 换成 jQuery3510..._1693...，不能只认 cb(
        jq_body = ('jQuery35108723733748578402_1693632913001({"result":'
                   '{"cmsArticleWebOld":[{"title":"某某企业获订单",'
                   '"url":"https://example.invalid/jq","mediaName":"测试源",'
                   '"date":"2026-11-21 09:00:00","content":"正文"}]}});')
        net.fetch = lambda url, headers=None: _Resp(jq_body)
        meta = {}
        items = eastmoney.search("某某企业", meta=meta)
        assert len(items) == 1 and items[0]["title"] == "某某企业获订单", items
        assert meta["status"] == "ok", meta

        # 风控时 HTTP 仍是 200，但 result 只有 passportWeb，没有文章列表
        risk_body = 'jQuery1({"result":{"passportWeb":{"uid":"x"}}})'
        net.fetch = lambda url, headers=None: _Resp(risk_body)
        meta = {}
        assert eastmoney.search("某某企业", meta=meta) == []
        assert meta["status"] == "error" and "风控" in meta["error"], meta

        net.fetch = lambda url, headers=None: _Resp('cb({"result":{"foo":1}})')
        meta = {}
        assert eastmoney.search("某某企业", meta=meta) == []
        assert meta["status"] == "error" and "缺少文章列表" in meta["error"], meta

        net.fetch = lambda url, headers=None: _Resp("oops")
        meta = {}
        assert eastmoney.search("某某企业", meta=meta) == []
        assert meta["status"] == "error" and "JSONP" in meta["error"], meta

        def boom(url, headers=None):
            raise net.FetchError(url, "HTTP 403", 403)
        net.fetch = boom
        meta = {}
        assert eastmoney.search("某某企业", meta=meta) == []
        assert meta["status"] == "error" and "403" in meta["error"], meta
    finally:
        net.fetch = old_fetch

    # news_bundle 逐路收敛状态：一路失败不影响其余路的结果
    def partial_search(keyword, page=1, size=10, meta=None):
        if "扩产" in keyword:
            if meta is not None:
                meta.update({"status": "error", "error": "FetchError 503",
                             "ms": 3, "raw": 0})
            return []
        if meta is not None:
            meta.update({"status": "ok", "error": "", "ms": 2, "raw": 1})
        return [{"kind": "news", "title": "某某企业发布新品",
                 "url": "https://example.invalid/ok", "source": "测试源",
                 "published_at": "2026-11-20", "body": "某某企业新品", "extra": {}}]

    def empty_search(keyword, page=1, size=10, meta=None):
        if meta is not None:
            meta.update({"status": "empty", "error": "", "ms": 1, "raw": 0})
        return []

    old_search = eastmoney.search
    from mingjian.infra.sources import baidu
    old_backup = baidu.search

    def no_backup(keyword, page=1, size=10, meta=None):
        if meta is not None:
            meta.update({"status": "error", "error": "备用源不可用",
                         "ms": 0, "raw": 0})
        return []

    try:
        baidu.search = no_backup
        eastmoney.search = partial_search
        trace: dict = {}
        items = eastmoney.news_bundle("某某企业", "某某企业",
                                      terms=["扩产 产能"], trace=trace)
        assert [it["url"] for it in items] == ["https://example.invalid/ok"], items
        failed = [q for q in trace["queries"] if q["status"] == "error"]
        assert len(failed) == 1 and "503" in failed[0]["error"], trace["queries"]
        assert failed[0]["ms"] == 3, failed[0]

        eastmoney.search = empty_search
        trace = {}
        eastmoney.news_bundle("某某企业", "某某企业", terms=["扩产 产能"], trace=trace)
        assert all(q["status"] == "empty" for q in trace["queries"]), trace
    finally:
        eastmoney.search = old_search
        baidu.search = old_backup


@case("检索回退：主源风控时走备用源并留痕")
def _search_fallback():
    from mingjian.infra.sources import baidu, eastmoney

    old_search, old_backup = eastmoney.search, baidu.search

    def risk(keyword, page=1, size=10, meta=None):
        if meta is not None:
            meta.update({"status": "error", "error": "上游风控：仅返回股民资料",
                         "ms": 3, "raw": 0})
        return []

    def backup(keyword, page=1, size=10, meta=None):
        if meta is not None:
            meta.update({"status": "ok", "error": "", "ms": 7, "raw": 1})
        return [{"kind": "news", "title": "某某企业获订单",
                 "url": "https://example.invalid/b", "source": "备用源",
                 "published_at": "2026-11-22", "body": "某某企业获订单",
                 "extra": {}}]

    try:
        eastmoney.search, baidu.search = risk, backup
        meta: dict = {}
        items = eastmoney.search_any("某某企业", meta=meta)
        assert [it["url"] for it in items] == ["https://example.invalid/b"], items
        assert meta["via"] == "baidu" and "风控" in meta["fallback_from"], meta
        assert items[0]["extra"]["via"] == "baidu", items

        trace: dict = {}
        items = eastmoney.news_bundle("某某企业", "某某企业",
                                      terms=["扩产 产能"], trace=trace)
        assert [it["url"] for it in items] == ["https://example.invalid/b"], items
        assert all(q["via"] == "baidu" for q in trace["queries"]), trace

        # 主源正常返回空结果时不回退，避免同一查询在不同源之间悄悄换口径
        def empty(keyword, page=1, size=10, meta=None):
            if meta is not None:
                meta.update({"status": "empty", "error": "", "ms": 1, "raw": 0})
            return []

        eastmoney.search = empty
        called: list[int] = []
        baidu.search = lambda *a, **k: called.append(1) or []
        assert eastmoney.search_any("某某企业") == []
        assert not called, "主源空结果不应触发回退"
    finally:
        eastmoney.search, baidu.search = old_search, old_backup


@case("百度资讯：首屏解析、日期归一与失败区分")
def _baidu_search():
    import re
    from datetime import datetime

    from mingjian.core import net
    from mingjian.infra.sources import baidu

    class _Resp:
        def __init__(self, text):
            self.text = text

    html = (
        '<h3 class="news-title_x"><a href="https://example.invalid/a?x=1&amp;y=2" '
        'aria-label="标题：甲公司发布新品">甲公司发布新品</a></h3>'
        '<div class="c-row"><span aria-label="发布于：昨天22:11">昨天22:11</span>'
        '<span aria-label="摘要 甲公司发布新品，正文摘要 摘要结束，点击查看详情">'
        '甲公司发布新品</span>'
        '<span aria-label="新闻来源：测试媒体">测试媒体</span></div>'
        '<h3 class="news-title_x"><a href="https://example.invalid/b" '
        'aria-label="标题：乙公司中标">乙公司中标</a></h3>'
        '<div><span aria-label="发布于：2026年11月20日">2026年11月20日</span>'
        '<span aria-label="新闻来源：另一媒体">另一媒体</span></div>'
    )
    old_fetch = net.fetch
    baidu._cache.clear()
    try:
        net.fetch = lambda url, headers=None: _Resp(html)
        meta: dict = {}
        items = baidu.search("甲公司回退测试", meta=meta)
        assert len(items) == 2, items
        assert items[0]["url"] == "https://example.invalid/a?x=1&y=2", items
        assert items[0]["title"] == "甲公司发布新品", items
        assert items[0]["source"] == "测试媒体", items
        assert "正文摘要" in items[0]["body"], items
        assert re.match(r"^\d{4}-\d{2}-\d{2} 22:11$", items[0]["published_at"]), items
        assert items[1]["published_at"] == "2026-11-20", items
        assert meta["via"] == "baidu" and meta["status"] == "ok", meta
        now = datetime(2026, 11, 25, 10, 0)
        assert baidu._date("昨天", now=now) == "2026-11-24", baidu._date("昨天", now=now)
        assert baidu._date("3天前", now=now) == "2026-11-22", baidu._date("3天前", now=now)

        meta = {}
        assert baidu.search("甲公司回退测试", page=2, meta=meta) == []
        assert meta["status"] == "empty" and "首屏" in meta["error"], meta

        baidu._cache.clear()
        net.fetch = lambda url, headers=None: _Resp("<html>百度安全验证</html>")
        meta = {}
        assert baidu.search("另一关键词", meta=meta) == []
        assert meta["status"] == "error" and "验证" in meta["error"], meta

        net.fetch = lambda url, headers=None: _Resp("<html>没有找到相关新闻</html>")
        meta = {}
        assert baidu.search("第三关键词", meta=meta) == []
        assert meta["status"] == "empty", meta

        net.fetch = lambda url, headers=None: _Resp("<html>结构变了</html>")
        meta = {}
        assert baidu.search("第四关键词", meta=meta) == []
        assert meta["status"] == "error" and "结构" in meta["error"], meta
    finally:
        net.fetch = old_fetch
        baidu._cache.clear()


@case("实体搜索：无匹配时保留按输入名称建档")
def _entity_manual():
    from mingjian.api.routes import entities
    from mingjian.infra.sources import cninfo

    old_listed, old_clues = cninfo.search, discover.clues
    q = "某某未收录企业"
    try:
        cninfo.search = lambda kw, limit: []
        discover.clues = lambda kw, limit: []
        out = entities.entity_search(q, limit=3)["items"]
        assert out and out[-1]["source"] == "manual" and out[-1]["name"] == q, out

        cninfo.search = lambda kw, limit: [{"name": q, "code": "000001",
                                            "market": "主板"}]
        out = entities.entity_search(q, limit=3)["items"]
        assert out and out[0]["source"] == "listed", out
        assert not any(x["source"] == "manual" for x in out), out

        cninfo.search = lambda kw, limit: []
        discover.clues = lambda kw, limit: [
            {"name": f"资讯企业{i}", "hint": "测试"} for i in range(6)]
        out = entities.entity_search(q, limit=3)["items"]
        assert len(out) == 3 and out[-1]["source"] == "manual", out
    finally:
        cninfo.search, discover.clues = old_listed, old_clues


@case("公告覆盖：命中、缺口与分数排序")
def _coverage_gap():
    from mingjian.services import coverage

    anns = [
        {"id": "A1", "kind": "announcement", "title": "关于回购股份的公告",
         "published_at": "2026-11-20", "url": "https://example.invalid/a1"},
        {"id": "A2", "kind": "announcement", "title": "关于签订重大合同的公告",
         "published_at": "2026-11-20", "url": "https://example.invalid/a2"},
        {"id": "A3", "kind": "announcement", "title": "关于修订公司章程的公告",
         "published_at": "2026-11-20", "url": "https://example.invalid/a3"},
        {"id": "A4", "kind": "announcement", "title": "公告",
         "published_at": "2026-11-20", "url": "https://example.invalid/a4"},
    ]
    news = [
        {"id": "N1", "kind": "news", "title": "某某企业拟回购股份",
         "body": "某某企业今日披露回购方案", "url": "https://example.invalid/n1"},
        {"id": "N2", "kind": "news", "title": "某某企业签订5亿元合同",
         "body": "合同金额5亿元，工期两年", "url": "https://example.invalid/n2"},
    ]
    report = coverage.announcement_gap(anns, news)
    assert report["announcements"] == 3, report   # A4 归一后太短，不参与
    assert report["skipped"] == 1, report
    assert report["covered"] == 2, report
    assert report["gap"] == 1, report
    assert 0 < report["ratio"] < 1, report
    by_id = {r["id"]: r for r in report["rows"]}
    assert by_id["A1"]["covered"] is True, by_id["A1"]
    assert by_id["A1"]["best"]["title"] == "某某企业拟回购股份", by_id["A1"]
    assert by_id["A2"]["covered"] is True, by_id["A2"]
    assert by_id["A3"]["covered"] is False, by_id["A3"]
    assert by_id["A3"]["best"] == {}, by_id["A3"]
    assert report["rows"][0]["id"] == "A3", report["rows"]   # 升序，缺口在前

    empty = coverage.announcement_gap([], news)
    assert empty["announcements"] == 0 and empty["ratio"] == 0.0, empty
    no_news = coverage.announcement_gap(anns[:1], [])
    assert no_news["covered"] == 0 and no_news["gap"] == 1, no_news


@case("深度对照：两次运行的候选、新增文档与事实")
def _optimizer_depth():
    import shutil
    import tempfile

    from mingjian.infra import optstore
    from mingjian.services import optimizer

    tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="mingjian_depth_"))
    old_opt, old_path, old_url = optstore.OPT_DIR, store.DB_PATH, store.DB_URL
    old_conn = getattr(store._local, "conn", None)
    optstore.OPT_DIR = tmpdir / "opt"
    store.DB_PATH = tmpdir / "d.db"
    store.DB_URL = ""
    store._local.conn = None
    try:
        store.init()
        store.create_run("R1", "某某企业", "某某企业",
                         {"news_pages": 3, "search_terms_version": "baseline"})
        store.finish_run("R1", "done", {"collect": {"queries": [
            {"query": "某某企业", "raw": 10, "kept": 2, "ms": 100, "status": "ok"},
            {"query": "某某企业 扩产 产能", "raw": 10, "kept": 1, "ms": 120,
             "status": "ok"}]}})
        store.add_document({"id": "D1", "run_id": "R1", "entity_key": "某某企业",
                            "kind": "news", "title": "某某企业扩产",
                            "url": "https://example.invalid/u1"})
        store.add_document({"id": "D2", "run_id": "R1", "entity_key": "某某企业",
                            "kind": "news", "title": "某某企业签约",
                            "url": "https://example.invalid/u2"})

        store.create_run("R2", "某某企业", "某某企业",
                         {"news_pages": 6, "search_terms_version": "baseline"})
        store.finish_run("R2", "done", {"collect": {"queries": [
            {"query": "某某企业", "raw": 20, "kept": 5, "ms": 200, "status": "ok"},
            {"query": "某某企业 扩产 产能", "raw": 20, "kept": 3, "ms": 210,
             "status": "ok"}]}})
        store.add_document({"id": "D3", "run_id": "R2", "entity_key": "某某企业",
                            "kind": "news", "title": "某某企业扩产",
                            "url": "https://example.invalid/u1"})
        store.add_document({"id": "D4", "run_id": "R2", "entity_key": "某某企业",
                            "kind": "news", "title": "某某企业新订单",
                            "url": "https://example.invalid/u3"})
        store.add_document({"id": "D5", "run_id": "R2", "entity_key": "某某企业",
                            "kind": "news", "title": "某某企业新产线",
                            "url": "https://example.invalid/u4"})
        store.save_facts("R2", "某某企业", [
            {"id": "F1", "doc_id": "D4", "type": "order", "statement": "新增订单"},
        ])

        report = optimizer.depth_report("R1", "R2")
        assert report["base"]["pages"] == 3 and report["cand"]["pages"] == 6, report
        assert report["candidates"] == {"base": 20, "cand": 40, "delta": 20}, report
        assert report["kept"] == {"base": 3, "cand": 8, "delta": 5}, report
        assert report["docs"] == {"base": 2, "cand": 3, "new": 2, "overlap": 1}, report
        assert report["new_docs_with_fact"] == 1, report
        assert report["ms"] == {"base": 220, "cand": 410}, report
        assert report["terms_changed"] is False, report
        assert report["per_query"][0]["query"] == "某某企业", report["per_query"]
        assert pathlib.Path(report["report_file"]).name.startswith("depth-"), report

        for bad in (("R1", "R1"), ("R1", "R404")):
            try:
                optimizer.depth_report(*bad)
            except ValueError:
                pass
            else:
                raise AssertionError("无效对照应报错")
        store.create_run("R3", "甲公司", "甲公司", {})
        try:
            optimizer.depth_report("R1", "R3")
        except ValueError:
            pass
        else:
            raise AssertionError("不同公司不应允许对照")
    finally:
        if getattr(store._local, "conn", None) is not None:
            store._local.conn.close()
        store._local.conn = old_conn
        store.DB_PATH, store.DB_URL = old_path, old_url
        optstore.OPT_DIR = old_opt
        shutil.rmtree(tmpdir, ignore_errors=True)


@case("模型配置：旧 config.json 首次读取时迁入数据库")
def _llm_migrate():
    import json
    import shutil
    import tempfile

    tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="mingjian_mig_"))
    old_path, old_url, old_conn = store.DB_PATH, store.DB_URL, getattr(store._local, "conn", None)
    old_conf = llmcfg.CONFIG_PATH
    conf = tmpdir / "config.json"
    store.DB_PATH = tmpdir / "t.db"
    store.DB_URL = ""
    store._local.conn = None
    llmcfg.CONFIG_PATH = conf
    try:
        conf.write_text(json.dumps({"llm_base": "https://legacy.invalid/v1",
                                    "llm_key": "sk-legacy-9999", "llm_model": "legacy-model",
                                    "llm_timeout": 60}), encoding="utf-8")
        shown = llmcfg.public()
        assert shown["profiles"][0]["model"] == "legacy-model", shown
        assert shown["profiles"][0]["key_hint"].endswith("9999"), shown
        assert store.get_setting("llm")["profiles"][0]["key"] == "sk-legacy-9999"

        # 迁入之后以库为准：再改 config.json 不影响页面
        conf.write_text(json.dumps({"llm_base": "https://other.invalid/v1",
                                    "llm_key": "sk-other-0000",
                                    "llm_model": "other-model"}), encoding="utf-8")
        assert llmcfg.public()["profiles"][0]["model"] == "legacy-model"
    finally:
        if getattr(store._local, "conn", None) is not None:
            store._local.conn.close()
        store._local.conn = old_conn
        store.DB_PATH = old_path
        store.DB_URL = old_url
        llmcfg.CONFIG_PATH = old_conf
        shutil.rmtree(tmpdir, ignore_errors=True)


@case("工商接口：配置落库、响应归一化与错误区分")
def _registry_basic():
    import shutil
    import tempfile
    import types

    from mingjian.infra import registry

    tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="mingjian_reg_"))
    old_path, old_url, old_conn = store.DB_PATH, store.DB_URL, getattr(store._local, "conn", None)
    old_fetch = registry.fetch
    store.DB_PATH, store.DB_URL, store._local.conn = tmpdir / "t.db", "", None
    registry._cache.clear()
    calls = []
    row = {"name": "某某智能装备有限公司", "creditCode": "91320100MA1XXXX01X",
           "legalPersonName": "张三", "regCapital": "5000万人民币", "regStatus": "存续",
           "estiblishTime": 1577836800000, "regLocation": "某省某市某区",
           "industry": "专用设备制造业", "businessScope": "智能装备研发、制造",
           "website": "www.example.com"}
    payload = {"error_code": 0, "reason": "ok", "result": {"items": [row]}}
    mapping = {
        "name": "name", "credit_code": "creditCode", "legal_person": "legalPersonName",
        "reg_capital": "regCapital", "reg_status": "regStatus",
        "establish_date": "estiblishTime", "location": "regLocation",
        "industry": "industry", "business": "businessScope", "website": "website",
    }

    def fake(url, **kw):
        calls.append(url)
        return types.SimpleNamespace(json=lambda: payload)

    registry.fetch = fake
    try:
        out = registry.save_profile({"preset": "tianyancha", "name": "测试接口",
                                    "key": "tk-1234", "map": mapping})
        assert out["enabled"] and out["active_id"], out
        assert out["profiles"][0]["key_hint"] == "…1234", out
        assert "key" not in out["profiles"][0], out          # 密钥不回传页面
        assert store.get_setting("registry")["profiles"][0]["key"] == "tk-1234"

        meta: dict = {}
        items = registry.search("某某", limit=5, meta=meta)
        assert meta["status"] == "ok" and meta["vendor"] == "tianyancha", meta
        assert items and items[0]["credit_code"] == "91320100MA1XXXX01X", items
        assert items[0]["establish_date"] == "2020-01-01", items[0]
        assert items[0]["legal_person"] == "张三", items[0]
        assert items[0]["website"] == "www.example.com", items[0]
        assert "pageSize=5" in calls[0] and "word=" in calls[0], calls[0]

        # 已缓存：同一关键词不再消耗接口额度
        n = len(calls)
        assert registry.search("某某", limit=5), "缓存应命中"
        assert len(calls) == n, calls
        assert store.get_company("某某智能装备有限公司")["industry"] == "专用设备制造业"
        assert store.get_company("某某智能装备有限公司")["website"] == "www.example.com"

        registry._cache.clear()
        payload.update({"error_code": 300002, "reason": "账号失效"})
        meta = {}
        registry.search("某某", limit=5, meta=meta)
        assert meta["status"] == "error" and "账号失效" in meta["error"], meta

        registry._cache.clear()
        payload.clear()
        meta = {}
        registry.search("某某", limit=5, meta=meta)
        assert meta["status"] == "error" and "结果路径" in meta["error"], meta

        out = registry.clear()
        assert not out["enabled"], out
        meta = {}
        assert registry.search("某某", meta=meta) == [] and meta["status"] == "disabled", meta
    finally:
        registry.fetch = old_fetch
        registry._cache.clear()
        if getattr(store._local, "conn", None) is not None:
            store._local.conn.close()
        store._local.conn = old_conn
        store.DB_PATH, store.DB_URL = old_path, old_url
        shutil.rmtree(tmpdir, ignore_errors=True)


@case("实体搜索：工商候选参与合并、去重与降级提示")
def _entity_registry():
    import shutil
    import tempfile

    from mingjian.api.routes import entities
    from mingjian.infra import registry

    tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="mingjian_ent_") )
    old_path, old_url, old_conn = store.DB_PATH, store.DB_URL, getattr(store._local, "conn", None)
    old_listed, old_clues, old_search = cninfo.search, discover.clues, registry.search
    old_enabled, old_local = registry.enabled, entities._local_matches
    store.DB_PATH, store.DB_URL, store._local.conn = tmpdir / "t.db", "", None
    try:
        store.init()
        entities._local_matches = lambda q, n: []
        cninfo.search = lambda kw, limit: []
        discover.clues = lambda kw, limit: []
        registry.enabled = lambda: True

        def fake_search(kw, limit=8, meta=None):
            meta.update({"status": "ok", "vendor": "tianyancha", "ms": 5,
                         "total": 1, "error": ""})
            return [{"name": "某某新材料有限公司", "credit_code": "91320100MA000001X",
                     "reg_status": "存续", "reg_capital": "1000万",
                     "establish_date": "2021-03-01", "source": "registry:tianyancha"}]

        registry.search = fake_search
        out = entities.entity_search("某某新材料", limit=8)
        reg = [x for x in out["items"] if x["source"] == "registry"]
        assert reg and reg[0]["credit_code"].endswith("001X"), out
        assert reg[0]["hint"] == "存续 · 1000万", reg[0]

        # 证券库已命中时，同一主体不重复给工商候选
        cninfo.search = lambda kw, limit: [{"name": "某某新材料有限公司",
                                           "code": "000001", "market": "主板"}]
        out = entities.entity_search("某某新材料", limit=8)
        assert [x["source"] for x in out["items"]].count("listed") == 1, out
        assert not any(x["source"] == "registry" for x in out["items"]), out

        # 上游失败：保留其它候选，并把原因带回页面
        cninfo.search = lambda kw, limit: []

        def broken(kw, limit=8, meta=None):
            meta.update({"status": "error", "vendor": "tianyancha", "ms": 9,
                         "total": 0, "error": "网络失败：超时"})
            return []

        registry.search = broken
        out = entities.entity_search("某某新材料", limit=8)
        assert out["notes"] and "工商接口" in out["notes"][0], out
        assert any(x["source"] == "manual" for x in out["items"]), out
    finally:
        cninfo.search, discover.clues, registry.search = old_listed, old_clues, old_search
        registry.enabled, entities._local_matches = old_enabled, old_local
        if getattr(store._local, "conn", None) is not None:
            store._local.conn.close()
        store._local.conn = old_conn
        store.DB_PATH, store.DB_URL = old_path, old_url
        shutil.rmtree(tmpdir, ignore_errors=True)


@case("公司档案：非上市主体用工商登记补全")
def _entity_profile_registry():
    from mingjian.api.routes import entities
    from mingjian.infra import registry

    old_resolve, old_listed = registry.resolve, cninfo.resolve
    old_sub, old_cache = subject.get, dict(entities.profile_cache)
    registry.resolve = lambda name: {
        "name": "某某精工科技有限公司", "credit_code": "91320100MA000002X",
        "industry": "通用设备制造业", "location": "某省某市",
        "reg_status": "存续", "reg_capital": "800万",
        "establish_date": "2019-06-01", "business": "精密零部件制造",
        "website": "www.example.com",
    }
    cninfo.resolve = lambda name: {}
    subject.get = lambda key: {}
    entities.profile_cache.clear()
    try:
        out = entities.entity_profile("某某精工")
        assert out["listed"] is False and out["credit_code"].endswith("002X"), out
        assert out["industry"] == "通用设备制造业", out
        assert out["region"] == "某省某市" and out["reg_status"] == "存续", out
        assert out["website"] == "www.example.com", out
        assert "工商登记" in out["source"], out
    finally:
        registry.resolve, cninfo.resolve, subject.get = old_resolve, old_listed, old_sub
        entities.profile_cache.clear()
        entities.profile_cache.update(old_cache)


@case("数据库：旧 companies 表自动补官网列")
def _company_patch_columns():
    import shutil
    import sqlite3
    import tempfile

    tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="mingjian_mig_"))
    dbp = tmpdir / "old.db"
    raw = sqlite3.connect(dbp)
    raw.execute("""CREATE TABLE companies (
        key TEXT PRIMARY KEY, name TEXT, credit_code TEXT, legal_person TEXT,
        reg_capital TEXT, reg_status TEXT, establish_date TEXT, location TEXT,
        industry TEXT, business TEXT, source TEXT, raw TEXT, fetched_at TEXT)""")
    raw.commit()
    raw.close()
    old_path, old_url = store.DB_PATH, store.DB_URL
    old_conn, old_ready = getattr(store._local, "conn", None), store._ready
    store.DB_PATH, store.DB_URL, store._local.conn = dbp, "", None
    store._ready = None
    try:
        store.init()
        cols = [r["name"] for r in
                store.conn().execute("PRAGMA table_info(companies)").fetchall()]
        assert "website" in cols, cols
        store.upsert_company({"key": "某公司", "name": "某公司", "website": "example.com"})
        assert store.get_company("某公司")["website"] == "example.com"
    finally:
        if getattr(store._local, "conn", None) is not None:
            store._local.conn.close()
        store._local.conn, store._ready = old_conn, old_ready
        store.DB_PATH, store.DB_URL = old_path, old_url
        shutil.rmtree(tmpdir, ignore_errors=True)


def main() -> int:
    ok = fail = 0
    for name, fn in CASES:
        try:
            fn()
            print(f"  PASS  {name}")
            ok += 1
        except AssertionError as e:
            print(f"  FAIL  {name}\n        {e}")
            fail += 1
        except Exception as e:  # noqa: BLE001
            print(f"  ERR   {name}\n        {type(e).__name__}: {e}")
            fail += 1
    print()
    print(f"{ok}/{len(CASES)} 通过，{fail} 失败")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
