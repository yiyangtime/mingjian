"""HTTP 接口层：FastAPI 应用装配与按领域拆分的路由。"""
from .app import create_app

__all__ = ["create_app"]
