"""
命令行入口。

    python -m mingjian run 某某科技
    python -m mingjian run 某某精密 --days 60 --fetch 16
    python -m mingjian resume R20261118...
    python -m mingjian ls
    python -m mingjian show R20261118...
    python -m mingjian sched --company 某某科技 --daily --time 09:00
    python -m mingjian db --counts
    python -m mingjian optimize eval --candidate 候选.json
    python -m mingjian serve --port 8765

输出同时落盘到 output/，报告可直接发给业务方。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

from .config import CFG, OUT_DIR, ROOT
from .infra import db as dbmod
from .infra import llmcfg, store
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
    cfg.news_pages = args.pages

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
    for k in ("days", "max_news", "max_ann", "max_fetch", "news_pages"):
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


def cmd_db(args) -> int:
    """查看当前数据库、建表与行数。换库只改配置，不改代码。"""
    try:
        dialect = store.dialect()
    except ValueError as e:
        print(f"配置错误：{e}")
        return 2
    print(f"方言：{dialect}")
    print(f"目标：{store.target()}")
    try:
        version = store.conn().version()
    except dbmod.DriverMissing as e:
        print(str(e))
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"连接失败：{type(e).__name__}: {e}")
        return 3
    print(f"版本：{version}")
    if args.init:
        store.init(force=True)
        print(f"建表：完成（{len(store.ddl_statements())} 条语句）")
    try:
        store.conn().execute("SELECT COUNT(*) AS n FROM runs").fetchone()
    except Exception as e:  # noqa: BLE001
        print(f"表未建立或不可读：{type(e).__name__}: {e}")
        print("可先执行：python -m mingjian db --init")
        return 4
    counts = store.table_counts()
    print(f"表：{len(counts)} 张，共 {sum(counts.values())} 行")
    if args.counts:
        for name, n in counts.items():
            print(f"  {name:<12}{n}")
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
        for k in ("days", "max_news", "max_ann", "max_fetch", "news_pages"):
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


def _rel(path) -> str:
    try:
        return str(pathlib.Path(path).resolve().relative_to(ROOT.resolve()))
    except (OSError, ValueError):
        return str(path)


def _print_metrics(title: str, base: dict, cand: dict) -> None:
    print(f"{title}：基线准确率 {base['accuracy']:.1%} → 候选 {cand['accuracy']:.1%}；"
          f"宏 F1 {base['macro_f1']:.3f} → {cand['macro_f1']:.3f}")


def _print_eval(report: dict) -> None:
    _print_metrics("离线评测", report["baseline"]["metrics"], report["candidate_metrics"])
    print(f"候选：{report['candidate']['version']}｜新增关键词：{report['candidate']['added']}")
    for check in report["gate"]["checks"]:
        flag = "通过" if check["ok"] else "未通过"
        print(f"  [{flag}] {check['name']}：{check['detail']}")
    print(f"门禁：{'通过' if report['gate']['passed'] else '未通过'}")
    if report.get("errors", {}).get("new"):
        print("新增错误：")
        for row in report["errors"]["new"][:5]:
            print(f"  {row['id']} {row['title']}：{row['expected']} → {row['predicted']}")
    if report.get("evaluation_file"):
        print(f"评测文件：{_rel(report['evaluation_file'])}")


def cmd_optimize(args) -> int:
    """离线候选评测、门禁上线、回滚与历史查看。不触网、不启动管道。"""
    from .services import optimizer, searchterms

    action = getattr(args, "opt_cmd", "")
    try:
        if action == "baseline":
            result = optimizer.evaluate_cases(args.cases)
            print(f"基线版本：{result['version']}｜样本 {result['case_count']} 条")
            print(f"准确率 {result['metrics']['accuracy']:.1%}｜宏 F1 {result['metrics']['macro_f1']:.3f}")
            if result["errors"]:
                print("基线错误：")
                for row in result["errors"][:5]:
                    print(f"  {row['id']} {row['title']}：{row['expected']} → {row['predicted']}")
            return 0
        if action == "draft":
            result = optimizer.draft(args.cases, args.out, version=args.version,
                                     description=args.description,
                                     max_per_type=args.max_per_type)
            print(f"草案：{_rel(result['path'])}｜基线 {result['base_version']}｜"
                  f"判错 {result['errors']} 条 / 共 {result['cases']} 条")
            print(f"建议关键词：{result['suggested']}")
            for row in result["reviewed"]:
                print(f"  {row['id']} {row['expected']} ← {row['predicted']}：{row['word']}")
            print("草案不会自动上线；复核后先 eval，再 promote")
            return 0
        if action == "harvest":
            if getattr(args, "kind", "facts") == "docs":
                result = optimizer.harvest_docs(args.out, limit=args.limit,
                                                entity_key=args.entity,
                                                prefix=args.prefix or "D",
                                                split=args.split, cases_path=args.cases,
                                                include_covered=args.include_covered)
                print(f"待标注文档：{_rel(result['path'])}｜{result['count']} 条"
                      f"（扫描 {result['scanned']} 份文档）")
                skipped = result["skipped"]
                print(f"跳过：重复 {skipped['duplicate']}｜无标题 {skipped['no_title']}｜"
                      f"例行公告 {skipped['routine']}｜正文过短 {skipped['no_body']}｜"
                      f"无主体 {skipped['no_entity']}")
                print("expected 填 fact 或 none：只判断“该文档是否应产出事实”，不标事件类型")
                return 0 if result["count"] else 1
            result = optimizer.harvest(args.out, limit=args.limit, entity_key=args.entity,
                                       prefix=args.prefix or "H", split=args.split,
                                       cases_path=args.cases)
            print(f"待标注样本：{_rel(result['path'])}｜{result['count']} 条"
                  f"（扫描 {result['scanned']} 条事实）")
            skipped = result["skipped"]
            print(f"跳过：重复 {skipped['duplicate']}｜无标题 {skipped['no_title']}｜"
                  f"例行公告 {skipped['routine']}")
            print("expected 已留空：需人工读原文填写；hint.predicted 只是运行期判定，不是标准答案")
            return 0 if result["count"] else 1
        if action == "recall":
            cases = optimizer.load_doc_cases(args.cases)
            report = optimizer.recall(cases, actor=args.actor)
            print(f"召回评测：{report['version']}｜样本 {report['cases']} 条"
                  f"｜准确率 {report['accuracy']:.1%}")
            print(f"漏检 {len(report['missed'])} 条｜误报 {len(report['false_positive'])} 条")
            for row in report["missed"][:5]:
                print(f"  漏检 {row['id']} {row['title']}")
            for row in report["false_positive"][:5]:
                print(f"  误报 {row['id']} {row['title']}")
            if report["skipped"]:
                print(f"跳过：{len(report['skipped'])} 条（缺少主体名）")
            print(f"评测文件：{_rel(report['evaluation_file'])}")
            return 0
        if action == "collect":
            data = optimizer.collect_report(args.run_id, limit=args.limit)
            print(f"运行 {data['run_id']}｜采集留痕：{data['log'] or '（无）'}")
            for q in data["queries"]:
                tail = ""
                if q.get("status") == "error":
                    tail = f"｜失败 {q.get('error') or ''}"
                elif q.get("status") == "empty":
                    tail = "｜无结果"
                print(f"  {q['query']}：{q.get('ms', 0)}ms｜候选 {q['raw']}"
                      f"｜入围 {q['kept']}｜重复 {q['duplicate']}"
                      f"｜不相关 {q['irrelevant']}{tail}")
            if not data["queries"]:
                print("  （没有统计：旧版本运行，或当次资讯采集失败）")
            if data["queries"]:
                print(f"检索失败 {data.get('failed', 0)} 路｜无结果 {data.get('empty', 0)} 路")
            cov = data.get("coverage") or {}
            if cov:
                print(f"公告覆盖：{cov.get('covered', 0)}/{cov.get('announcements', 0)} 条在资讯中出现"
                      f"（{cov.get('ratio', 0):.0%}）｜缺口 {cov.get('gap', 0)} 条"
                      f"｜{data.get('gap_log') or '（无）'}")
                for row in data.get("gap_rows") or []:
                    print(f"  [{row.get('score', 0):.2f}] {row.get('title', '')}")
            print(f"被拒条目 {data['rejected']} 条，显示前 {len(data['rows'])} 条：")
            for row in data["rows"]:
                print(f"  [{row.get('reason', '')}] {row.get('title', '')}"
                      f"｜{row.get('query', '')}")
            if data["rejected"] and not data["rows"]:
                print("  （留痕文件缺失：统计还在，明细可能被手动清理）")
            return 0
        if action == "depth":
            report = optimizer.depth_report(args.base, args.cand)
            b, c = report["base"], report["cand"]
            print(f"深度对照：{b['run_id']} → {c['run_id']}｜{b['company']}"
                  f"｜页数 {b['pages']} → {c['pages']}")
            print(f"候选 {b['candidates']} → {c['candidates']}"
                  f"（{report['candidates']['delta']:+d}）"
                  f"｜入围 {b['kept']} → {c['kept']}"
                  f"（{report['kept']['delta']:+d}）")
            print(f"文档 {b['docs']} → {c['docs']}｜新增 {report['docs']['new']}"
                  f"（带事实 {report['new_docs_with_fact']}）"
                  f"｜重叠 {report['docs']['overlap']}")
            print(f"检索耗时 {b['ms']}ms → {c['ms']}ms")
            for row in report["per_query"]:
                print(f"  {row['query']}：候选 {row['base_raw']}→{row['cand_raw']}"
                      f"｜入围 {row['base_kept']}→{row['cand_kept']}")
            if report["terms_changed"]:
                print("检索词版本不同：只能看边际，不能当同词对照")
            print(f"报告：{_rel(report['report_file'])}")
            return 0
        if action == "eval":
            report = optimizer.evaluate_file(args.candidate, args.cases, actor=args.actor)
            _print_eval(report)
            return 0 if report["gate"]["passed"] else 1
        if action == "promote":
            result = optimizer.promote(args.candidate, args.cases, actor=args.actor)
            _print_eval(result["report"])
            print(f"已上线：{result['version']}（原版本 {result['base_version']}）")
            return 0
        if action == "rollback":
            result = optimizer.rollback(args.version, actor=args.actor)
            print(f"已回滚：{result['from_version']} → {result['version']}")
            return 0
        if action == "status":
            data = optimizer.status(limit=args.limit)
            active = data["active"]
            print(f"当前生效：{active['version']}｜规则 {active['rules']} 类 / {active['keywords']} 词")
            if active.get("promoted_at"):
                print(f"上线时间：{active['promoted_at']}｜操作人：{active.get('actor') or '-'}")
            print(f"已归档版本：{data['versions']} 个")
            terms = searchterms.load()
            print(f"检索词：{terms['version']}｜{len(terms['terms'])} 路（未纳入门禁）")
            if data["evaluations"]:
                print(f"最近评测：{data['evaluations'][0]}")
            return 0
        if action == "history":
            data = optimizer.history(limit=args.limit)
            print("已归档版本：")
            if not data["versions"]:
                print("  （无）")
            for row in data["versions"]:
                print(f"  {row['version']} ← {row.get('base_version') or '-'}｜{row.get('promoted_at') or '-'}｜{row.get('actor') or '-'}")
            print("最近评测：")
            if not data["evaluations"]:
                print("  （无）")
            for name in data["evaluations"][:args.limit]:
                print(f"  {name}")
            return 0
        print("请指定 optimize 子命令：baseline / draft / harvest / recall / collect / depth / eval / promote / rollback / status / history")
        return 1
    except (ValueError, FileExistsError, OSError) as e:
        print(f"失败：{e}")
        return 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="mingjian", description="明鉴：输入公司名，自动完成采集、抽取、比对与成稿")
    sub = p.add_subparsers(dest="cmd")

    r = sub.add_parser("run", help="运行一次情报分析")
    r.add_argument("company", help="公司名称")
    r.add_argument("--days", type=int, default=CFG.days, help="回溯天数")
    r.add_argument("--fetch", type=int, default=CFG.max_fetch, help="正文抓取条数上限")
    r.add_argument("--news", type=int, default=CFG.max_news, help="资讯条数上限")
    r.add_argument("--pages", type=int, default=CFG.news_pages, help="资讯首路翻页数（加深对照用）")
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

    d = sub.add_parser("db", help="查看当前数据库、建表与行数（默认 SQLite，可切 PostgreSQL / MySQL）")
    d.add_argument("--init", action="store_true", help="按当前连接串建表（幂等）")
    d.add_argument("--counts", action="store_true", help="逐表打印行数")
    d.set_defaults(func=cmd_db)

    o = sub.add_parser("optimize", help="离线评测候选规则，门禁通过后上线或回滚")
    osub = o.add_subparsers(dest="opt_cmd")
    cases_default = str(ROOT / "tools" / "optimizer" / "event_cases.jsonl")

    ob = osub.add_parser("baseline", help="只跑当前生效规则的基线评测")
    ob.add_argument("--cases", default=cases_default, help="评测集（JSON 或 JSONL）")
    ob.set_defaults(func=cmd_optimize)

    oe = osub.add_parser("eval", help="离线对照评测候选，不改变生效规则")
    oe.add_argument("--candidate", required=True, help="候选 JSON")
    oe.add_argument("--cases", default=cases_default, help="评测集（JSON 或 JSONL）")
    oe.add_argument("--actor", default="cli", help="操作人（写入审计）")
    oe.set_defaults(func=cmd_optimize)

    od = osub.add_parser("draft", help="从评测集判错样本生成候选草案，不上线")
    od.add_argument("--cases", default=cases_default, help="评测集（JSON 或 JSONL）")
    od.add_argument("--out", default=str(ROOT / "tools" / "optimizer"), help="草案输出目录")
    od.add_argument("--version", default="", help="草案版本号，默认按时间生成")
    od.add_argument("--description", default="", help="草案说明")
    od.add_argument("--max-per-type", type=int, default=6, help="每个事件类型最多抽取几个词")
    od.set_defaults(func=cmd_optimize)

    ohv = osub.add_parser("harvest", help="把运行事实或文档导出成待标注样本，不自动标注")
    ohv.add_argument("--out", default=str(ROOT / "tools" / "optimizer"), help="输出目录")
    ohv.add_argument("--cases", default=cases_default, help="已有评测集，用于去重")
    ohv.add_argument("--kind", default="facts", choices=("facts", "docs"),
                     help="facts=分类样本；docs=文档级召回样本")
    ohv.add_argument("--limit", type=int, default=200, help="最多扫描多少条事实或文档")
    ohv.add_argument("--entity", default="", help="只导出某个实体 key 的样本")
    ohv.add_argument("--prefix", default="", help="样本编号前缀（默认 H / D）")
    ohv.add_argument("--split", default="dev", choices=("dev", "holdout"), help="归入开发集还是保留集")
    ohv.add_argument("--include-covered", action="store_true",
                     help="仅 kind=docs：连已产出事实的文档一起导出")
    ohv.set_defaults(func=cmd_optimize)

    orc = osub.add_parser("recall", help="文档级召回评测：只判是否产出事实，不设门禁")
    orc.add_argument("--cases", default=str(ROOT / "tools" / "optimizer" / "doc_cases.jsonl"),
                     help="文档级召回评测集（JSON 或 JSONL）")
    orc.add_argument("--actor", default="cli", help="操作人（写入审计）")
    orc.set_defaults(func=cmd_optimize)

    occ = osub.add_parser("collect", help="查看某次运行的采集留痕：每路召回与被拒条目")
    occ.add_argument("run_id", help="运行编号，如 R20261118103000-1234")
    occ.add_argument("--limit", type=int, default=20, help="最多显示多少条被拒条目")
    occ.set_defaults(func=cmd_optimize)

    odp = osub.add_parser("depth", help="对照两次运行的检索深度：新增候选、文档与覆盖")
    odp.add_argument("base", help="浅检索运行编号")
    odp.add_argument("cand", help="深检索运行编号")
    odp.set_defaults(func=cmd_optimize)

    op = osub.add_parser("promote", help="重新评测，门禁通过后写入版本库并切换上线")
    op.add_argument("--candidate", required=True, help="候选 JSON")
    op.add_argument("--cases", default=cases_default, help="评测集（JSON 或 JSONL）")
    op.add_argument("--actor", default="cli", help="操作人（写入审计）")
    op.set_defaults(func=cmd_optimize)

    orb = osub.add_parser("rollback", help="回滚到已归档版本或 baseline")
    orb.add_argument("version", help="版本号，或 baseline")
    orb.add_argument("--actor", default="cli", help="操作人（写入审计）")
    orb.set_defaults(func=cmd_optimize)

    os_ = osub.add_parser("status", help="查看当前生效版本与最近评测")
    os_.add_argument("--limit", type=int, default=5)
    os_.set_defaults(func=cmd_optimize)

    oh = osub.add_parser("history", help="查看版本、评测与审计记录")
    oh.add_argument("--limit", type=int, default=20)
    oh.set_defaults(func=cmd_optimize)

    o.set_defaults(func=cmd_optimize)

    args = p.parse_args(argv)
    if not getattr(args, "func", None):
        p.print_help()
        return 1
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    # db 子命令自己连库并给友好提示；optimize 是离线评测，不依赖数据库；
    # 其余命令先保证表已建好，并读回库里的模型配置
    if args.cmd not in ("db", "optimize"):
        store.init()
        llmcfg.bootstrap()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
