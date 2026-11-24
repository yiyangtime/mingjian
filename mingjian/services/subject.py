"""
非上市主体档案（我方基线）。

上市公司可以从巨潮、东财自动取到画像：全称、行业、主营、代码、公告、财报。
非上市企业没有证券代码，没有法定披露，也没有公开财报口径——系统"外查"不到，
只能"内录"：由企业把自己讲清楚的那部分固化下来，作为竞品对比里的「我方」一列。

三条硬约束，和系统其他部分一致：
  · 自述就是自述。每条都标「企业自述」，不伪装成外部核验过的公开事实；
  · 缺失就留空。没填的维度不生成条目，矩阵里显示「—」，不填默认值；
  · 只做对比基线，不做自我评价。这里不产判断，判断由 compare 基于事实写。

字段与竞品周报的八个维度对齐，避免"我方说的"和"对手说的"不在一个口径上。
"""
from __future__ import annotations

import re

from ..infra import store

# (字段, 中文名, 对齐的对比维度)
FIELDS: list[tuple[str, str, str]] = [
    ("name", "企业名称", ""),
    ("region", "所在地", ""),
    ("industry", "行业", ""),
    ("products", "主营产品", "product"),
    ("process", "关键工艺", "rd"),
    ("capacity", "产能与产线", "capacity"),
    ("price_band", "价格带", "price"),
    ("customers", "主要客户行业", "order"),
    ("channels", "销售渠道", "other"),
    ("scale", "营收规模档", "performance"),
    ("note", "补充说明", "other"),
]

FIELD_MAP = {k: (label, dim) for k, label, dim in FIELDS}
TEXT_FIELDS = [k for k, _, _ in FIELDS]


def normalize(name: str) -> str:
    """企业名归一化：去空白、去括号备注、统一大小写，作为主键。"""
    s = (name or "").strip()
    s = re.sub(r"[（(][^）)]*[）)]", "", s)
    s = re.sub(r"\s+", "", s)
    return s


def build(data: dict) -> dict:
    """清洗并校验一份档案。只保留已知字段，空值不落库。"""
    clean: dict = {}
    for k in TEXT_FIELDS:
        v = (data.get(k) or "").strip() if isinstance(data.get(k), str) else data.get(k)
        if v:
            clean[k] = v
    if not clean.get("name"):
        raise ValueError("企业名称不能为空")
    clean["listed"] = bool(data.get("listed"))
    # 自述来源时间，用于提醒甲方"这份基线多久没更新了"
    clean["declared_at"] = (data.get("declared_at") or "").strip()
    return clean


def save(data: dict) -> dict:
    """保存档案。同名覆盖，写入审计留痕（原值不丢）。"""
    clean = build(data)
    key = normalize(clean["name"])
    old = store.get_subject(key)
    store.save_subject(key, clean["name"], clean)
    changed = [k for k in TEXT_FIELDS
               if (old or {}).get("data", {}).get(k) != clean.get(k)] if old else list(clean)
    store.add_audit("subject:" + key, "-", "subject_save" if old else "subject_create",
                    key, old_value="" , new_value=",".join(changed),
                    reason=("更新自述档案" if old else "新建自述档案") + f"，字段 {len(changed)} 项",
                    actor="企业用户")
    return get(key) or {}


def get(key_or_name: str) -> dict:
    key = normalize(key_or_name)
    row = store.get_subject(key)
    if not row:
        return {}
    d = dict(row.get("data") or {})
    d["key"] = key
    d["updated_at"] = row.get("updated_at", "")
    return d


def list_all(limit: int = 50) -> list[dict]:
    out = []
    for r in store.list_subjects(limit):
        d = dict(r.get("data") or {})
        d["key"] = r.get("key")
        d["updated_at"] = r.get("updated_at", "")
        out.append(d)
    return out


def baseline(subject: dict) -> list[dict]:
    """档案 → 「我方」条目，供竞争矩阵按维度对齐。

    返回结构刻意做成和事实一致（id/type/statement/source/declared），
    这样矩阵不用为"我方"写第二套渲染逻辑。
    """
    if not subject:
        return []
    out = []
    for i, (k, label, dim) in enumerate(FIELDS, 1):
        if not dim:
            continue
        v = subject.get(k)
        if not v:
            continue
        out.append({
            "id": f"S{i:02d}",
            "type": dim,
            "statement": f"{label}：{v}",
            "source": "企业自述",
            "evidence_level": "declared",
            "declared": True,
            "url": "",
        })
    return out


def coverage(subject: dict, dims=None) -> dict:
    """档案覆盖了哪几个对比维度——用来告诉甲方"哪几格你自己也没数"。

    dims 传入 compare.DIMENSIONS（键, 中文名），保证对比维度只有一份定义，
    这里不另抄一份；不传则退回档案自带的维度键。
    """
    filled = {dim for k, _, dim in FIELDS if dim and subject.get(k)}
    if dims:
        keys = [k for k, _ in dims]
        blank = [(k, label) for k, label in dims if k not in filled]
        return {"filled": len([k for k in keys if k in filled]), "total": len(keys),
                "dims": sorted(filled & set(keys)),
                "blank": blank,
                "labels": {k: label for k, label in dims if k in filled}}
    return {"filled": len(filled), "total": len(filled), "dims": sorted(filled),
            "blank": [], "labels": {d: d for d in filled}}
