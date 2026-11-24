"""按领域注册路由。

应用装配只依赖本入口：新增接口组时在这里登记一次，
其余路由文件保持互不感知，避免再出现一个文件装下全部端点。
"""
from __future__ import annotations

from fastapi import FastAPI

from . import agents, competitors, entities, metrics, policies, runs, schedules, settings, system

_MODULES = (system, runs, entities, policies, metrics, agents, competitors, schedules, settings)


def register_all(app: FastAPI) -> None:
    for module in _MODULES:
        app.include_router(module.router)
