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
import re
import time

from ..config import CFG, OUT_DIR
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
MAX_LABEL_SHIFT = 0.10

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


def _label_shift(base: dict, cand: dict, total: int) -> list[dict]:
    """预测分布偏移超过阈值的类型；允许条数随样本量放宽，最少 2 条。

    防的不是修正本身，而是把一批样本从一类成批推到另一类：类型召回下滑已
    有单独检查，这项盯的是预测结果的结构突变。
    """
    allowed = max(2, int(round(total * MAX_LABEL_SHIFT)))
    out = []
    for label, b in base["per_label"].items():
        shift = cand["per_label"][label]["predicted"] - b["predicted"]
        if abs(shift) > allowed:
            out.append({"label": label, "shift": shift})
    return out


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
    shift = _label_shift(base, cand, total)

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
        {"name": "类型分布", "ok": not shift,
         "detail": "无类型偏移超过允许条数" if not shift else
                   "偏移：" + "，".join(f"{row['label']} {row['shift']:+d}" for row in shift)},
        {"name": "other 比例", "ok": cand["other_rate"] <= base["other_rate"] + MAX_OTHER_RATE_DELTA,
         "detail": f"{base['other_rate']:.1%} → {cand['other_rate']:.1%}"},
    ]
    return {"passed": all(c["ok"] for c in checks), "checks": checks,
            "new_errors": len(new_errors), "fixed": len(fixed),
            "thresholds": {"min_cases": MIN_CASES, "min_holdout": MIN_HOLDOUT,
                           "max_recall_drop": MAX_RECALL_DROP,
                           "min_macro_delta": MIN_MACRO_DELTA,
                           "max_holdout_drop": MAX_HOLDOUT_DROP,
                           "max_label_shift": MAX_LABEL_SHIFT,
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


# ---------------------------------------------------------------- 草案生成

# 草案只从评测集判错样本里抽词，产物是候选 JSON，不直接上线：仍需人工复核，
# 再走 evaluate / promote 的同一套门禁。抽词是规则行为，不让模型参与。
_STOP_WORDS = {
    "某某", "企业", "公司", "集团", "股份", "有限", "控股", "实业", "科技",
    "签订", "签署", "获得", "关于", "公告", "通知", "本次", "上述", "已经",
    "拟于", "甲乙", "丙方", "以及", "实现", "完成", "进行", "同比", "环比",
    "相关", "有关", "目前", "近日", "记者", "一个", "两个", "多个",
}
_STOP_CHARS = set("某的了与及并在为是将已就都还又其该本把被从对和等之于得地着过很更最各每")


def _bad_word(text: str) -> bool:
    if not text or any(ch in _STOP_CHARS for ch in text):
        return True
    return any(text[i:i + 2] in _STOP_WORDS for i in range(len(text) - 1))


def _phrases(text: str) -> list[str]:
    """按双字切分再拼接相邻双字，产出 2—4 字片段，长词优先。

    中文短标题里双字对齐通常正好落在词边界上；逐字滑窗会产出「业签订联」
    这类跨词碎片，看起来像命中，实际毫无泛化价值。
    """
    out: list[str] = []
    for chunk in re.findall(r"[\u4e00-\u9fa5]+", text or ""):
        for offset in (0, 1):
            grams = [chunk[i:i + 2] for i in range(offset, len(chunk) - 1, 2)]
            for i, gram in enumerate(grams):
                if len(gram) != 2 or _bad_word(gram):
                    continue
                if i + 1 < len(grams) and not _bad_word(grams[i + 1]):
                    joined = gram + grams[i + 1]
                    if not _bad_word(joined):
                        out.append(joined)
                out.append(gram)
    return list(dict.fromkeys(out))


def _acceptable(word: str, known: set[str]) -> bool:
    """拒绝已有词、已有词的真子串和长度越界的片段。"""
    if not 2 <= len(word) <= 4:
        return False
    for kw in known:
        if word == kw:
            return False
        if len(word) < len(kw) and word in kw:
            return False
    return True


def _screen(word: str, target: str, cases: list[dict], rules: list[dict],
            base_rows: list[dict], source: int) -> bool:
    """加入一个词后只重跑受影响的样本：必须修好来源样本，且不弄坏基线判对的样本。"""
    trial = [{"type": r["type"], "label": r["label"], "keywords": list(r["keywords"])}
             for r in rules]
    for rule in trial:
        if rule["type"] == target:
            rule["keywords"].append(word)
            break
    for i, case in enumerate(cases):
        if word not in case["title"] and word not in case["body"][:1200]:
            continue
        predicted, _ = extract.classify(case["title"], case["body"], trial)
        if i == source:
            if predicted != case["expected"]:
                return False
        elif base_rows[i]["predicted"] == case["expected"] and predicted != case["expected"]:
            return False
    return True


def suggest(cases: list[dict], base_rules: list[dict],
            max_per_type: int = 6) -> tuple[dict[str, list[str]], list[dict]]:
    """按判错样本的事件类型抽词，每条错误最多贡献一个词。"""
    rules = extract.normalize_rules(base_rules)
    if not rules:
        raise ValueError("基线规则无效，无法生成草案")
    _, base_rows = _run(cases, rules)
    known = {kw for rule in rules for kw in rule["keywords"]}
    limit = max(1, min(int(max_per_type), MAX_KEYWORDS_PER_TYPE))
    picked: dict[str, list[str]] = {}
    reviewed: list[dict] = []
    for i, case in enumerate(cases):
        row = base_rows[i]
        if row["predicted"] == case["expected"]:
            continue
        target = case["expected"]
        bucket = picked.setdefault(target, [])
        if len(bucket) >= limit:
            continue
        word = ""
        for text in (case["title"], case["body"][:300]):
            for item in _phrases(text):
                if item in bucket or not _acceptable(item, known):
                    continue
                if _screen(item, target, cases, rules, base_rows, i):
                    word = item
                    break
            if word:
                break
        if not word:
            continue
        bucket.append(word)
        known.add(word)
        for rule in rules:                      # 后续词按累积效果筛选
            if rule["type"] == target:
                rule["keywords"].append(word)
                break
        reviewed.append({"id": case["id"], "expected": target,
                         "predicted": row["predicted"], "word": word})
    return {k: v for k, v in picked.items() if v}, reviewed


def draft(cases_path, out_dir, base_rules=None, base_version=None,
          version: str = "", description: str = "", max_per_type: int = 6) -> dict:
    """从评测集判错样本生成候选草案，只落盘，不改生效规则。"""
    cases = load_cases(cases_path)
    rules = _base_rules(base_rules)
    base_version = _base_version(base_version)
    picked, reviewed = suggest(cases, rules, max_per_type=max_per_type)
    if not picked:
        raise ValueError("评测集里没有可抽取的判错样本，或候选词都过不了筛选")
    ver = optstore.safe_version(version or "draft-" + time.strftime("%Y%m%d-%H%M%S"))
    candidate = validate_candidate({
        "kind": KIND,
        "version": ver,
        "base_version": base_version,
        "description": description or "由离线评测判错样本自动抽取的候选草案",
        "add_keywords": picked,
    })
    path = pathlib.Path(out_dir) / f"{ver}.json"
    if path.exists() and version:
        raise ValueError(f"草案文件已存在：{path.name}")
    optstore.write_json(path, candidate)
    return {"candidate": candidate, "path": str(path), "base_version": base_version,
            "cases": len(cases), "errors": len(reviewed),
            "suggested": picked, "reviewed": reviewed}


def _norm_title(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _known_samples(path) -> tuple[set[str], set[str]]:
    """宽松读取已有样本的标题与编号。

    不能用 load_cases：待标注文件的 expected 是空的，而这里只需要去重用的
    标题和编号。
    """
    p = pathlib.Path(path)
    if not p.exists():
        return set(), set()
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return set(), set()
    try:
        payload = json.loads(text)
    except ValueError:
        payload = None
    items: list = []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict) and isinstance(payload.get("cases"), list):
        items = payload["cases"]
    else:
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                items.append(json.loads(line))
            except ValueError:
                continue
    titles: set[str] = set()
    ids: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        title = _norm_title(str(item.get("title") or ""))
        if title:
            titles.add(title)
        cid = str(item.get("id") or "").strip()
        if cid:
            ids.add(cid)
    return titles, ids


def harvest(out_dir, limit: int = 200, entity_key: str = "", prefix: str = "H",
            split: str = "dev", cases_path=None) -> dict:
    """把运行期事实导出成待标注评测样本，expected 一律留空。

    标准答案只能由人读原文后填写。`hint.predicted` 是运行期判定，只用于对照
    与发现分歧，不能直接当 expected——否则评测集就变成了模型的回声。
    """
    from ..infra import store  # 只有 harvest 需要数据库，其余 optimize 命令离线

    split = _split(split)
    try:
        store.init()
        rows = store.facts_with_doc(limit=limit, entity_key=entity_key)
    except Exception as e:
        raise ValueError(f"读取运行事实失败：{type(e).__name__}") from e
    seen_titles, seen_ids = _known_samples(cases_path) if cases_path else (set(), set())
    skipped = {"duplicate": 0, "no_title": 0, "routine": 0}
    items: list[dict] = []
    seq = 0
    for row in rows:
        title = str(row.get("doc_title") or "").strip()
        if not title:
            skipped["no_title"] += 1
            continue
        key = _norm_title(title)
        if key in seen_titles:
            skipped["duplicate"] += 1
            continue
        if extract.ROUTINE.search(title):
            skipped["routine"] += 1
            continue
        seen_titles.add(key)
        seq += 1
        cid = f"{prefix}{seq:04d}"
        while cid in seen_ids:
            seq += 1
            cid = f"{prefix}{seq:04d}"
        items.append({
            "id": cid,
            "title": title[:300],
            "body": re.sub(r"\s+", " ", str(row.get("doc_body") or ""))[:300],
            "expected": "",
            "split": split,
            "hint": {
                "run_id": row.get("run_id") or "",
                "doc_id": row.get("doc_id") or "",
                "kind": row.get("doc_kind") or "",
                "evidence_level": row.get("evidence_level") or "",
                "predicted": row.get("type") or "",
                "url": row.get("url") or "",
            },
        })
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"harvest-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for item in items:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    result = {"path": str(path), "count": len(items), "scanned": len(rows),
              "skipped": skipped, "split": split}
    optstore.append_audit({"action": "harvest", "actor": "cli",
                           "version": path.name, "count": len(items),
                           "scanned": len(rows)})
    return result


DOC_MIN_BODY = 80
RECALL_LABELS = ("fact", "none")


def _doc_names(store, run_id: str, entity_key: str) -> list[str]:
    """采集时的候选主体名，离线重跑时用同一组名字做主体核验。"""
    run = store.get_run(run_id) or {}
    ent = store.get_entity(entity_key) or {}
    out: list[str] = []
    for value in (run.get("company"), ent.get("short_name"), ent.get("name")):
        v = str(value or "").strip()
        if v and v not in out:
            out.append(v)
    return out


def harvest_docs(out_dir, limit: int = 200, entity_key: str = "", prefix: str = "D",
                 split: str = "dev", cases_path=None,
                 include_covered: bool = False) -> dict:
    """把入库文档导出成召回评测的待标注样本，expected 一律留空。

    默认只导没有产出事实的文档——这是抽取与检索的盲区；include_covered=True
    时连已产出事实的文档一起导，用于检查误报。expected 只能填 fact 或 none：
    这一层只问“该文档是否应产出事实”，不标事件类型。
    """
    from ..infra import store  # 只有采集需要数据库，其余 optimize 命令离线

    split = _split(split)
    try:
        store.init()
        rows = store.docs_with_fact_state(limit=limit, entity_key=entity_key,
                                          only_blind=not include_covered)
    except Exception as e:
        raise ValueError(f"读取运行文档失败：{type(e).__name__}") from e

    seen_titles, seen_ids = _known_samples(cases_path) if cases_path else (set(), set())
    skipped = {"duplicate": 0, "no_title": 0, "routine": 0,
               "no_body": 0, "no_entity": 0}
    names_cache: dict[tuple[str, str], list[str]] = {}
    items: list[dict] = []
    seq = 0
    for row in rows:
        title = str(row.get("title") or "").strip()
        if not title:
            skipped["no_title"] += 1
            continue
        key = _norm_title(title)
        if key in seen_titles:
            skipped["duplicate"] += 1
            continue
        if extract.ROUTINE.search(title):
            skipped["routine"] += 1
            continue
        body = re.sub(r"\s+", " ", str(row.get("body") or "")).strip()
        if len(body) < DOC_MIN_BODY:
            skipped["no_body"] += 1
            continue
        cache_key = (str(row.get("run_id") or ""), str(row.get("entity_key") or ""))
        if cache_key not in names_cache:
            names_cache[cache_key] = _doc_names(store, *cache_key)
        names = names_cache[cache_key]
        if not names:
            skipped["no_entity"] += 1
            continue
        try:
            extra = json.loads(row.get("extra") or "{}")
        except ValueError:
            extra = {}
        query = str(extra.get("query") or "") if isinstance(extra, dict) else ""

        seen_titles.add(key)
        seq += 1
        cid = f"{prefix}{seq:04d}"
        while cid in seen_ids:
            seq += 1
            cid = f"{prefix}{seq:04d}"
        fact_count = int(row.get("fact_count") or 0)
        items.append({
            "id": cid,
            "title": title[:300],
            "body": body[:1200],
            "expected": "",
            "split": split,
            "hint": {
                "run_id": row.get("run_id") or "",
                "doc_id": row.get("doc_id") or "",
                "kind": row.get("kind") or "",
                "evidence_level": row.get("evidence_level") or "",
                "url": row.get("url") or "",
                "source": row.get("source") or "",
                "query": query,
                "fact_count": fact_count,
                "predicted": "fact" if fact_count else "none",
                "names": names,
            },
        })

    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"harvest-docs-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for item in items:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    result = {"path": str(path), "count": len(items), "scanned": len(rows),
              "skipped": skipped, "split": split,
              "include_covered": include_covered}
    optstore.append_audit({"action": "harvest_docs", "actor": "cli",
                           "version": path.name, "count": len(items),
                           "scanned": len(rows),
                           "include_covered": include_covered})
    return result


def load_doc_cases(path) -> list[dict]:
    """读取文档级召回评测集。

    每条要有 title、expected（fact / none）与 hint.names；body 可选。
    与分类评测集分开校验：expected 不是事件类型，而是“该文档是否应产出事实”。
    """
    p = pathlib.Path(path)
    if not p.exists():
        raise FileNotFoundError(f"召回评测集不存在：{p}")
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        raise ValueError(f"读取召回评测集失败：{e}") from e
    payload = None
    try:
        payload = json.loads(text)
    except ValueError:
        pass
    items: list = []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict) and isinstance(payload.get("cases"), list):
        items = payload["cases"]
    else:
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                items.append(json.loads(line))
            except ValueError as e:
                raise ValueError(f"召回评测集行格式错误：{e}") from e

    cases: list[dict] = []
    for i, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise ValueError(f"召回样本第 {i} 条不是对象")
        cid = str(item.get("id") or f"R{i:04d}")
        title = str(item.get("title") or "").strip()
        if not title:
            raise ValueError(f"召回样本 {cid} 缺少 title")
        expected = str(item.get("expected") or "").strip().lower()
        if expected not in RECALL_LABELS:
            raise ValueError(f"召回样本 {cid} 的 expected 只能是 fact 或 none")
        hint = item.get("hint") or {}
        names = [str(x).strip() for x in (hint.get("names") or []) if str(x).strip()]
        if not names:
            raise ValueError(f"召回样本 {cid} 缺少 hint.names，无法重跑主体核验")
        cases.append({"id": cid, "title": title, "body": str(item.get("body") or ""),
                      "expected": expected, "split": _split(item.get("split") or "dev"),
                      "hint": {**hint, "names": names}})
    if not cases:
        raise ValueError("召回评测集没有样本")
    return cases


def recall(cases: list[dict], rules=None, base_version=None, min_conf=None,
           actor: str = "offline") -> dict:
    """文档级召回评测：只判“是否产出事实”，不判事件类型。

    判定与运行期同口径：抽得出事实且置信度不低于 min_fact_conf 才算产出。
    这不是门禁，只用于对照：检索词或抽取规则调整前后各跑一次，比较漏检与误报。
    评测报告写入 data/optimizer/evaluations/，文件名以 recall- 开头。
    """
    rules = _base_rules(rules)
    version = _base_version(base_version)
    min_conf = CFG.min_fact_conf if min_conf is None else float(min_conf)
    year = time.localtime().tm_year
    rows: list[dict] = []
    skipped: list[dict] = []
    for case in cases:
        hint = case.get("hint") or {}
        names = {str(x).strip() for x in (hint.get("names") or []) if str(x).strip()}
        if not names:
            skipped.append({"id": case["id"], "reason": "no_names"})
            continue
        doc = {"title": case["title"], "body": case.get("body") or "",
               "kind": hint.get("kind") or "",
               "evidence_level": hint.get("evidence_level") or "",
               "url": hint.get("url") or ""}
        try:
            fact = extract.build_fact(doc, names, year, rules=rules)
        except Exception:
            fact = None
        kept = bool(fact and fact.confidence >= min_conf)
        predicted = "fact" if kept else "none"
        rows.append({"id": case["id"], "title": case["title"],
                     "kind": hint.get("kind") or "",
                     "split": case.get("split") or "dev",
                     "expected": case["expected"], "predicted": predicted,
                     "url": hint.get("url") or "",
                     "fact_type": fact.type if fact else "",
                     "confidence": round(fact.confidence, 3) if fact else 0.0})

    total = len(rows)
    correct = sum(1 for r in rows if r["expected"] == r["predicted"])
    missed = [r for r in rows if r["expected"] == "fact" and r["predicted"] == "none"]
    false_pos = [r for r in rows if r["expected"] == "none" and r["predicted"] == "fact"]
    by_kind: dict[str, dict] = {}
    for kind in sorted({r["kind"] or "unknown" for r in rows}):
        sub = [r for r in rows if (r["kind"] or "unknown") == kind]
        hit = sum(1 for r in sub if r["expected"] == r["predicted"])
        by_kind[kind] = {"cases": len(sub), "accuracy": round(hit / len(sub), 4)}

    report = {
        "kind": "recall",
        "version": version,
        "min_conf": min_conf,
        "cases": total,
        "skipped": skipped,
        "correct": correct,
        "accuracy": round(correct / total, 4) if total else 0.0,
        "missed": missed,
        "false_positive": false_pos,
        "by_kind": by_kind,
        "rows": rows,
        "evaluated_at": optstore.now(),
    }
    path = optstore.save_evaluation(f"recall-{version}", report)
    report["evaluation_file"] = str(path)
    optstore.append_audit({
        "action": "recall_eval", "actor": actor, "version": version,
        "cases": total, "missed": len(missed),
        "false_positive": len(false_pos), "accuracy": report["accuracy"],
        "report": path.name,
    })
    return report


def collect_report(run_id: str, limit: int = 20) -> dict:
    """读取某次运行的采集留痕：每路查询的召回统计、覆盖对照与被拒条目。

    留痕在采集时写入 output/collect/<run_id>.jsonl 与 -gap.jsonl，统计随
    runs.stats 落库。旧版本运行没有留痕，返回空列表而不是报错。
    """
    from ..infra import store

    run_id = str(run_id or "").strip()
    if not run_id:
        raise ValueError("必须指定运行编号")
    try:
        store.init()
        run = store.get_run(run_id)
    except Exception as e:
        raise ValueError(f"读取运行失败：{type(e).__name__}") from e
    if not run:
        raise ValueError(f"运行不存在：{run_id}")
    stats = run.get("stats") or "{}"
    if isinstance(stats, str):
        try:
            stats = json.loads(stats)
        except ValueError:
            stats = {}
    collect = (stats or {}).get("collect") or {}
    path = OUT_DIR / "collect" / f"{run_id}.jsonl"
    gap_path = OUT_DIR / "collect" / f"{run_id}-gap.jsonl"
    return {
        "run_id": run_id,
        "queries": collect.get("queries") or [],
        "rejected": int(collect.get("rejected") or 0),
        "failed": int(collect.get("failed") or 0),
        "empty": int(collect.get("empty") or 0),
        "log": path.name if path.exists() else "",
        "rows": _read_jsonl(path, limit),
        "coverage": (stats or {}).get("coverage") or {},
        "gap_rows": _read_jsonl(gap_path, limit),
        "gap_log": gap_path.name if gap_path.exists() else "",
    }


def _read_jsonl(path: pathlib.Path, limit: int) -> list[dict]:
    """读前 limit 行 JSONL；文件缺失或某行损坏时跳过，不抛异常。"""
    rows: list[dict] = []
    if not path.exists():
        return rows
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if len(rows) >= max(1, int(limit)):
                break
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        return []
    return rows


def depth_report(base_id: str, cand_id: str) -> dict:
    """对照两次运行：加深检索后新增了多少候选、文档与事实。

    只读运行记录与文档，不联网。两次运行的新闻流随时间变化，结果只能作观察，
    不是严格对照；也不因此自动修改页数或检索词。
    """
    from ..infra import store

    base_id, cand_id = str(base_id or "").strip(), str(cand_id or "").strip()
    if not base_id or not cand_id:
        raise ValueError("必须指定两次运行编号")
    if base_id == cand_id:
        raise ValueError("两次运行不能是同一个编号")
    try:
        store.init()
        base, cand = store.get_run(base_id), store.get_run(cand_id)
    except Exception as e:
        raise ValueError(f"读取运行失败：{type(e).__name__}") from e
    for rid, run in ((base_id, base), (cand_id, cand)):
        if not run:
            raise ValueError(f"运行不存在：{rid}")
    if base.get("company") != cand.get("company"):
        raise ValueError("只能对照同一家公司的两次运行")

    def _pack(run: dict, docs: list[dict], facts: list[dict]) -> dict:
        stats = run.get("stats") or "{}"
        if isinstance(stats, str):
            try:
                stats = json.loads(stats)
            except ValueError:
                stats = {}
        params = run.get("params") or "{}"
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except ValueError:
                params = {}
        queries = ((stats or {}).get("collect") or {}).get("queries") or []
        urls = {d.get("url") or d.get("title") or d.get("id") for d in docs}
        return {
            "run_id": run["id"],
            "company": run.get("company") or "",
            "pages": int((params or {}).get("news_pages") or 0),
            "terms_version": (params or {}).get("search_terms_version") or "",
            "candidates": sum(int(q.get("raw") or 0) for q in queries),
            "kept": sum(int(q.get("kept") or 0) for q in queries),
            "ms": sum(int(q.get("ms") or 0) for q in queries),
            "docs": len(urls),
            "facts": len(facts),
            "_urls": urls,
            "_queries": {str(q.get("query") or ""): q for q in queries},
            "_doc_facts": {f.get("doc_id") for f in facts if f.get("doc_id")},
        }

    base_docs, cand_docs = store.docs_of_run(base_id), store.docs_of_run(cand_id)
    base_facts, cand_facts = store.facts_of_run(base_id), store.facts_of_run(cand_id)
    b, c = _pack(base, base_docs, base_facts), _pack(cand, cand_docs, cand_facts)
    new_urls = c["_urls"] - b["_urls"]
    overlap = b["_urls"] & c["_urls"]
    new_doc_ids = {d.get("id") for d in cand_docs
                   if (d.get("url") or d.get("title") or d.get("id")) in new_urls}
    new_with_fact = len(new_doc_ids & c["_doc_facts"])

    per_query = []
    for q in sorted(set(b["_queries"]) | set(c["_queries"])):
        br = b["_queries"].get(q) or {}
        cr = c["_queries"].get(q) or {}
        per_query.append({"query": q,
                          "base_raw": int(br.get("raw") or 0),
                          "cand_raw": int(cr.get("raw") or 0),
                          "base_kept": int(br.get("kept") or 0),
                          "cand_kept": int(cr.get("kept") or 0)})
    per_query.sort(key=lambda r: r["cand_raw"] - r["base_raw"], reverse=True)

    report = {
        "kind": "depth",
        "base": {k: v for k, v in b.items() if not k.startswith("_")},
        "cand": {k: v for k, v in c.items() if not k.startswith("_")},
        "candidates": {"base": b["candidates"], "cand": c["candidates"],
                       "delta": c["candidates"] - b["candidates"]},
        "kept": {"base": b["kept"], "cand": c["kept"],
                 "delta": c["kept"] - b["kept"]},
        "docs": {"base": b["docs"], "cand": c["docs"],
                 "new": len(new_urls), "overlap": len(overlap)},
        "new_docs_with_fact": new_with_fact,
        "ms": {"base": b["ms"], "cand": c["ms"]},
        "terms_changed": b["terms_version"] != c["terms_version"],
        "per_query": per_query,
        "note": "两次运行的时间窗与新闻流不同，结果只能作观察，不是严格对照",
        "evaluated_at": optstore.now(),
    }
    path = optstore.save_evaluation(f"depth-{base_id}-{cand_id}", report)
    report["report_file"] = str(path)
    optstore.append_audit({"action": "depth_eval", "base": base_id, "cand": cand_id,
                           "new_docs": len(new_urls), "report": path.name})
    return report


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
