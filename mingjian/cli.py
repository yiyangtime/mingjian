"""
命令行入口。

    python -m mingjian run 某某科技
    python -m mingjian run 某某精密 --days 60 --fetch 16
    python -m mingjian resume R20261118...
    python -m mingjian ls
    python -m mingjian show R20261118...
    python -m mingjian sched --company 某某科技 --daily --time 09:00
    python -m mingjian serve --port 8765

输出同时落盘到 output/，报告可直接发给业务方。
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from .config import CFG, OUT_DIR
from .infra import store
from .runtime import pipeline


def _mk_event_printer():
    def ev(e: dict) -> None:
        t = e.get("type")
        if t == "step":
            if e["status"] == "start":
                sys.stdout.write(f"[{e['idx']}/7] {e['name']} … ")
                sys.stdout.flush()
            else:
                sys.stdout.write(f"{e['detail']}\n")
                sys.stdout.flush()
        elif t == "log":
            lv = e.get("level")
            if lv in ("warn", "error"):
                sys.stdout.write(f"      ! {e['msg']}\n")
            elif lv == "ok":
                sys.stdout.write(f"      ✓ {e['msg']}\n")
            sys.stdout.flush()
    return ev


def cmd_run(args) -> int:
    cfg = CFG
    cfg.days = args.days
    cfg.max_fetch = args.fetch
    if args.news:
        cfg.max_news = args.news
    if args.ann:
        cfg.max_ann = args.ann

    print(f"\n明鉴 · {args.company}（近 {cfg.days} 天）")
    print("=" * 62)
    res = pipeline.run(args.company, cfg=cfg, on_event=_mk_event_printer())

    # 报告与事实表已由 pipeline 第 7 步统一落盘，这里只取路径用于展示
    md_path = OUT_DIR / f"{args.company}_{res['run_id']}.md"
    csv_path = OUT_DIR / f"{args.company}_{res['run_id']}.csv"

    s = res["stats"]
    print("=" * 62)
    print(f"材料 {s['docs']} 份（公告 {s['announcements']} / 资讯 {s['news']}），正文 {s['full_text']} 份")
    print(f"事实 {s['facts']} 条，其中高置信 {s['high_conf']} 条，全部可回溯")
    print(f"用时 {s['elapsed_sec']} 秒\n")
    print(f"报告：{md_path}")
    print(f"事实表：{csv_path}\n")

    if args.print_all:
        print(res["report"])
    else:
        head = res["report"].split("## 三、")[0]
        print(head)
        print(f"……（完整报告见文件，共 {len(res['report'])} 字）")
    return 0


def cmd_resume(args) -> int:
    """从最近检查点续跑：失败/取消后不把已经采集、抽取过的部分重做一遍。"""
    run = store.get_run(args.run_id)
    if not run:
        print(f"找不到运行 {args.run_id}")
        return 1
    status = run.get("status")
    if status not in ("failed", "cancelled"):
        print(f"该运行状态为 {status}，只有 failed / cancelled 可以续跑")
        return 1
    if str(args.run_id).startswith("C"):
        print("竞品周报暂不支持续跑，请重新发起一次。")
        return 1

    try:
        params = json.loads(run.get("params") or "{}")
    except Exception:
        params = {}
    cfg = CFG
    for k in ("days", "max_news", "max_ann", "max_fetch"):
        if params.get(k) is not None:
            try:
                setattr(cfg, k, int(params[k]))
            except Exception:
                pass

    cp = store.last_checkpoint(args.run_id)
    print(f"\n明鉴 · 续跑 {run['company']}（{args.run_id}）")
    if cp:
        print(f"从第 {cp['step']} 步检查点继续，已完成步骤不重跑")
    else:
        print("没有可用检查点，按完整流程重跑（保留原事件日志）")
    print("=" * 62)
    res = pipeline.run(run["company"], cfg=cfg, on_event=_mk_event_printer(),
                       run_id=args.run_id, resume=True)
    if args.print_all:
        print(res["report"])
    else:
        print(res["report"].split("## 三、")[0])
        print(f"……（完整报告见文件，共 {len(res['report'])} 字）")
    print(f"报告：{OUT_DIR / (run['company'] + '_' + args.run_id + '.md')}\n")
    return 0


def cmd_ls(args) -> int:
    rows = store.list_runs(args.limit)
    if not rows:
        print("暂无运行记录。先执行：python -m mingjian run 某某科技")
        return 0
    print(f"{'运行编号':<24}{'公司':<12}{'状态':<8}{'开始时间':<21}")
    print("-" * 66)
    for r in rows:
        stats = json.loads(r.get("stats") or "{}")
        n = stats.get("facts", "-")
        print(f"{r['id']:<24}{r['company']:<12}{r['status']:<8}{r['started_at']:<21}事实 {n}")
    return 0


def cmd_show(args) -> int:
    run = store.get_run(args.run_id)
    if not run:
        print(f"找不到运行 {args.run_id}")
        return 1
    facts = store.facts_of_run(args.run_id)
    print(f"{run['company']} · {run['started_at']} · {run['status']}")
    print(f"统计：{run['stats']}\n")
    for f in facts[: args.limit]:
        print(f"[{f['id'].split('-')[-1]}] {f['type_label']} {f['confidence']:.2f}  {f['statement']}")
    if len(facts) > args.limit:
        print(f"…… 共 {len(facts)} 条")
    return 0


def cmd_gov(args) -> int:
    """按产业取政策：公司 → 行业/注册地 → 工信部 + 省厅官网 → 抽字段。"""
    from .infra.sources import cninfo, eastmoney
    from .services import govpolicy

    company = args.company.strip()
    ent = cninfo.resolve(company) or {}
    code, market = ent.get("code", ""), ent.get("market", "")
    prof = eastmoney.profile(code, market) if code else {}
    industry = prof.get("industry", "")
    print(f"\n明鉴 · 产业政策取数（{company}）")
    print("=" * 70)
    if prof:
        print(f"主体：{prof.get('full_name') or company}｜行业：{industry or '—'}｜"
              f"注册地：{prof.get('address') or '—'}")

    res = govpolicy.collect(
        company, industry=industry, main_business=prof.get("main_business", ""),
        address=prof.get("address", ""), province=args.province,
        max_central=args.central, max_local=args.local,
        keywords=args.keyword or None,
        on_event=lambda e: print("  " + e["msg"]) if e.get("type") == "log" else None,
    )
    docs = res["docs"]
    if not docs:
        print("未取到政策公告。可在 materials/ 放文件后走 policy，或指定 --province / --keyword 重试。")
        return 0

    head = ["编号", "层级", "来源", "标题", "状态", "置信度", "发文机关", "文号",
            "发布日期", "适用地区", "支持标准", "申报截止"]
    rows = [head]
    for d in docs:
        f = d["f"]
        rows.append([d["id"], d.get("level", ""), d.get("source", ""), d["name"],
                     d["status"], f"{d['conf']:.2f}", f.get("issuer") or "",
                     f.get("docNo") or "", f.get("date") or "", f.get("region") or "",
                     f.get("support") or "", f.get("deadline") or ""])
    width = [5, 5, 16, 34, 8, 6, 20, 16, 11, 10, 34, 11]
    for r in rows[: args.limit]:
        print(" | ".join(str(v)[:w] for v, w in zip(r, width)))

    st = res["stats"]
    print("-" * 70)
    print(f"{st['docs']} 份（完全成功 {st['ok']} / 部分成功 {st['partial']} / 失败 {st['failed']}）"
          f"｜字段 {st['fields']} 个，可定位 {st['locatable']} 个，平均置信度 {st['avg_conf']}")
    if res["skipped"]:
        print(f"跳过 {len(res['skipped'])} 条：" + "；".join(res["skipped"][:3]))
    print("原文链接：")
    for d in docs:
        print(f"  {d['id']} {d.get('url', '')}")

    if args.csv:
        import csv
        with open(args.csv, "w", encoding="utf-8-sig", newline="") as fh:
            csv.writer(fh).writerows(rows)
        print(f"已写出：{args.csv}")
    return 0


def cmd_policy(args) -> int:
    """政策公告批量解析：materials/ → 结构化字段 + 异常清单（CSV）。"""
    from .config import ROOT
    from .services import policy

    src = args.dir or str(ROOT / "materials")
    docs, skipped = policy.scan_dir(src)
    if not docs:
        print(f"目录中没有可解析的文件：{src}")
        print("支持 .pdf / .docx / .txt / .md / .html")
        return 0

    st = policy.stats(docs)
    print(f"\n明鉴 · 政策公告批量解析（{src}）")
    print("=" * 70)
    head = ["编号", "文件", "状态", "置信度", "发文机关", "文号", "发布日期",
            "适用地区", "适用行业", "支持标准", "申报截止", "异常说明"]
    rows = [head]
    for d in docs:
        f = d["f"]
        rows.append([d["id"], d["name"], d["status"], f"{d['conf']:.2f}",
                     f.get("issuer") or "", f.get("docNo") or "", f.get("date") or "",
                     f.get("region") or "", f.get("industry") or "",
                     f.get("support") or "", f.get("deadline") or "", d.get("issue") or ""])
    width = [6, 30, 8, 6, 22, 18, 10, 12, 14, 30, 12, 30]
    for r in rows[: args.limit]:
        print(" | ".join(str(v)[:w] for v, w in zip(r, width)))

    print("-" * 70)
    print(f"材料 {st['docs']} 份（完全成功 {st['ok']} / 部分成功 {st['partial']} / 失败 {st['failed']}）")
    print(f"字段 {st['fields']} 个，可定位原文 {st['locatable']} 个，平均置信度 {st['avg_conf']}")
    if skipped:
        print(f"跳过 {len(skipped)} 份：" + "；".join(skipped[:3]))

    if args.csv:
        import csv
        with open(args.csv, "w", encoding="utf-8-sig", newline="") as fh:
            csv.writer(fh).writerows(rows)
        print(f"已写出：{args.csv}")
    return 0


def cmd_sched(args) -> int:
    """定时任务管理：列表、新建/修改、启停、删除、立即执行。

    调度线程只在 serve 进程里跑；本命令只改定义，不常驻。
    """
    from .infra import scheduler

    if args.delete:
        if not store.get_schedule(args.delete):
            print(f"找不到定时任务 {args.delete}")
            return 1
        store.delete_schedule(args.delete)
        print(f"已删除定时任务 {args.delete}")
        return 0

    if args.toggle:
        s = store.get_schedule(args.toggle)
        if not s:
            print(f"找不到定时任务 {args.toggle}")
            return 1
        enabled = not s.get("enabled")
        nxt = scheduler.next_run_text(s["spec"]) if enabled else ""
        store.set_schedule_enabled(s["id"], enabled, nxt)
        print(f"{s['id']} 已{'启用，下次 ' + nxt if enabled else '停用'}")
        return 0

    if args.run_now:
        s = store.get_schedule(args.run_now)
        if not s:
            print(f"找不到定时任务 {args.run_now}")
            return 1
        params = s.get("params") or {}
        cfg = CFG
        for k in ("days", "max_news", "max_ann", "max_fetch"):
            if params.get(k) is not None:
                try:
                    setattr(cfg, k, int(params[k]))
                except Exception:
                    pass
        print(f"\n明鉴 · 立即执行定时任务 {s['id']}（{s.get('name')}）")
        print("=" * 62)
        if s.get("kind") == "compare":
            from .runtime import compare
            names = [str(c).strip() for c in (params.get("companies") or []) if str(c).strip()]
            if len(names) < 2:
                print("该任务的公司列表不足两家，无法执行")
                return 1
            res = compare.run(names, cfg=cfg, on_event=_mk_event_printer())
            md = OUT_DIR / f"竞品周报_CLI_{s['id']}.md"
            md.write_text(res["report"], encoding="utf-8")
            print(f"周报：{md}")
        else:
            company = str(params.get("company") or "").strip()
            if not company:
                print("该任务缺少公司名，无法执行")
                return 1
            res = pipeline.run(company, cfg=cfg, on_event=_mk_event_printer())
            print(f"报告：{OUT_DIR / (company + '_' + res['run_id'] + '.md')}")
        store.mark_schedule_run(s["id"], res["run_id"])
        return 0

    if not args.company and not args.companies:
        rows = store.list_schedules()
        if not rows:
            print("还没有定时任务。新建：python -m mingjian sched --company 某某科技 --daily --time 09:00")
            return 0
        print(f"{'编号':<22}{'名称':<24}{'规则':<14}{'下次运行':<21}{'状态':<6}最近运行")
        print("-" * 100)
        for s in rows:
            print(f"{s['id']:<22}{s['name'][:22]:<24}{scheduler.describe(s['spec']):<14}"
                  f"{s.get('next_run_at') or '-':<21}{'启用' if s.get('enabled') else '停用':<6}"
                  f"{s.get('last_run_id') or '-'}")
        return 0

    spec = {"freq": "weekly" if args.weekly else "daily", "time": args.time}
    if args.weekly:
        spec["weekday"] = args.weekly
    try:
        scheduler.validate(spec)
    except ValueError as e:
        print(f"调度规则有误：{e}")
        return 1

    if args.companies:
        names = [c.strip() for c in args.companies.replace("，", ",").split(",") if c.strip()]
        if len(names) < 2:
            print('竞品周报至少需要两家公司，用 --companies "A,B" 给出')
            return 1
        kind = "compare"
        params = {"companies": names[:6], "days": args.days, "max_fetch": args.fetch}
        default_name = " · ".join(names[:3]) + (" 等" if len(names) > 3 else "") + " 竞品周报"
    else:
        kind = "run"
        params = {"company": args.company.strip(), "days": args.days, "max_fetch": args.fetch}
        default_name = args.company.strip() + " 情报"

    seed = json.dumps(params, sort_keys=True, ensure_ascii=False)
    sid = args.id or f"S{time.strftime('%Y%m%d%H%M%S')}-{abs(hash(seed)) % 10000:04d}"
    s = store.upsert_schedule({
        "id": sid, "name": (args.name or default_name)[:60], "kind": kind,
        "params": params, "spec": spec, "enabled": not args.off,
        "next_run_at": scheduler.next_run_text(spec) if not args.off else "",
    })
    print(f"已保存定时任务 {s['id']}：{s['name']}｜{scheduler.describe(spec)}"
          f"｜下次 {s.get('next_run_at') or '未启用'}")
    return 0


def cmd_serve(args) -> int:
    from .server import serve
    serve(port=args.port, host=args.host, open_browser=not args.no_open)
    return 0


def cmd_subject(args) -> int:
    """建立/查看我方自述档案。非上市企业没有证券代码，竞品对比靠这份档案立基线。"""
    from .runtime.compare import DIMENSIONS  # 维度只有一份定义，这里借来算覆盖度
    from .services import subject
    if args.list:
        rows = subject.list_all()
        if not rows:
            print("还没有自述档案。用 python -m mingjian subject 建工 -p \"谐波减速器\" 先建一份。")
            return 0
        print(f"{'企业名称':<22}{'覆盖维度':<8}{'更新时间'}")
        for s in rows:
            cov = subject.coverage(s, DIMENSIONS)
            print(f"{s.get('name', ''):<22}{cov['filled']}/{cov['total']}{'':<5}{s.get('updated_at', '')}")
        return 0

    if not args.company:
        print("请给出企业名称，或用 --list 查看已有档案")
        return 1
    data = subject.get(args.company)
    data.update({"name": args.company})
    pairs = [("region", args.region), ("industry", args.industry), ("products", args.products),
             ("process", args.process), ("capacity", args.capacity),
             ("price_band", args.price), ("customers", args.customers),
             ("channels", args.channels), ("scale", args.scale), ("note", args.note)]
    for k, v in pairs:
        if v:
            data[k] = v
    if not any(v for _, v in pairs):
        # 没给任何字段时只做查看，避免一条空档案覆盖掉已有内容
        if not data:
            print(f"没有「{args.company}」的档案。补齐字段后再保存一次。")
            return 1
        cov = subject.coverage(data, DIMENSIONS)
        print(f"{data.get('name')}｜覆盖 {cov['filled']}/{cov['total']} 个维度")
        for f in subject.baseline(data):
            print(f"  {f['id']}〔{f['type']}〕{f['statement']}")
        return 0
    s = subject.save(data)
    cov = subject.coverage(s, DIMENSIONS)
    print(f"已保存：{s['name']}，覆盖 {cov['filled']}/{cov['total']} 个对标维度（企业自述，不冒充公开事实）")
    for f in subject.baseline(s):
        print(f"  {f['id']}〔{f['type']}〕{f['statement']}")
    return 0


def cmd_peers(args) -> int:
    """从品类词反查候选竞品：非上市企业不知道该盯谁时先用这个出线索。"""
    from .services import discover
    res = discover.find(args.products, args.region, days=args.days,
                        limit=args.limit, exclude=[args.exclude] if args.exclude else [])
    print(f"检索词：{' / '.join(res['queries'])}｜扫 {res['scanned']} 篇｜读正文 {res['fetched']} 篇")
    if not res["candidates"]:
        for n in res["notes"]:
            print(f"  · {n}")
        return 0
    print("")
    for c in res["candidates"]:
        ev = c.get("evidence") or {}
        flag = f"上市 {c['code']}" if c["listed"] else "未上市"
        print(f"  {c['name']}　[{flag}]　曝光 {c['count']} 篇")
        if ev.get("quote"):
            print(f"      原话：{ev['quote']}")
        print(f"      出处：{ev.get('date', '')} {ev.get('source', '')} {ev.get('url', '')}")
    print("\n以上是线索不是结论：曝光频次不代表规模或份额；带代码的可直接进竞品周报。")
    if args.csv:
        import csv
        with open(args.csv, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["候选企业", "曝光篇数", "是否上市", "代码", "证据原话", "证据标题", "证据链接", "日期"])
            for c in res["candidates"]:
                ev = c.get("evidence") or {}
                w.writerow([c["name"], c["count"], "是" if c["listed"] else "否", c["code"],
                            ev.get("quote", ""), ev.get("title", ""), ev.get("url", ""),
                            ev.get("date", "")])
        print(f"已写出：{args.csv}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="mingjian", description="明鉴：输入公司名，自动完成采集、抽取、比对与成稿")
    sub = p.add_subparsers(dest="cmd")

    r = sub.add_parser("run", help="运行一次情报分析")
    r.add_argument("company", help="公司名称或 6 位证券代码")
    r.add_argument("--days", type=int, default=CFG.days, help="回溯天数")
    r.add_argument("--fetch", type=int, default=CFG.max_fetch, help="正文抓取条数上限")
    r.add_argument("--news", type=int, default=CFG.max_news, help="资讯条数上限")
    r.add_argument("--ann", type=int, default=CFG.max_ann, help="公告条数上限")
    r.add_argument("--print-all", action="store_true", help="打印完整报告")
    r.set_defaults(func=cmd_run)

    ls_p = sub.add_parser("ls", help="查看运行历史")
    ls_p.add_argument("--limit", type=int, default=20)
    ls_p.set_defaults(func=cmd_ls)

    rs = sub.add_parser("resume", help="从最近检查点续跑失败/已取消的运行")
    rs.add_argument("run_id", help="运行编号，如 R20261118103000-1234")
    rs.add_argument("--print-all", action="store_true", help="打印完整报告")
    rs.set_defaults(func=cmd_resume)

    s = sub.add_parser("show", help="查看某次运行的事实")
    s.add_argument("run_id")
    s.add_argument("--limit", type=int, default=40)
    s.set_defaults(func=cmd_show)

    y = sub.add_parser("policy", help="批量解析 materials/ 下的政策公告")
    y.add_argument("--dir", default="", help="材料目录，默认 materials/")
    y.add_argument("--csv", default="", help="导出 CSV 路径")
    y.add_argument("--limit", type=int, default=40, help="终端最多显示条数")
    y.set_defaults(func=cmd_policy)

    g = sub.add_parser("gov", help="按公司所属产业，从工信部与省工信厅官网取政策与补贴公告")
    g.add_argument("company", help="公司名称，例如 某某科技")
    g.add_argument("--province", default="", help="指定省份（默认按注册地识别）")
    g.add_argument("--keyword", action="append", default=[], help="自定义检索词，可多次")
    g.add_argument("--central", type=int, default=5, help="中央部委最多取几份")
    g.add_argument("--local", type=int, default=5, help="地方厅局最多取几份")
    g.add_argument("--csv", default="", help="导出 CSV 路径")
    g.add_argument("--limit", type=int, default=40, help="终端最多显示条数")
    g.set_defaults(func=cmd_gov)

    j = sub.add_parser("subject", help="建立/查看我方自述档案（非上市企业用）")
    j.add_argument("company", nargs="?", default="", help="企业名称")
    j.add_argument("--list", action="store_true", help="列出已有档案")
    j.add_argument("--region", default="", help="所在地")
    j.add_argument("--industry", default="", help="行业")
    j.add_argument("-p", "--products", default="", help="主营产品（逗号分隔）")
    j.add_argument("--process", default="", help="关键工艺")
    j.add_argument("--capacity", default="", help="产能与产线")
    j.add_argument("--price", default="", help="价格带")
    j.add_argument("--customers", default="", help="主要客户行业")
    j.add_argument("--channels", default="", help="销售渠道")
    j.add_argument("--scale", default="", help="营收规模档")
    j.add_argument("--note", default="", help="补充说明")
    j.set_defaults(func=cmd_subject)

    q = sub.add_parser("peers", help="用产品品类词反查候选竞品（线索，需人工确认）")
    q.add_argument("products", help="产品/工艺品类词，逗号分隔，如 谐波减速器,精密行星减速机")
    q.add_argument("--region", default="", help="所在地，用于加一组地域联合检索")
    q.add_argument("--days", type=int, default=180, help="资讯回溯天数（提示用）")
    q.add_argument("--limit", type=int, default=12, help="最多返回候选数")
    q.add_argument("--exclude", default="", help="要排除的企业名，通常是甲方自己")
    q.add_argument("--csv", default="", help="导出 CSV 路径")
    q.set_defaults(func=cmd_peers)

    v = sub.add_parser("serve", help="启动本地 Web 界面")
    v.add_argument("--port", type=int, default=8765)
    v.add_argument("--host", default="127.0.0.1")
    v.add_argument("--no-open", action="store_true")
    v.set_defaults(func=cmd_serve)

    sc = sub.add_parser("sched", help="管理定时任务（每天/每周，由 serve 进程触发）")
    sc.add_argument("--company", default="", help="公司名；与 --companies 二选一")
    sc.add_argument("--companies", default="", help="竞品周报公司列表，逗号分隔，至少两家")
    sc.add_argument("--daily", action="store_true", help="每天执行（默认）")
    sc.add_argument("--weekly", type=int, default=0, help="每周执行，1=周一 … 7=周日")
    sc.add_argument("--time", default="09:00", help="执行时间 HH:MM，默认 09:00")
    sc.add_argument("--name", default="", help="任务名称")
    sc.add_argument("--id", default="", help="更新已有任务时给出编号")
    sc.add_argument("--days", type=int, default=CFG.days, help="回溯天数")
    sc.add_argument("--fetch", type=int, default=CFG.max_fetch, help="正文抓取条数上限")
    sc.add_argument("--off", action="store_true", help="创建后暂不启用")
    sc.add_argument("--delete", default="", help="删除指定任务")
    sc.add_argument("--toggle", default="", help="启用/停用指定任务")
    sc.add_argument("--run-now", default="", help="立即在本地执行一次指定任务")
    sc.set_defaults(func=cmd_sched)

    args = p.parse_args(argv)
    if not getattr(args, "func", None):
        p.print_help()
        return 1
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    store.init()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
