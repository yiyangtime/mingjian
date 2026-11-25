"""资讯检索词：声明式、可版本固定，暂不参与上线门禁。

检索词决定采集阶段能发现什么，比分类词更靠前。当前只做到三件事：

    - 内置基线 + 本地文件覆盖（data/optimizer/search_terms.json）；
    - 运行开始时读取一次，版本写入 runs.params 与检查点；
    - 每路召回条数写入 runs.stats.search_recall，用于攒对照基线。

还没有带标注的语料来度量「该召回而未召回」，所以这里不提供 promote/rollback：
文件只能人工编辑，改完先观察召回结构，再考虑纳入门禁。
"""
from __future__ import annotations

import pathlib

from ..infra import optstore
from ..infra.sources import eastmoney

DEFAULT_TERMS: tuple[str, ...] = eastmoney.DEFAULT_TERMS
MIN_TERMS = 2
MAX_TERMS = 8
MAX_TERM_LEN = 40


def file_path() -> pathlib.Path:
    return optstore.OPT_DIR / "search_terms.json"


def normalize_terms(raw) -> list[str] | None:
    """校验并归一化检索词；不合法返回 None，由调用方回退基线。"""
    if not isinstance(raw, (list, tuple)):
        return None
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            return None
        term = " ".join(item.split())
        if not term or len(term) > MAX_TERM_LEN:
            return None
        if term not in out:
            out.append(term)
    if not MIN_TERMS <= len(out) <= MAX_TERMS:
        return None
    return out


def load() -> dict:
    """读取本地检索词配置；缺失或非法时回退内置基线，不阻断运行。"""
    path = file_path()
    data = optstore.read_json(path)
    if isinstance(data, dict):
        terms = normalize_terms(data.get("terms"))
        try:
            version = optstore.safe_version(str(data.get("version") or ""))
        except ValueError:
            version = ""
        if terms and version:
            return {"version": version, "terms": terms, "source": path.name}
    return {"version": "baseline", "terms": list(DEFAULT_TERMS), "source": "builtin"}


def active_version() -> str:
    return load()["version"]


def active_terms() -> list[str]:
    return load()["terms"]
