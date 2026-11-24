"""
技能注册表：把系统能力声明成带参数契约的条目，统一校验与执行。

约束：
  · 只有注册在册的技能能被调用，未知名字直接拒绝；
  · 每个技能显式声明副作用（network / write / filesystem），执行留痕由服务端负责；
  · 流水线内部的步骤只登记、不开放单独执行，避免绕过运行状态与取消机制；
  · 这里不引入模型：模型将来只负责「选技能 + 填参数」，执行与校验仍在本模块。
"""
from __future__ import annotations

import json
import time

from ..config import ROOT
from . import memory, metrics, policy

KIND_LABEL = {"query": "查询", "collect": "采集", "write": "写入", "internal": "流程内"}
KIND_ORDER = ("query", "collect", "write", "internal")

_FIELDS = ("title", "url", "source", "published_at", "body")


def _skill(name: str, title: str, kind: str, side_effect: str, description: str,
           schema: dict, handler=None, internal: bool = False) -> dict:
    return {"name": name, "title": title, "kind": kind, "kind_label": KIND_LABEL[kind],
            "side_effect": side_effect, "description": description, "schema": schema,
            "handler": handler, "internal": internal}


def _f(kind: str, desc: str = "", *, required: bool = False, enum: list | None = None,
       min: float | None = None, max: float | None = None, max_len: int | None = None,
       default=None) -> dict:
    d = {"type": kind, "desc": desc, "required": required}
    if enum:
        d["enum"] = enum
    if min is not None:
        d["min"] = min
    if max is not None:
        d["max"] = max
    if max_len is not None:
        d["max_len"] = max_len
    if default is not None:
        d["default"] = default
    return d


# ---------------- handlers ----------------

def _entity_resolve(args: dict) -> dict:
    from .sources import cninfo
    ent = cninfo.resolve(args["company"])
    return {"found": bool(ent), "entity": ent or {}}


def _news_collect(args: dict) -> dict:
    from .sources import eastmoney
    items = eastmoney.news_bundle(args["company"], args.get("short") or args["company"],
                                  days=int(args.get("days") or 30))
    slim = [{"title": it.get("title"), "url": it.get("url"), "source": it.get("source"),
             "published_at": it.get("published_at"), "excerpt": (it.get("body") or "")[:160]}
            for it in items[:20]]
    return {"count": len(items), "items": slim}


def _discover(args: dict) -> dict:
    from . import discover
    return discover.find(args["products"], args.get("region") or "",
                         days=int(args.get("days") or 180),
                         limit=int(args.get("limit") or 12))


def _policy_scan(_args: dict) -> dict:
    docs, skipped = policy.scan_dir(ROOT / "materials")
    slim = []
    for d in docs[:20]:
        row = {k: d.get(k) for k in _FIELDS}
        row["body"] = (row.get("body") or "")[:800]
        row["id"] = d.get("id") or ""
        row["evidence_level"] = d.get("evidence_level") or ""
        slim.append(row)
    return {"files": len(docs), "stats": policy.stats(docs), "skipped": skipped, "docs": slim}


def _metric_query(args: dict) -> dict:
    from .sources import cninfo
    company = args["company"]
    ent = cninfo.resolve(company) or {"short_name": company, "code": "", "market": ""}
    out = metrics.query(company, ent, args["metric"], int(args.get("periods") or 4))
    out["entity"] = {"short_name": ent.get("short_name"), "code": ent.get("code")}
    return out


def _memory_recall(args: dict) -> dict:
    items = memory.recall(args.get("query") or "", args.get("scope") or "",
                          int(args.get("limit") or 8))
    return {"count": len(items), "items": items}


def _memory_remember(args: dict) -> dict:
    refs = args.get("refs") or []
    if isinstance(refs, str):
        refs = [x.strip() for x in refs.replace("，", ",").split(",") if x.strip()]
    return memory.remember(args.get("scope") or "global", args.get("key") or "",
                           args["content"], args.get("kind") or "note",
                           refs=refs, source=args.get("source") or "skill",
                           confidence=args.get("confidence"))


# ---------------- 注册表 ----------------

_REGISTRY: list[dict] = [
    _skill("entity.resolve", "主体识别", "query", "network",
           "公司名 → 上市主体（代码/简称/市场），未上市返回 found=false。",
           {"company": _f("string", "公司名称，可填简称", required=True, max_len=60)},
           _entity_resolve),
    _skill("news.collect", "资讯采集", "collect", "network",
           "按公司名与事件词多路检索近期资讯，按主体相关度过滤。",
           {"company": _f("string", "公司名称", required=True, max_len=60),
            "short": _f("string", "简称（可选，用于相关度过滤）", max_len=40),
            "days": _f("number", "回溯天数", min=1, max=365, default=30)},
           _news_collect),
    _skill("discover.competitors", "竞品线索", "query", "network",
           "主营产品词 → 候选竞品名单，附命中文章与检索词。",
           {"products": _f("string", "主营产品/品类词，逗号分隔", required=True, max_len=120),
            "region": _f("string", "区域限定（可选）", max_len=40),
            "days": _f("number", "回溯天数", min=30, max=365, default=180),
            "limit": _f("number", "候选数量上限", min=3, max=30, default=12)},
           _discover),
    _skill("policy.scan", "政策材料扫描", "collect", "filesystem",
           "扫描 materials 目录，返回政策文件清单与字段抽取统计。",
           {}, _policy_scan),
    _skill("metric.query", "指标取数", "query", "network",
           "按白名单指标取最近报告期数据，返回口径与来源。",
           {"company": _f("string", "公司名称", required=True, max_len=60),
            "metric": _f("string", "指标", required=True,
                         enum=[i["k"] for i in metrics.INDICATORS]),
            "periods": _f("number", "报告期数", min=1, max=12, default=4)},
           _metric_query),
    _skill("memory.recall", "记忆召回", "query", "none",
           "按关键词召回已有记忆，命中会计入使用次数。",
           {"query": _f("string", "检索词（公司名、指标、口径）", max_len=120),
            "scope": _f("string", "限定作用域（实体 key）", max_len=120),
            "limit": _f("number", "返回条数", min=1, max=20, default=8)},
           _memory_recall),
    _skill("memory.remember", "写入记忆", "write", "write",
           "写入一条记忆；同作用域、同主题、同类型、同内容会合并引用。",
           {"scope": _f("string", "作用域，默认 global", max_len=120),
            "key": _f("string", "主题（公司名/口径名）", max_len=120),
            "kind": _f("string", "类型", enum=list(memory.KINDS), default="note"),
            "content": _f("string", "记忆内容", required=True, max_len=2000),
            "refs": _f("array", "引用列表（run_id / URL）"),
            "source": _f("string", "来源标记", max_len=120),
            "confidence": _f("number", "置信度 0—1", min=0, max=1)},
           _memory_remember),
    # 流水线内部步骤：只登记，不开放单独执行
    _skill("run.pipeline", "完整情报运行", "internal", "network",
           "实体解析 → 多源采集 → 正文抓取 → 事实抽取 → 变化比对 → 成稿 → 入库。",
           {"company": _f("string", "公司名称", required=True, max_len=60)}, None, True),
    _skill("run.compare", "竞品周报", "internal", "network",
           "多主体并跑 + 矩阵归并 + 差异高亮，按周输出。",
           {"companies": _f("array", "对比公司列表", required=True)}, None, True),
]


def _validate(schema: dict, args: dict) -> dict:
    if not isinstance(args, dict):
        raise ValueError("args 必须是对象")
    unknown = [k for k in args if k not in schema]
    if unknown:
        raise ValueError(f"未知参数：{'、'.join(unknown)}")
    out = {}
    for name, spec in schema.items():
        val = args.get(name)
        if val is None or (isinstance(val, str) and not val.strip()):
            if spec.get("default") is not None:
                out[name] = spec["default"]
                continue
            if spec.get("required"):
                raise ValueError(f"缺少参数：{name}")
            continue
        tp = spec.get("type")
        if tp == "string":
            if not isinstance(val, str):
                raise ValueError(f"参数 {name} 应为字符串")
            val = val.strip()
            if spec.get("max_len") and len(val) > spec["max_len"]:
                raise ValueError(f"参数 {name} 超过 {spec['max_len']} 字")
        elif tp == "number":
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                raise ValueError(f"参数 {name} 应为数字")
            if spec.get("min") is not None and val < spec["min"]:
                raise ValueError(f"参数 {name} 不能小于 {spec['min']}")
            if spec.get("max") is not None and val > spec["max"]:
                raise ValueError(f"参数 {name} 不能大于 {spec['max']}")
        elif tp == "boolean":
            if not isinstance(val, bool):
                raise ValueError(f"参数 {name} 应为布尔值")
        elif tp == "array":
            if not isinstance(val, list):
                raise ValueError(f"参数 {name} 应为数组")
            val = [x for x in val if x][:20]
        if spec.get("enum") and val not in spec["enum"]:
            raise ValueError(f"参数 {name} 不在允许范围：{'、'.join(spec['enum'])}")
        out[name] = val
    return out


def public() -> list[dict]:
    """给页面/规划器看的清单：不含 handler，按类别排序。"""
    rows = []
    for s in _REGISTRY:
        rows.append({k: v for k, v in s.items() if k != "handler"}
                    | {"available": not s["internal"]})
    rows.sort(key=lambda s: (KIND_ORDER.index(s["kind"]), s["name"]))
    return rows


def get(name: str) -> dict | None:
    return next((s for s in _REGISTRY if s["name"] == name), None)


def brief(args: dict) -> str:
    try:
        text = json.dumps(args, ensure_ascii=False)
    except Exception:
        text = str(args)
    return text[:200]


def run(name: str, args: dict | None = None) -> dict:
    """执行一个在册技能。参数错误抛 ValueError；执行失败返回 ok=false。"""
    s = get((name or "").strip())
    if not s:
        raise ValueError(f"未注册的技能：{name}")
    if s["internal"]:
        raise ValueError("该技能由流水线内部调用，不开放单独执行")
    argv = _validate(s["schema"], args or {})
    t0 = time.time()
    try:
        data = s["handler"](argv)
        return {"ok": True, "skill": s["name"], "side_effect": s["side_effect"],
                "elapsed_ms": int((time.time() - t0) * 1000), "data": data}
    except ValueError as e:
        raise ValueError(str(e))
    except Exception as e:
        return {"ok": False, "skill": s["name"], "side_effect": s["side_effect"],
                "elapsed_ms": int((time.time() - t0) * 1000),
                "error": f"{type(e).__name__}: {e}"[:300]}
