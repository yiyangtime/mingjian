"""定时调度：不引入 cron 依赖，只支持业务真正需要的两种频率。

    {"freq": "daily",  "time": "09:00"}
    {"freq": "weekly", "weekday": 5, "time": "16:00"}   # 1=周一，7=周日

设计取舍：
  · 不做任意 cron 表达式。业务要的是「每周五 16:00 出周报」，不是给运维配作业；
  · 调度线程只在 serve 进程里跑（CLI 的 sched 命令只管理定义，不常驻）；
  · 触发前先把 next_run_at 推进到下一次，避免某次执行异常导致同一时刻反复触发；
  · 服务停机期间错过的时点不补跑——补跑一批过期任务比漏跑更危险。
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta

from ..core.log import get as _get_logger
from . import store

LOG = _get_logger("scheduler")

WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def _parse_time(value: str) -> tuple[int, int]:
    try:
        hh, mm = str(value or "").strip().split(":")
        h, m = int(hh), int(mm)
    except Exception:
        raise ValueError("时间格式应为 HH:MM，例如 09:00")
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError("时间超出范围：小时 0-23，分钟 0-59")
    return h, m


def validate(spec: dict) -> bool:
    if not isinstance(spec, dict):
        raise ValueError("调度规则必须是对象")
    freq = str(spec.get("freq") or "").strip()
    if freq not in ("daily", "weekly"):
        raise ValueError("频率只支持 daily（每天）/ weekly（每周）")
    _parse_time(spec.get("time"))
    if freq == "weekly":
        try:
            wd = int(spec.get("weekday"))
        except Exception:
            raise ValueError("每周任务需给出 weekday：1=周一，7=周日")
        if not 1 <= wd <= 7:
            raise ValueError("weekday 需在 1-7 之间（1=周一，7=周日）")
    return True


def compute_next(spec: dict, now: datetime | None = None) -> datetime:
    """返回下一次触发时间（本地时间）。"""
    validate(spec)
    now = now or datetime.now()
    hour, minute = _parse_time(spec["time"])
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if spec["freq"] == "daily":
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    target = int(spec["weekday"]) - 1          # Python: 周一=0
    candidate += timedelta(days=(target - candidate.weekday()) % 7)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


def next_run_text(spec: dict, now: datetime | None = None) -> str:
    return compute_next(spec, now).strftime("%Y-%m-%d %H:%M:%S")


def describe(spec: dict) -> str:
    validate(spec)
    if spec["freq"] == "daily":
        return f"每天 {spec['time']}"
    return f"每{WEEKDAY_CN[int(spec['weekday']) - 1]} {spec['time']}"


def tick(handler) -> int:
    """检查并触发到期任务，返回本次触发条数。

    handler(schedule) 由调用方提供，负责真正入队；返回 run_id。
    """
    now_text = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        due = store.due_schedules(now_text)
    except Exception as e:  # 数据库暂时不可用不应让调度线程退出
        LOG.warning("读取定时任务失败：%s", e)
        return 0

    fired = 0
    for s in due:
        try:
            # 先推进，再执行：即使 handler 抛错，也不会在同一分钟反复重试
            store.set_schedule_next(s["id"], next_run_text(s.get("spec") or {}))
            run_id = handler(s)
            LOG.info("定时任务已触发 %s（%s）-> %s", s.get("id"), s.get("name"), run_id)
            fired += 1
        except Exception as e:
            LOG.warning("定时任务执行失败 %s（%s）：%s", s.get("id"), s.get("name"), e)
    return fired


_thread: threading.Thread | None = None
_lock = threading.Lock()


def start(handler, interval: int = 20) -> bool:
    """启动后台调度线程；重复调用不会起第二个线程。"""
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return False

        def loop() -> None:
            while True:
                try:
                    tick(handler)
                except Exception as e:  # noqa: BLE001
                    LOG.warning("调度循环异常：%s", e)
                time.sleep(max(5, int(interval)))

        _thread = threading.Thread(target=loop, name="mingjian-scheduler", daemon=True)
        _thread.start()
    return True


def running() -> bool:
    return _thread is not None and _thread.is_alive()
