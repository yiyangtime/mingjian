"""
本地 Web 服务启动器。

HTTP 层已迁移到 FastAPI（api/app.py），后台作业在 runtime/jobs.py；
本模块只负责端口检查、浏览器打开与 uvicorn 启动，
定时调度线程随 FastAPI lifespan 启动。

同时保留作业队列的旧入口（_job_push/_steps_of 等）以兼容既有脚本与自测。
"""
from __future__ import annotations

import socket
import threading
import webbrowser

import uvicorn

from .api import create_app
from .config import ROOT
from .core.log import get as _get_logger
from .infra import store
from .runtime import jobs

LOG = _get_logger("server")

# 兼容层：自测与脚本仍可按 server._job_push / server._steps_of 使用
_JOBS = jobs._JOBS
_LOCK = jobs._LOCK
_CANCEL = jobs._CANCEL
_report_path = jobs._report_path
_result_from_db = jobs._result_from_db
_steps_of = jobs._steps_of
_new_job = jobs._new_job
_job_push = jobs._job_push
_cfg_from_params = jobs._cfg_from_params
_enqueue_run = jobs._enqueue_run
_enqueue_compare = jobs._enqueue_compare
_start_scheduled = jobs._start_scheduled


def _port_in_use(host: str, port: int) -> bool:
    """探一下端口是否已有服务在听。"""
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0


def serve(port: int = 8765, host: str = "127.0.0.1", open_browser: bool = True) -> None:
    store.init()
    # Windows 允许两个进程绑同一端口，第二个会静默「成功」但请求可能被旧进程接走，
    # 改了代码却看不到效果，排查成本很高。这里先探一下。
    if _port_in_use(host, port):
        print(f"警告：{host}:{port} 已有进程在监听，本次启动可能与旧实例争抢请求。")
        print("      建议先结束占用该端口的进程，或换一个端口。")
        LOG.warning("端口 %s:%s 已被占用，可能重复启动", host, port)
    url = f"http://{host}:{port}/"
    print(f"明鉴 · 产业情报 已启动： {url}")
    print("提示：在页面输入公司名称或证券代码，服务端会真实联网采集。")
    print("按 Ctrl+C 停止。")
    LOG.info("服务启动 %s（FastAPI + uvicorn，根目录 %s）", url, ROOT)
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        uvicorn.run(create_app(), host=host, port=port, log_config=None, access_log=False)
    except KeyboardInterrupt:
        pass
    print("\n已停止。")
    LOG.info("服务已停止")
