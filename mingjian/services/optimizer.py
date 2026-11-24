"""离线候选评测、门禁与上线/回滚。

这里实现的是受控优化，不是在线自改：

    候选 JSON（只允许增量关键词）
      → 冻结评测集
      → 基线 / 候选在同一批样本上对照
      → 硬门禁 + 分片检查
      → 写入版本库并切换 active 指针
      → 下一轮运行读取新版本，历史运行记录原版本

候选不执行代码、不生成 SQL、不替换完整规则集。模型或人可以提出候选，
但是否上线只由离线评测和门禁决定；评测不过不会改动任何生效规则。
"""
from __future__ import annotations

import hashlib
import json
import pathlib

from ..infra import optstore
from . import extract

KIND = "event_rules"
MIN_CASES = 12
MIN_HOLDOUT = 4
MAX_KEYWORDS_PER_TYPE = 20
MAX_KEYWORDS_TOTAL = 60
MAX_KEYWORD_LEN = 40
MAX_RECALL_DROP = 0.15
MIN_MACRO_DELTA = -0.01
MAX_HOLDOUT_DROP = 0.02
MAX_OTHER_RATE_DELTA = 0.05

_ALLOWED_FIELDS = {"kind", "version", "base_version", "description", "add_keywords"}


def _split(value) -> str:
    s = str(value or "dev").strip().lower()
    if s not in ("dev", "holdout"):
        raise ValueError("评测样本的 split 只能是 dev 或 holdout")
    return s


def load_cases(path) -> list[dict]:
    """读取 JSON / JSONL 评测集。

    每条至少要有 title、expected；body 可选，split 为 dev 或 holdout。
    评测集是人工标注资产，不从这里生成标准答案。
    """
    p = pathlib.Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        raise ValueError(f"无法读取评测集：{p}（{type(e).__name__}）") from e
    if not text.strip():
        raise ValueError("评测集为空")
    items = None
    try:
        payload = json.loads(text)
    except ValueError:
        payload = None
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict) and isinstance(payload.get("cases"), list):
        items = payload["cases"]
    else:
        items = []
        for lineno, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except ValueError as e:
                raise ValueError(f"评测集第 {lineno} 行不是合法 JSON") from e
            if not isinstance(item, dict):
                raise ValueError(f"评测集第 {lineno} 行必须是对象")
            items.append(item)
    if not isinstance(items, list) or not items:
        raise ValueError("评测集没有可用样本")

    out: list[dict] = []
    seen_ids: set[str] = set()
    for i, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise ValueError(f"第 {i} 条评测样本必须是对象")
        title = str(item.get("title") or "").strip()
        body = str(item.get("body") or "")[:4000]
        expected = str(item.get("expected") or item.get("label") or "").strip()
        if not title:
            raise ValueError(f"第 {i} 条评测样本缺少 title")
        if not expected or (expected not in extract.LABEL and expected != "other"):
            raise ValueError(f"第 {i} 条评测样本的 expected 不是合法事件类型")
        cid = str(item.get("id") or f"C{i:03d}").strip()
        if cid in seen_ids:
            raise ValueError(f"评测样本编号重复：{cid}")
        seen_ids.add(cid)
        out.append({"id": cid, "title": title[:300], "body": body,
                    "expected": expected, "split": _split(item.get("split"))})
    return out


def validate_candidate(data: dict) -> dict:
    """只接受声明式增量候选，拒绝代码、完整替换和未知字段。"""
    if not isinstance(data, dict):
        raise ValueError("候选必须是一个 JSON 对象")
    unknown = set(data) - _ALLOWED_FIELDS
    if unknown:
        raise ValueError(f"候选包含不支持的字段：{', '.join(sorted(unknown))}")
    if data.get("kind") != KIND:
        raise ValueError(f"候选 kind 必须是 {KIND}")
    version = optstore.safe_version(str(data.get("version") or ""))
    base_version = str(data.get("base_version") or "").strip()
    if base_version:
        base_version = optstore.safe_version(base_version)
        if base_version == version:
            raise ValueError("候选版本不能与基线版本相同")
    description = str(data.get("description") or "").strip()
    if len(description) > 200:
        raise ValueError("候选说明不能超过 200 字")

    raw_add = data.get("add_keywords")
    if not isinstance(raw_add, dict) or not raw_add:
        raise ValueError("候选必须提供非空的 add_keywords")
    add: dict[str, list[str]] = {}
    seen_words: set[str] = set()
    total = 0
    for key, words in raw_add.items():
        key = str(key or "").strip()
        if key == "other" or key not in extract.LABEL:
            raise ValueError(f"add_keywords 的事件类型不合法：{key}")
        if not isinstance(words, (list, tuple)) or not words:
            raise ValueError(f"{key} 的关键词必须是非空数组")
        if len(words) > MAX_KEYWORDS_PER_TYPE:
            raise ValueError(f"{key} 一次最多新增 {MAX_KEYWORDS_PER_TYPE} 个关键词")
        clean: list[str] = []
        for word in words:
            if not isinstance(word, str):
                raise ValueError(f"{key} 的关键词必须是字符串")
            w = word.strip()
            if not w or len(w) > MAX_KEYWORD_LEN or any(ch in w for ch in "\r\n\t"):
                raise ValueError(f"{key} 的关键词不合法：{word!r}")
            if w in seen_words:
                raise ValueError(f"关键词重复：{w}")
            seen_words.add(w)
            clean.append(w)
        total += len(clean)
        add[key] = clean
    if total > MAX_KEYWORDS_TOTAL:
        raise ValueError(f"候选关键词总数不能超过 {MAX_KEYWORDS_TOTAL}")
    return {"kind": KIND, "version": version, "base_version": base_version,
            "description": description, "add_keywords": add}


def load_candidate(path) -> dict:
    p = pathlib.Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except OSError as e:
        raise ValueError(f"无法读取候选：{p}（{type(e).__name__}）") from e
    except ValueError as e:
        raise ValueError(f"候选不是合法 JSON：{p}") from e
    return validate_candidate(data)


def _base_rules(base_rules=None) -> list[dict]:
    rules = extract.normalize_rules(base_rules) if base_rules is not None else None
    return rules or extract.active_rules()


def _base_version(base_version=None) -> str:
    return str(base_version or extract.active_version() or "baseline")


def resolve_candidate(candidate: dict, base_rules: list[dict]) -> tuple[list[dict], dict]:
    """把增量候选应用到基线规则，返回完整规则和真正新增的关键词。"""
    base = extract.normalize_rules(base_rules)
    if not base:
        raise ValueError("基线规则无效，拒绝生成候选规则")
    by_type = {r["type"]: {"type": r["type"], "label": r["label"],
                           "keywords": list(r["keywords"])} for r in base}
    added: dict[str, list[str]] = {}
    for key, words in candidate["add_keywords"].items():
        bucket = by_type.get(key)
        if not bucket:
            raise ValueError(f"基线规则没有事件类型：{key}")
        fresh = []
        for word in words:
            if word not in bucket["keywords"]:
                bucket["keywords"].append(word)
                fresh.append(word)
        if fresh:
            added[key] = fresh
    if not added:
        raise ValueError("候选没有新增任何关键词（与当前生效规则完全重复）")
    return [by_type[key] for key, _, _ in extract.EVENT_TYPES], added


def _digest(candidate: dict, rules: list[dict]) -> str:
    raw = json.dumps({"candidate": candidate, "rules": rules}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _predict(case: dict, rules: list[dict]) -> tuple[str, float]:
    return extract.classify(case["title"], case["body"], rules)


def _summary(rows: list[dict]) -> dict:
    labels = list(extract.LABEL) + ["other"]
    n = len(rows)
    correct = sum(1 for r in rows if r["expected"] == r["predicted"])
    per_label = {}
    for label in labels:
        support = sum(1 for r in rows if r["expected"] == label)
        predicted = sum(1 for r in rows if r["predicted"] == label)
        tp = sum(1 for r in rows if r["expected"] == label and r["predicted"] == label)
        fp = predicted - tp
        fn = support - tp
        precision = tp / predicted if predicted else 0.0
        recall = tp / support if support else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_label[label] = {"support": support, "predicted": predicted, "tp": tp,
                            "fp": fp, "fn": fn, "precision": precision,
                            "recall": recall, "f1": f1}
    active_labels = [k for k, v in per_label.items() if v["support"] or v["predicted"]]
    macro_f1 = sum(per_label[k]["f1"] for k in active_labels) / len(active_labels) if active_labels else 0.0
    return {
        "total": n,
        "correct": correct,
        "accuracy": correct / n if n else 0.0,
        "macro_f1": macro_f1,
        "other_rate": sum(1 for r in rows if r["predicted"] == "other") / n if n else 0.0,
        "per_label": per_label,
    }


def _run(cases: list[dict], rules: list[dict]) -> tuple[dict, list[dict]]:
    rows = []
    for case in cases:
        predicted, strength = _predict(case, rules)
        rows.append({"id": case["id"], "title": case["title"], "split": case["split"],
                     "expected": case["expected"], "predicted": predicted,
                     "strength": round(strength, 3)})
    return _summary(rows), rows


def _gate(base: dict, cand: dict, base_rows: list[dict], cand_rows: list[dict],
          cases: list[dict]) -> dict:
    total = len(cases)
    holdout_n = sum(1 for c in cases if c["split"] == "holdout")
    base_ok = {r["id"] for r in base_rows if r["expected"] == r["predicted"]}
    cand_ok = {r["id"] for r in cand_rows if r["expected"] == r["predicted"]}
    new_errors = [r for r in cand_rows if r["id"] not in cand_ok and r["id"] in base_ok]
    fixed = [r for r in cand_rows if r["id"] in cand_ok and r["id"] not in base_ok]

    base_h = _summary([r for r in base_rows if r["split"] == "holdout"])
    cand_h = _summary([r for r in cand_rows if r["split"] == "holdout"])
    drop_labels = []
    for label, b in base["per_label"].items():
        if b["support"]:
            c = cand["per_label"][label]
            if b["recall"] - c["recall"] > MAX_RECALL_DROP:
                drop_labels.append(label)

    checks = [
        {"name": "样本量", "ok": total >= MIN_CASES and holdout_n >= MIN_HOLDOUT,
         "detail": f"{total} 条（保留集 {holdout_n}）"},
        {"name": "无新增错误", "ok": not new_errors,
         "detail": f"新增错误 {len(new_errors)} 条，修正 {len(fixed)} 条"},
        {"name": "整体准确率", "ok": cand["accuracy"] >= base["accuracy"],
         "detail": f"{base['accuracy']:.1%} → {cand['accuracy']:.1%}"},
        {"name": "宏平均 F1", "ok": cand["macro_f1"] >= base["macro_f1"] + MIN_MACRO_DELTA,
         "detail": f"{base['macro_f1']:.3f} → {cand['macro_f1']:.3f}"},
        {"name": "保留集不退化", "ok": cand_h["accuracy"] >= base_h["accuracy"] - MAX_HOLDOUT_DROP,
         "detail": f"{base_h['accuracy']:.1%} → {cand_h['accuracy']:.1%}"},
        {"name": "分类型召回", "ok": not drop_labels,
         "detail": "无类型下降超过 15%" if not drop_labels else f"下降超过阈值：{', '.join(drop_labels)}"},
        {"name": "other 比例", "ok": cand["other_rate"] <= base["other_rate"] + MAX_OTHER_RATE_DELTA,
         "detail": f"{base['other_rate']:.1%} → {cand['other_rate']:.1%}"},
    ]
    return {"passed": all(c["ok"] for c in checks), "checks": checks,
            "new_errors": len(new_errors), "fixed": len(fixed),
            "thresholds": {"min_cases": MIN_CASES, "min_holdout": MIN_HOLDOUT,
                           "max_recall_drop": MAX_RECALL_DROP,
                           "min_macro_delta": MIN_MACRO_DELTA,
                           "max_holdout_drop": MAX_HOLDOUT_DROP,
                           "max_other_rate_delta": MAX_OTHER_RATE_DELTA}}


def _errors(base_rows: list[dict], cand_rows: list[dict]) -> dict:
    base_ok = {r["id"] for r in base_rows if r["expected"] == r["predicted"]}
    cand_ok = {r["id"] for r in cand_rows if r["expected"] == r["predicted"]}
    base_err = [r for r in base_rows if r["id"] not in base_ok]
    cand_err = [r for r in cand_rows if r["id"] not in cand_ok]
    new = [r for r in cand_err if r["id"] in base_ok]
    fixed = [r for r in cand_rows if r["id"] in cand_ok and r["id"] not in base_ok]

    def clean(rows):
        return [{"id": r["id"], "split": r["split"], "title": r["title"],
                 "expected": r["expected"], "predicted": r["predicted"]} for r in rows[:50]]

    return {"baseline": clean(base_err), "candidate": clean(cand_err),
            "new": clean(new), "fixed": clean(fixed)}


def evaluate(candidate: dict, cases: list[dict], base_rules=None, base_version=None,
             actor: str = "offline") -> dict:
    """离线对照评测并把完整结果写入 data/optimizer/evaluations/。"""
    candidate = validate_candidate(candidate)
    base_rules = _base_rules(base_rules)
    base_version = _base_version(base_version)
    if candidate["base_version"] and candidate["base_version"] != base_version:
        raise ValueError(f"候选基于 {candidate['base_version']}，当前基线是 {base_version}")
    resolved, added = resolve_candidate(candidate, base_rules)

    base_metrics, base_rows = _run(cases, base_rules)
    cand_metrics, cand_rows = _run(cases, resolved)
    gate = _gate(base_metrics, cand_metrics, base_rows, cand_rows, cases)
    splits = sorted({c["split"] for c in cases})
    by_split = {}
    for split in splits:
        b = _summary([r for r in base_rows if r["split"] == split])
        c = _summary([r for r in cand_rows if r["split"] == split])
        by_split[split] = {"baseline": b, "candidate": c}

    report = {
        "kind": KIND,
        "candidate": {"version": candidate["version"], "base_version": base_version,
                      "description": candidate["description"],
                      "digest": _digest(candidate, resolved), "added": added},
        "baseline": {"version": base_version, "metrics": base_metrics},
        "candidate_metrics": cand_metrics,
        "by_split": by_split,
        "gate": gate,
        "errors": _errors(base_rows, cand_rows),
        "case_count": len(cases),
        "evaluated_at": optstore.now(),
    }
    path = optstore.save_evaluation(candidate["version"], report)
    report["evaluation_file"] = str(path)
    optstore.append_audit({
        "action": "evaluate", "actor": actor, "version": candidate["version"],
        "base_version": base_version, "passed": gate["passed"],
        "accuracy": round(cand_metrics["accuracy"], 4),
        "macro_f1": round(cand_metrics["macro_f1"], 4),
        "report": path.name,
    })
    return report


def evaluate_file(candidate_path, cases_path, base_rules=None, base_version=None,
                  actor: str = "offline") -> dict:
    return evaluate(load_candidate(candidate_path), load_cases(cases_path),
                    base_rules=base_rules, base_version=base_version, actor=actor)


def evaluate_cases(cases_path) -> dict:
    """只跑基线，用于检查评测集与当前规则，不生成候选。"""
    cases = load_cases(cases_path)
    rules = extract.active_rules()
    metrics, rows = _run(cases, rules)
    return {"version": extract.active_version(), "metrics": metrics,
            "case_count": len(cases), "errors": _errors(rows, rows)["baseline"]}


def promote(candidate_path, cases_path, actor: str = "cli") -> dict:
    """重新评测并门禁；只有通过才写版本库和 active 指针。"""
    candidate = load_candidate(candidate_path)
    cases = load_cases(cases_path)
    base_rules = extract.active_rules()
    base_version = extract.active_version()
    report = evaluate(candidate, cases, base_rules=base_rules,
                      base_version=base_version, actor=actor)
    if not report["gate"]["passed"]:
        failed = [c["name"] for c in report["gate"]["checks"] if not c["ok"]]
        raise ValueError(f"门禁未通过：{', '.join(failed)}；详见 {report['evaluation_file']}")
    if candidate["version"] == base_version:
        raise ValueError("候选版本与当前生效版本相同，不能重复上线")
    resolved, _added = resolve_candidate(candidate, base_rules)
    now = optstore.now()
    payload = {
        "kind": KIND,
        "version": candidate["version"],
        "base_version": base_version,
        "promoted_at": now,
        "actor": actor,
        "source": pathlib.Path(candidate_path).name,
        "evaluation": {"file": pathlib.Path(report["evaluation_file"]).name,
                       "digest": report["candidate"]["digest"]},
        "rules": resolved,
    }

    previous = optstore.load_active()
    if isinstance(previous, dict):
        prev_version = str(previous.get("version") or "")
        if prev_version and prev_version != "baseline" and extract.normalize_rules(previous.get("rules")):
            try:
                optstore.archive_version(prev_version, previous)
            except FileExistsError:
                pass
    optstore.archive_version(candidate["version"], payload)
    optstore.save_active(payload)
    optstore.append_audit({
        "action": "promote", "actor": actor, "version": candidate["version"],
        "base_version": base_version, "passed": True,
        "accuracy": round(report["candidate_metrics"]["accuracy"], 4),
        "report": pathlib.Path(report["evaluation_file"]).name,
    })
    return {"version": candidate["version"], "base_version": base_version,
            "report": report, "active": payload}


def rollback(version: str, actor: str = "cli") -> dict:
    """切回已归档版本；版本文件保留，不删除历史。"""
    target = optstore.safe_version(version)
    current = extract.active_version()
    if target == "baseline":
        rules = extract.default_rules()
        base_version = current
    else:
        payload = optstore.load_version(target)
        if not isinstance(payload, dict):
            raise ValueError(f"找不到已归档版本：{target}")
        rules = extract.normalize_rules(payload.get("rules"))
        if not rules:
            raise ValueError(f"版本 {target} 的规则无效，拒绝回滚")
        base_version = current
    new_payload = {
        "kind": KIND, "version": target, "base_version": base_version,
        "promoted_at": optstore.now(), "actor": actor,
        "rolled_back": True, "rules": rules,
    }
    optstore.save_active(new_payload)
    optstore.append_audit({"action": "rollback", "actor": actor, "version": target,
                           "from_version": current})
    return {"version": target, "from_version": current}


def status(limit: int = 5) -> dict:
    active = optstore.load_active()
    rules = extract.normalize_rules(active.get("rules")) if isinstance(active, dict) else None
    version = str(active.get("version") or "baseline") if rules else "baseline"
    active_meta = {
        "version": version,
        "base_version": str(active.get("base_version") or "") if rules else "",
        "promoted_at": str(active.get("promoted_at") or "") if rules else "",
        "actor": str(active.get("actor") or "") if rules else "",
        "rules": len(rules) if rules else len(extract.default_rules()),
        "keywords": sum(len(r["keywords"]) for r in rules) if rules else
                    sum(len(r["keywords"]) for r in extract.default_rules()),
    }
    evals = sorted(optstore.evaluations_dir().glob("*.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    return {"active": active_meta, "versions": len(optstore.list_versions()),
            "evaluations": [p.name for p in evals],
            "audit": optstore.read_audit(limit)}


def history(limit: int = 20) -> dict:
    evals = sorted(optstore.evaluations_dir().glob("*.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    return {"versions": optstore.list_versions(),
            "evaluations": [p.name for p in evals],
            "audit": optstore.read_audit(limit)}