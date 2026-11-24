"""
全局配置。全部可通过环境变量或 config.json 覆盖，便于交付后甲方自行调整。

模型接入配置例外：页面保存的配置存数据库（settings 表）并优先生效，
环境变量与 config.json 只在该库还没有模型配置时作为兜底。
"""
from __future__ import annotations

import json
import os
import pathlib
import urllib.parse
from dataclasses import asdict, dataclass

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "output"
for _d in (DATA_DIR, OUT_DIR):
    _d.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "mingjian.db"
CONFIG_PATH = ROOT / "config.json"


def mask_db_url(url: str) -> str:
    """隐去连接串里的密码，供日志与接口展示。"""
    if not url or "://" not in url:
        return url
    try:
        p = urllib.parse.urlsplit(url)
        port = f":{p.port}" if p.port else ""
    except ValueError:
        return url
    if not p.password:
        return url
    user = urllib.parse.unquote(p.username or "")
    host = p.hostname or ""
    if ":" in host:                       # IPv6 字面量
        host = f"[{host}]"
    netloc = f"{user}:***@{host}{port}"
    return urllib.parse.urlunsplit((p.scheme, netloc, p.path, p.query, p.fragment))


@dataclass
class Config:
    # 采集窗口与规模
    days: int = 30                 # 回溯天数
    max_companies: int = 6         # 一次竞品对比的家数上限
    max_news: int = 45             # 资讯条数上限
    max_ann: int = 40              # 公告条数上限
    max_fetch: int = 12            # 正文抓取条数上限（控制耗时）
    news_pages: int = 3            # 东财资讯首路翻页数

    # 网络
    timeout: float = 15.0
    retries: int = 2
    host_interval: float = 0.45    # 同域最小请求间隔（秒），礼貌抓取

    # 抽取与成稿
    min_fact_conf: float = 0.45    # 低于此置信度只进复核区，不进事实区
    max_facts: int = 30            # 报告引用事实上限

    # 模型（可选）。页面保存的配置存 settings 表并优先；这里只作兜底，留空则走规则引擎
    llm_base: str = ""             # 例：https://api.deepseek.com/v1
    llm_key: str = ""
    llm_model: str = ""
    llm_timeout: float = 90.0

    # 数据库（可选）。留空则使用 data/mingjian.db；支持 sqlite / postgresql / mysql
    db_url: str = ""

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_base and self.llm_key and self.llm_model)

    def to_dict(self) -> dict:
        d = asdict(self)
        if d.get("llm_key"):
            d["llm_key"] = "***"
        if d.get("db_url"):
            d["db_url"] = mask_db_url(d["db_url"])
        return d


def load() -> Config:
    cfg = Config()

    # 1) config.json
    if CONFIG_PATH.exists():
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            for k, v in raw.items():
                if hasattr(cfg, k) and not k.startswith("_"):
                    setattr(cfg, k, type(getattr(cfg, k))(v))
        except Exception as e:  # 配置坏掉不应阻断运行
            print(f"[config] 忽略无法解析的 config.json: {e}")

    # 2) 环境变量优先（MINGJIAN_ 前缀，保留 AGENT_ 兼容旧部署）
    env_map = {
        "DAYS": ("days", int),
        "MAX_COMPANIES": ("max_companies", int),
        "MAX_NEWS": ("max_news", int),
        "MAX_ANN": ("max_ann", int),
        "MAX_FETCH": ("max_fetch", int),
        "LLM_BASE": ("llm_base", str),
        "LLM_KEY": ("llm_key", str),
        "LLM_MODEL": ("llm_model", str),
        "DB_URL": ("db_url", str),
    }
    for suffix, (attr, caster) in env_map.items():
        for prefix in ("MINGJIAN_", "AGENT_"):
            val = os.environ.get(prefix + suffix)
            if val:
                try:
                    setattr(cfg, attr, caster(val))
                except Exception:
                    pass
                break

    # 3) 通用模型环境变量兜底
    if not cfg.llm_base:
        for base_env in ("OPENAI_BASE_URL", "DEEPSEEK_BASE_URL", "MOONSHOT_BASE_URL"):
            if os.environ.get(base_env):
                cfg.llm_base = os.environ[base_env]
                break
    if not cfg.llm_key:
        for key_env in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "MOONSHOT_API_KEY", "DASHSCOPE_API_KEY"):
            if os.environ.get(key_env):
                cfg.llm_key = os.environ[key_env]
                break
    if not cfg.llm_model:
        for m_env in ("OPENAI_MODEL", "DEEPSEEK_MODEL", "MODEL_NAME"):
            if os.environ.get(m_env):
                cfg.llm_model = os.environ[m_env]
                break

    return cfg


CFG = load()
DB_URL = CFG.db_url          # 空串表示使用本地 SQLite（DB_PATH）
