"""统一日志：文件轮转 + 终端输出，只用标准库。

排查线上问题最需要知道的是「什么时候、哪个环节、失败还是成功」。
之前只有 traceback 打到 stdout，服务一停就全没了，所以这里固定两件事：
  1. 关键路径（采集、取数、解析、接口异常）各留一行，带公司/运行号等上下文；
  2. 文件轮转，单文件 2MB × 4 份，够回溯最近一段时间，又不会把磁盘吃满。
"""
from __future__ import annotations

import logging
import logging.handlers
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
LOG_DIR = ROOT / "logs"

_configured = False


def get(name: str = "") -> logging.Logger:
    """取一个带统一格式的 logger。

    统一挂在 "mingjian" 命名空间下（如 mingjian.server），
    这样 handler 只需要在本包根上装一次，子模块自动继承。
    """
    global _configured
    if not _configured:
        _setup()
    full = f"mingjian.{name}" if name else "mingjian"
    return logging.getLogger(full)


def _setup() -> None:
    global _configured
    _configured = True

    root = logging.getLogger("mingjian")
    root.setLevel(logging.INFO)
    if root.handlers:
        return

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)-20s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            LOG_DIR / "mingjian.log", maxBytes=2 * 1024 * 1024,
            backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except Exception:
        # 目录不可写也不能影响主流程，至少保留终端输出
        pass

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)
