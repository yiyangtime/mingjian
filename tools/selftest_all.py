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

纯函数为主，不依赖网络；联网链路由 API 冒烟另行验证。
"""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mingjian.core import context  # noqa: E402
from mingjian.infra import scheduler, store  # noqa: E402
from mingjian.infra.sources import cninfo  # noqa: E402
from mingjian.runtime import compare  # noqa: E402
from mingjian.services import discover, extract, govpolicy, memory, metrics, skills, subject, topic  # noqa: E402

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
    old_path, old_conn = store.DB_PATH, getattr(store._local, "conn", None)
    store.DB_PATH = tmpdir / "t.db"
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
        shutil.rmtree(tmpdir, ignore_errors=True)


@case("记忆沉淀：从运行事实生成带引用的记忆")
def _memory_harvest():
    import shutil
    import tempfile
    tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="mingjian_hv_"))
    old_path, old_conn = store.DB_PATH, getattr(store._local, "conn", None)
    store.DB_PATH = tmpdir / "t.db"
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
    old_path, old_conn = store.DB_PATH, getattr(store._local, "conn", None)
    store.DB_PATH = tmpdir / "t.db"
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
        shutil.rmtree(tmpdir, ignore_errors=True)


@case("服务事件推送：落库后能重建步骤条")
def _server_events():
    import shutil
    import tempfile

    from mingjian import server

    tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="mingjian_srv_"))
    old_path, old_conn = store.DB_PATH, getattr(store._local, "conn", None)
    store.DB_PATH = tmpdir / "t.db"
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
