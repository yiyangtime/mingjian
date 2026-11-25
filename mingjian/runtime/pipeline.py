"""
流水线编排：输入一个公司名，走完采集→解析→抽取→合并→比对→成稿→入库。

设计原则（与需求文档里 8 条硬约束对应）：
  · 每一步都可单独失败、单独降级，失败写进缺口而不是抛出中断整批；
  · 所有事实必须绑定 doc_id，doc 必须绑定 url；
  · 判断与事实分离，判断只能引用事实编号；
  · 每一步都发事件，前端和 CLI 是同一套执行路径，不存在"演示走假流程"。
"""
from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..config import CFG, OUT_DIR
from ..core import pdftext
from ..core.textproc import cjk_count, clip, extract_main, page_title
from ..infra import llm, store
from ..infra.sources import cninfo, eastmoney
from ..services import coverage, diff, memory
from ..services import extract as EX
from ..services import report as R
from ..services import searchterms as ST


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _hash(s: str, n: int = 12) -> str:
    return hashlib.md5(s.encode("utf-8", "ignore")).hexdigest()[:n]


class Cancelled(RuntimeError):
    """用户主动取消。单独一类异常，状态记 cancelled，不与失败混在一起。"""


class Pipeline:
    def __init__(self, company: str, cfg=None, on_event=None, run_id: str | None = None,
                 resume: bool = False, cancel_check=None):
        self.company = (company or "").strip()
        self.cfg = cfg or CFG
        self.on = on_event or (lambda e: None)
        self.run_id = run_id or f"R{time.strftime('%Y%m%d%H%M%S')}-{_hash(self.company, 4)}"
        self.resume = bool(resume)
        self.cancel_check = cancel_check or (lambda: False)
        self.entity_key = ""
        self.entity: dict = {}
        self.profile: dict = {}
        self.quote: dict = {}
        self.docs: list[dict] = []
        self.facts: list[dict] = []
        self.dropped_conflicts: list[dict] = []
        self.judges: list[dict] = []
        self.gaps: list[str] = []
        self.changes: dict = {}
        self.report_md = ""
        self.stats: dict = {}
        self.errors: list[str] = []
        # 运行开始时固定规则与检索词版本：中途上线新版本不影响本次运行
        self.event_rules_version = ""
        self.event_rules: list[dict] | None = None
        self.search_terms_version = ""
        self.search_terms: list[str] = []

    # ---- 事件 ----
    def _log(self, msg: str, level: str = "info") -> None:
        self.on({"type": "log", "level": level, "msg": msg, "ts": _now()})

    def _step(self, idx: int, name: str, status: str, detail: str = "") -> None:
        if status == "start":
            self._tick()
        self.on({"type": "step", "idx": idx, "name": name, "status": status, "detail": detail})

    def _tick(self) -> None:
        """在每个步骤边界检查是否被取消；长循环里也会主动调用。"""
        if self.cancel_check():
            raise Cancelled("用户取消")

    # ---- 主流程 ----
    def run(self) -> dict:
        t0 = time.time()
        store.init()
        self.event_rules_version = EX.active_version()
        self.event_rules = EX.rules_for_version(self.event_rules_version)
        self.search_terms_version = ST.active_version()
        self.search_terms = ST.active_terms()
        try:
            if not self.resume:
                # 新运行使用新编号；若人为复用编号，先清掉旧产物，避免新旧材料混在一起
                store.reset_run(self.run_id)
            else:
                store.mark_running(self.run_id)

            start = self._restore() if self.resume else 0
            if start == 0 and self.resume:
                # 没有检查点：保留失败日志，从第 1 步完整重跑
                store.reset_run(self.run_id, keep_events=True)
            if start < 1:
                self._s1_resolve()
            if start < 2:
                store.clear_documents(self.run_id)
                self._s2_collect()
            else:
                self.docs = store.docs_of_run(self.run_id)
                self._log(f"检查点恢复：{len(self.docs)} 份材料")
            if start < 3:
                self._s3_fetch()
            if start < 4:
                store.clear_facts(self.run_id)
                self._s4_extract()
            else:
                self.facts = self._facts_from_db()
                self._log(f"检查点恢复：{len(self.facts)} 条事实")
            self._s5_diff()
            self._s6_compose()
            self._s7_persist()
        except Cancelled as e:
            self.stats["elapsed_sec"] = round(time.time() - t0, 1)
            self._log(str(e), "warn")
            store.finish_run(self.run_id, "cancelled", self.stats, str(e))
            raise
        except Exception as e:
            self.stats["elapsed_sec"] = round(time.time() - t0, 1)
            self._log(f"运行中断：{type(e).__name__}: {e}", "error")
            store.finish_run(self.run_id, "failed", self.stats, f"{type(e).__name__}: {e}")
            raise
        self.stats["elapsed_sec"] = round(time.time() - t0, 1)
        store.finish_run(self.run_id, "done", self.stats)
        self._log(f"完成，用时 {self.stats['elapsed_sec']} 秒，{len(self.facts)} 条事实", "ok")
        self.on({"type": "done", "run_id": self.run_id, "stats": self.stats})
        return {
            "run_id": self.run_id, "company": self.company, "entity": self.entity,
            "entity_key": self.entity_key,
            "profile": self.profile, "quote": self.quote, "docs": self.docs,
            "facts": self.facts, "changes": self.changes, "report": self.report_md,
            "stats": self.stats, "errors": self.errors,
        }

    # ---- 检查点 ----
    def _save_cp(self, step: int) -> None:
        store.save_checkpoint(self.run_id, step, {
            "entity_key": self.entity_key, "entity": self.entity,
            "profile": self.profile, "quote": self.quote, "stats": self.stats,
            "event_rules_version": self.event_rules_version,
            "search_terms_version": self.search_terms_version,
        })

    def _restore(self) -> int:
        cp = store.last_checkpoint(self.run_id)
        if not cp:
            self._log("没有可用检查点，按完整流程重跑", "warn")
            return 0
        p = cp.get("payload") or {}
        run = store.get_run(self.run_id) or {}
        self.entity_key = p.get("entity_key") or run.get("entity_key", "")
        self.entity = p.get("entity") or store.get_entity(self.entity_key) or {}
        self.profile = p.get("profile") or {}
        self.quote = p.get("quote") or {}
        self.stats = p.get("stats") or {}
        self.event_rules_version = p.get("event_rules_version") or self.event_rules_version or "baseline"
        self.event_rules = EX.rules_for_version(self.event_rules_version)
        if int(cp["step"]) >= 2:
            # 第 2 步已完成，检索词不再使用；保留当次采集记录的版本号
            self.search_terms_version = p.get("search_terms_version") or self.search_terms_version
        else:
            # 会重跑第 2 步，检索词取当次生效值，版本号同步
            self.search_terms_version = ST.active_version()
            self.search_terms = ST.active_terms()
        name = self.entity.get("short_name") or self.company
        self._log(f"从第 {cp['step']} 步检查点续跑（{name}）", "ok")
        return int(cp["step"])

    def _facts_from_db(self) -> list[dict]:
        by_id = {d["id"]: d for d in self.docs}
        out = []
        for row in store.facts_of_run(self.run_id):
            full = row.get("id") or ""
            short = full[len(self.run_id) + 1:] if full.startswith(self.run_id + "-") else full
            doc = by_id.get(row.get("doc_id") or "", {})
            out.append({
                "id": short, "doc_id": row.get("doc_id"),
                "type": row.get("type"), "type_label": row.get("type_label"),
                "statement": row.get("statement"),
                "evidence_quote": row.get("evidence_quote") or "",
                "amount": row.get("amount") or "", "event_date": row.get("event_date") or "",
                "confidence": float(row.get("confidence") or 0),
                "dedupe_key": row.get("dedupe_key") or "",
                "corroboration": int(row.get("corroboration") or 1),
                "conflict": int(row.get("conflict") or 0),
                "conflict_note": row.get("conflict_note") or "",
                "source": doc.get("source") or "", "url": doc.get("url") or "",
                "published_at": doc.get("published_at") or "",
                "evidence_level": doc.get("evidence_level") or "",
                "kind": doc.get("kind") or "", "doc_title": doc.get("title") or "",
            })
        return out

    # ---- 1. 实体解析 ----
    def _s1_resolve(self) -> None:
        self._step(1, "实体解析", "start")
        if not self.company:
            raise ValueError("公司名不能为空")

        ent = cninfo.resolve(self.company)
        if not ent:
            self._log(f"「{self.company}」未在巨潮检索到上市主体，按非上市/名称不精确处理", "warn")
            ent = {"short_name": self.company, "code": "", "org_id": "", "market": "",
                   "column": "", "resolved_by": "none"}
        self.entity = ent
        self.entity_key = (ent.get("code") or ent.get("short_name") or self.company).strip()
        name = ent.get("short_name") or self.company
        self._log(f"匹配到主体：{name} {ent.get('code') or ''}（{ent.get('resolved_by')}）")

        if ent.get("code"):
            with ThreadPoolExecutor(max_workers=2) as ex:
                f1 = ex.submit(eastmoney.profile, ent["code"], ent.get("market", ""))
                f2 = ex.submit(eastmoney.quote, ent["code"])
                try:
                    self.profile = f1.result(timeout=25)
                except Exception as e:
                    self._log(f"公司档案获取失败（{type(e).__name__}），降级为仅名称", "warn")
                try:
                    self.quote = f2.result(timeout=25)
                except Exception as e:
                    self._log(f"行情获取失败（{type(e).__name__}），报告不含市值", "warn")

        if self.profile.get("full_name"):
            self._log(f"全称：{self.profile['full_name']}｜行业：{self.profile.get('industry', '—')}")

        store.upsert_entity(
            self.entity_key, name=self.company,
            short_name=ent.get("short_name", ""), full_name=self.profile.get("full_name", ""),
            code=ent.get("code", ""), org_id=ent.get("org_id", ""),
            market=ent.get("market", ""), resolved_by=ent.get("resolved_by", ""),
        )
        params = self.cfg.to_dict()
        params["event_rules_version"] = self.event_rules_version or EX.active_version()
        params["search_terms_version"] = self.search_terms_version or ST.active_version()
        store.create_run(self.run_id, self.entity_key, self.company, params)
        self._save_cp(1)
        self._step(1, "实体解析", "done", f"{name} {ent.get('code') or ''}".strip())

    def _names(self) -> set[str]:
        ns = {self.entity.get("short_name"), self.company, self.profile.get("full_name")}
        return {n for n in ns if n}

    # ---- 2. 采集 ----
    def _s2_collect(self) -> None:
        self._step(2, "多源采集", "start")
        ent = self.entity
        anns: list[dict] = []
        news: list[dict] = []

        if ent.get("code"):
            try:
                anns = cninfo.announcements(ent["code"], ent["org_id"], ent["column"],
                                            days=self.cfg.days, page_size=self.cfg.max_ann)
                self._log(f"巨潮公告：取得 {len(anns)} 条（近 {self.cfg.days} 天）")
            except Exception as e:
                self.errors.append(f"公告采集失败：{e}")
                self._log(f"公告采集失败：{type(e).__name__}", "warn")

        trace: dict = {}
        try:
            news = eastmoney.news_bundle(self.company, ent.get("short_name") or self.company,
                                         days=self.cfg.days,
                                         terms=self.search_terms or None, trace=trace)
            recall: dict[str, int] = {}
            for d in news:
                q = str((d.get("extra") or {}).get("query") or "")
                if q:
                    recall[q] = recall.get(q, 0) + 1
            self.stats["search_recall"] = recall
            self.stats["search_terms_version"] = self.search_terms_version or "baseline"
            self._record_collect(trace)
            self._log(f"资讯检索：取得 {len(news)} 条（{len(recall)} 路有召回，"
                      f"过滤 {len(trace.get('rejected') or [])} 条）")
        except Exception as e:
            self.errors.append(f"资讯采集失败：{e}")
            self._log(f"资讯采集失败：{type(e).__name__}", "warn")

        # 赋值 id 与主体信息
        for d in anns + news:
            d["id"] = "D" + _hash(d.get("url") or d.get("title", ""), 10)
            d["run_id"] = self.run_id
            d["entity_key"] = self.entity_key
            d["fetched_at"] = _now()
            d["title"] = clip(d.get("title", ""), 200)

        # 公告优先，资讯按时间排序，去重同 URL
        seen, merged = set(), []
        for d in anns + news:
            u = d.get("url") or d.get("title")
            if u in seen:
                continue
            seen.add(u)
            merged.append(d)
        self._tick()
        self.docs = merged
        self._record_coverage(merged)
        # 第 2 步就把材料清单落库：即便第 3 步抓正文时中断，续跑也不用重新检索
        for d in self.docs:
            store.add_document(d)
        self._save_cp(2)
        self._step(2, "多源采集", "done", f"公告 {len(anns)} · 资讯 {len(news)}")

    def _record_collect(self, trace: dict) -> None:
        """采集留痕：统计进 runs.stats，被拒条目落到 output/collect/<run_id>.jsonl。

        只记标题、链接与拒因，不存正文。用途是区分“检索词没召回”和“召回了
        但被主体过滤误杀”——后者现在可以人工复核，前者仍需接口层日志。
        """
        queries = trace.get("queries") or []
        rejected = trace.get("rejected") or []
        name = f"{self.run_id}.jsonl"
        try:
            path = OUT_DIR / "collect" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as fh:
                for row in rejected:
                    fh.write(json.dumps({"run_id": self.run_id, **row},
                                        ensure_ascii=False) + "\n")
        except OSError as e:
            self._log(f"采集留痕写入失败：{type(e).__name__}", "warn")
            name = ""
        failed = [q for q in queries if q.get("status") == "error"]
        empty = [q for q in queries if q.get("status") == "empty"]
        self.stats["collect"] = {"queries": queries, "rejected": len(rejected),
                                 "failed": len(failed), "empty": len(empty),
                                 "log": name}
        if failed:
            self._log(f"资讯检索失败 {len(failed)} 路：{failed[0].get('error') or ''}", "warn")
            if len(failed) == len(queries):
                # 部分路失败只降级；全部失败说明本轮可能整体缺资讯
                self.errors.append(f"资讯检索全部失败：{failed[0].get('error') or '原因未知'}")

    def _record_coverage(self, docs: list[dict]) -> None:
        """公告覆盖对照：公开披露的事项是否在同一轮资讯里出现过。

        公告与媒体资讯不是同一集合，低覆盖不必然是缺陷；这里只产出口清单
        供人工抽样，不参与门禁，也不影响采集与抽取。
        """
        anns = [d for d in docs if d.get("kind") == "announcement"]
        news = [d for d in docs if d.get("kind") == "news"]
        if not anns or not news:
            return
        report = coverage.announcement_gap(anns, news)
        name = f"{self.run_id}-gap.jsonl"
        try:
            path = OUT_DIR / "collect" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as fh:
                for row in report["rows"]:
                    if row["covered"]:
                        continue
                    fh.write(json.dumps({"run_id": self.run_id, **row},
                                        ensure_ascii=False) + "\n")
        except OSError as e:
            self._log(f"覆盖对照写入失败：{type(e).__name__}", "warn")
            name = ""
        self.stats["coverage"] = {
            "announcements": report["announcements"],
            "covered": report["covered"],
            "gap": report["gap"],
            "ratio": report["ratio"],
            "threshold": report["threshold"],
            "skipped": report["skipped"],
            "log": name,
        }
        self._log(f"公告覆盖对照：{report['covered']}/{report['announcements']} 条公告"
                  f"在资讯中出现（{report['ratio']:.0%}）")

    # ---- 3. 正文获取 ----
    def _s3_fetch(self) -> None:
        self._step(3, "正文解析", "start")
        budget = self.cfg.max_fetch
        # 优先级：有事件信息的公告（全文最有价值）→ 其余公告 → 资讯
        # 例行公告（月报表、法律意见书）不占抓取预算，避免把额度浪费在图片型 PDF 上
        anns = [d for d in self.docs if d["kind"] == "announcement" and d.get("url")]
        info_anns = [d for d in anns if not EX.ROUTINE.search(d.get("title", ""))]
        routine_anns = [d for d in anns if EX.ROUTINE.search(d.get("title", ""))]
        anns = info_anns + routine_anns
        news = [d for d in self.docs if d["kind"] == "news" and d.get("url")]
        n_ann = min(len(anns), max(2, int(budget * 0.6)))
        targets = anns[:n_ann] + news[:max(0, budget - n_ann)]

        ok, failed, image_pdf = 0, 0, 0

        def work(d: dict) -> tuple[str, str]:
            url = d["url"]
            if url.lower().endswith(".pdf") or d["kind"] == "announcement":
                from . import net
                data = net.fetch(url, retries=1, timeout=25).content
                txt = pdftext.extract_text(data)
                if pdftext.looks_like_text(txt):
                    return "full", txt
                return "image_pdf", ""
            from . import net
            html = net.fetch(url, timeout=20).text
            body = extract_main(html, page_title(html))
            if cjk_count(body) >= 150:
                return "full", body
            return "fail", ""

        with ThreadPoolExecutor(max_workers=4) as ex:
            futs = {ex.submit(work, d): d for d in targets}
            for fu in as_completed(futs):
                self._tick()
                d = futs[fu]
                try:
                    level, text = fu.result()
                except Exception as e:
                    level, text = "fail", ""
                    self._log(f"正文获取失败：{clip(d['title'], 30)}（{type(e).__name__}）", "warn")
                if level == "full":
                    d["body"], d["evidence_level"] = text, "full"
                    ok += 1
                    self._log(f"已取得正文：{clip(d['title'], 34)}（{cjk_count(text)} 字）")
                elif level == "image_pdf":
                    d["evidence_level"] = "title"
                    image_pdf += 1
                    self._log(f"公告为图片型 PDF，无法读取文本：{clip(d['title'], 30)}", "warn")
                else:
                    d["evidence_level"] = "snippet"
                    failed += 1

        self._log(f"正文解析完成：成功 {ok}，图片型 {image_pdf}，失败 {failed}")
        for d in self.docs:
            store.add_document(d)
        self._save_cp(3)
        self._step(3, "正文解析", "done", f"全文 {ok} · 图片 {image_pdf} · 失败 {failed}")

    # ---- 4. 抽取 ----
    def _s4_extract(self) -> None:
        self._step(4, "事实抽取", "start")
        names = self._names()
        year = time.localtime().tm_year
        rules = self.event_rules or EX.rules_for_version(self.event_rules_version)
        self.stats["event_rules_version"] = self.event_rules_version or "baseline"
        raw: list[EX.Fact] = []
        for d in self.docs:
            self._tick()
            try:
                f = EX.build_fact(d, names, year, rules=rules)
            except Exception:
                f = None
            if f:
                raw.append(f)
        self._log(f"候选事实 {len(raw)} 条（已过滤例行公告与非主体相关资讯）")

        merged_all = EX.merge_facts(raw)
        conflict_all = [f for f in merged_all if f.conflict]
        dropped = len(raw) - len(merged_all)
        if dropped:
            note = f"，其中 {len(conflict_all)} 条存在口径冲突" if conflict_all else ""
            self._log(f"跨源合并：{dropped} 条重复报道并入同一事件，佐证数累加{note}")

        doc_by_id = {d["id"]: d for d in self.docs}
        merged = [f for f in merged_all if f.confidence >= self.cfg.min_fact_conf]
        merged.sort(key=lambda f: (-f.confidence, f.event_date or ""))
        merged = merged[:self.cfg.max_facts]
        # 冲突导致置信度跌破阈值的，不能连冲突一起消失：留给缺口清单说明
        self.dropped_conflicts = [f.as_dict() for f in conflict_all if f not in merged]
        self.stats["conflicts"] = len(conflict_all)

        out = []
        for i, f in enumerate(merged, 1):
            did = f.doc_id
            doc = doc_by_id.get(did, {})
            out.append({
                "id": f"F{i:02d}",
                **f.as_dict(),
                "source": doc.get("source", ""),
                "url": doc.get("url", ""),
                "published_at": doc.get("published_at", ""),
                "evidence_level": doc.get("evidence_level", "") if doc else "",
                "kind": doc.get("kind", "") if doc else "",
                "doc_title": doc.get("title", "") if doc else "",
            })
        self.facts = out
        # 第 4 步即落库：第 5～7 步是本地计算与成稿，中断后从检查点恢复不必重抓
        store.save_facts(self.run_id, self.entity_key, out)
        self._save_cp(4)
        self._log(f"入库事实 {len(out)} 条，平均置信度 "
                  f"{(sum(f['confidence'] for f in out) / len(out)):.2f}" if out else "未抽到满足阈值的事实", "ok" if out else "warn")
        self._step(4, "事实抽取", "done", f"{len(out)} 条事实")

    # ---- 5. 变化 ----
    def _s5_diff(self) -> None:
        self._step(5, "变化比对", "start")
        prev = store.last_snapshot(self.entity_key, exclude_run=self.run_id)
        self.changes = diff.compare(self.facts, prev["facts"] if prev else None)
        if prev:
            self.changes["baseline_run_at"] = prev.get("run_at")
            self._log(f"对比上次（{prev.get('run_at')}）：新增 {len(self.changes['new'])}，"
                      f"升温 {len(self.changes['escalated'])}，本轮未见 {len(self.changes['faded'])}")
        else:
            self._log("首次运行，无历史基线；不做新增/升温判断")
        self._step(5, "变化比对", "done",
                   "首次运行" if not prev else f"对比 {prev.get('run_at')}")

    # ---- 6. 成稿 ----
    def _s6_compose(self) -> None:
        self._step(6, "报告成稿", "start")
        # 记忆只作口径参考：召回结果记录在 stats，不进入事实清单与报告引用
        mems = []
        try:
            mems = memory.recall(f"{self.company} {self.profile.get('industry') or ''}",
                                 scope=self.entity_key, limit=5)
            if mems:
                self._log(f"记忆召回 {len(mems)} 条（仅作口径参考，不进入事实）")
        except Exception as e:
            self._log(f"记忆召回失败：{type(e).__name__}", "warn")
        self.stats["memory_hits"] = len(mems)
        rule_judges = R.judgments(self.facts, self.profile)
        judges, judge_meta = R.apply_polish(rule_judges, None)
        model_used = ""
        rejected: list[dict] = []
        polished: dict | None = None
        if llm.available():
            self._log(f"调用模型润色判断段：{self.cfg.llm_model}")
            polished = llm.polish_judgments(rule_judges, self.facts)
            rejected = (polished or {}).get("rejected") or []
            judges, judge_meta = R.apply_polish(rule_judges, polished)
            if judge_meta["llm"]:
                model_used = self.cfg.llm_model
                self._log("模型润色完成，引用、数字与表述重合度均已校验", "ok")
            elif polished:
                self._log(f"模型改写未全部通过校验，回退规则判断（拦截 {len(rejected)} 条）", "warn")
                for item in rejected[:3]:
                    self._log(f"拦截：{item.get('text', '')[:40]}（{item.get('reason', '')}）", "warn")
            else:
                self._log("模型返回不合规（引用缺失或格式错误），回退规则判断", "warn")
        judge_meta["rejected"] = len(rejected)
        judge_meta["trail"] = self._write_judge_trail(
            rule_judges, judges, rejected, model_used,
            str((polished or {}).get("raw") or "") if llm.available() else "")

        gaps_list = R.gaps(self.facts, self.docs, self.changes, self.entity,
                           dropped_conflicts=self.dropped_conflicts)
        sug = R.suggestions(judges, self.facts, gaps_list)
        self.report_md = R.render_markdown(
            company=self.company, entity=self.entity, profile=self.profile, quote=self.quote,
            facts=self.facts, docs=self.docs, changes=self.changes, gaps_list=gaps_list, sug=sug,
            judges=judges, judge_meta=judge_meta,
            run_meta={"days": self.cfg.days, "run_id": self.run_id,
                      "finished_at": _now(), "model_used": model_used},
        )
        # 推送摘要需要最终判断与缺口；自动推送在第 7 步末尾执行
        self.judges = judges
        self.gaps = gaps_list
        self.stats.update({
            "docs": len(self.docs),
            "announcements": sum(1 for d in self.docs if d["kind"] == "announcement"),
            "news": sum(1 for d in self.docs if d["kind"] == "news"),
            "full_text": sum(1 for d in self.docs if d.get("evidence_level") == "full"),
            "facts": len(self.facts),
            "high_conf": sum(1 for f in self.facts if f["confidence"] >= 0.85),
            "unlocatable": sum(1 for f in self.facts if not f.get("doc_id")),
            "conflicts": self.stats.get("conflicts", 0),
            "judgments_rule": judge_meta["rule"],
            "judgments_llm": judge_meta["llm"],
            "judgments_rejected": judge_meta["rejected"],
            "model_used": model_used or "rule-engine",
            "new": len(self.changes.get("new", [])),
            "escalated": len(self.changes.get("escalated", [])),
        })
        self._step(6, "报告成稿", "done", f"{len(judges)} 条判断")

    def _write_judge_trail(self, rule: list[dict], final: list[dict],
                           rejected: list[dict], model: str, raw: str = "") -> str:
        """判断改写前后版本落盘并入库（不依赖模型是否启用），供事后对比与复核。"""
        name = f"judgments_{self.run_id}.json"
        payload = {"run_id": self.run_id, "model": model or "rule-engine",
                   "requested": len(rule),
                   "accepted": sum(1 for j in final if j.get("source") == "llm"),
                   "rule": rule, "final": final, "rejected": rejected}
        if raw:
            payload["raw"] = raw
        try:
            (OUT_DIR / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
        except OSError as e:
            self._log(f"判断留痕写入失败：{type(e).__name__}", "warn")
        try:
            store.save_judgments(self.run_id, payload)
        except Exception as e:  # noqa: BLE001
            self._log(f"判断留痕入库失败：{type(e).__name__}", "warn")
        return name

    # ---- 7. 入库 ----
    def _s7_persist(self) -> None:
        self._step(7, "留痕入库", "start")
        # 第 4 步已经落过事实；这里再整批替换一次，兼容从第 5 步之后恢复的执行
        store.save_facts(self.run_id, self.entity_key, self.facts)
        store.save_snapshot(self.entity_key, self.run_id, self.facts)
        store.add_audit(self.entity_key, self.run_id, "run", self.run_id,
                        reason=f"自动生成，{len(self.facts)} 条事实", actor="Agent")

        # 报告与事实表统一在这里落盘：无论从 CLI 还是网页触发，产物路径都一致。
        # 早先只有 CLI 写文件，导致网页跑完没有产物、历史运行也无法回看报告。
        md_path = OUT_DIR / f"{self.company}_{self.run_id}.md"
        csv_path = OUT_DIR / f"{self.company}_{self.run_id}.csv"
        try:
            md_path.write_text(self.report_md, encoding="utf-8")
            csv_path.write_text(facts_csv(self.facts), encoding="utf-8")
        except Exception as e:
            self.errors.append(f"报告落盘失败：{type(e).__name__}: {e}")
        self._step(7, "留痕入库", "done",
                   f"{len(self.facts)} 条事实已落库，报告已写入 output/")
        self._push_report()

    def _push_report(self) -> None:
        """自动推送报告摘要：失败只记日志与 errors，不影响运行完成状态。"""
        try:
            from ..infra import push
            if not push.auto_enabled():
                return
            title, text = R.push_digest(self.company, self.run_id, self.facts,
                                        self.judges, self.gaps, push.base_url())
            results = push.send_all(title, text)
            ok = sum(1 for r in results if r.get("ok"))
            store.add_audit(self.entity_key, self.run_id, "push", f"{ok}/{len(results)}",
                            reason="自动推送报告摘要", actor="Agent")
            self._log(f"报告推送完毕：{ok}/{len(results)} 个渠道",
                      "ok" if results and ok == len(results) else "warn")
            for r in results:
                if not r.get("ok"):
                    self._log(f"推送失败（{r.get('name')}）：{r.get('error', '')[:80]}", "warn")
        except Exception as e:  # noqa: BLE001 —— 推送失败不能把已完成的运行拖成 failed
            self.errors.append(f"报告推送失败：{type(e).__name__}: {e}")
            self._log(f"报告推送失败：{type(e).__name__}", "warn")


def run(company: str, cfg=None, on_event=None, run_id: str | None = None,
        resume: bool = False, cancel_check=None) -> dict:
    return Pipeline(company, cfg=cfg, on_event=on_event, run_id=run_id,
                    resume=resume, cancel_check=cancel_check).run()


# ---------------- 导出 ----------------

def facts_csv(facts: list[dict]) -> str:
    head = ["编号", "类型", "事实陈述", "金额", "事件日期", "置信度", "佐证源数",
            "口径冲突说明", "来源", "证据等级", "发布时间", "证据句", "原文链接"]
    lines = [",".join(head)]
    for f in facts:
        row = [f["id"], f["type_label"], f["statement"], f.get("amount", ""),
               f.get("event_date", ""), f"{f['confidence']:.2f}", str(f.get("corroboration", 1)),
               f.get("conflict_note", ""),
               f.get("source", ""), f.get("evidence_level", ""), f.get("published_at", ""),
               f.get("evidence_quote", ""), f.get("url", "")]
        lines.append(",".join('"' + str(c).replace('"', '""') + '"' for c in row))
    return "\ufeff" + "\r\n".join(lines)
