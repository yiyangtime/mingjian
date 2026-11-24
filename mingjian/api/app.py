"""
FastAPI 应用装配：中间件、统一错误形状、生命周期与 SPA 静态托管。

业务端点按领域放在 api/routes/ 下，由 routes.register_all 统一注册；
长任务与作业队列仍在 runtime/jobs.py。
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import __version__
from ..config import ROOT
from ..core.log import get as _get_logger
from ..infra import llmcfg, scheduler, store
from ..runtime import jobs
from .routes import register_all

LOG = _get_logger("api")

DIST_DIR = ROOT / "web" / "dist"

# OpenAPI 分组：路由按领域拆分，文档也按领域成组，避免 /api/openapi.json 一屏平铺
TAGS = [
    {"name": "系统与审计", "description": "健康检查、系统信息、审计日志读写"},
    {"name": "情报运行", "description": "发起 / 轮询 / 取消 / 续跑、报告与事实表"},
    {"name": "企业与主体", "description": "实体搜索、公司档案、我方基线、竞品线索"},
    {"name": "政策取数", "description": "政策目录扫描、文件解析、工信部与省厅官网取数"},
    {"name": "指标与专题", "description": "经营指标、主营业务构成、专题归因"},
    {"name": "智能体", "description": "技能注册表执行、跨运行记忆读写与沉淀"},
    {"name": "竞品对比", "description": "多家公司按事件维度横向对比"},
    {"name": "定时调度", "description": "每天 / 每周任务的增删改查与立即触发"},
    {"name": "模型设置", "description": "模型接入配置、启用切换与连接测试"},
]


@asynccontextmanager
async def _lifespan(app: FastAPI):
    store.init()
    # 模型配置以库为准：启动时读回生效项（首次会把 config.json 的旧配置迁入）
    llmcfg.bootstrap()
    # 上次进程留下的 queued/running 已经没有线程在跑，先落成失败，避免前端无限轮询
    stale = store.reconcile_interrupted_runs()
    if stale:
        LOG.warning("已将 %d 个上次中断的运行标记为失败（可从断点续跑）", stale)
    # 定时任务只在 serve 进程里跑：CLI 的 sched 命令只管理定义，不常驻
    if scheduler.start(jobs._start_scheduled):
        LOG.info("定时调度线程已启动（每 20 秒检查一次）")
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="明鉴 · 产业情报",
        version=__version__,
        description="产业情报工作台：公告与资讯采集、政策匹配、竞品对比、指标归因、记忆与调度。",
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
        openapi_tags=TAGS,
        lifespan=_lifespan,
    )

    # ---------- 统一响应与错误 ----------

    @app.middleware("http")
    async def _access_log(request: Request, call_next):
        t0 = time.time()
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            # 与旧 HTTP 层保持一致：接口响应不缓存，避免轮询拿到上一次的结果
            response.headers.setdefault("Cache-Control", "no-store")
            ms = (time.time() - t0) * 1000
            (LOG.warning if response.status_code >= 500 else LOG.info)(
                "%s %s -> %d %.0fms", request.method, request.url.path,
                response.status_code, ms)
        return response

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code,
                            headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        errs = exc.errors()
        first = errs[0] if errs else {}
        loc = ".".join(str(x) for x in first.get("loc", []) if x != "body")
        msg = first.get("msg", "参数不合法")
        return JSONResponse({"error": f"参数校验失败：{loc} {msg}".strip()}, status_code=422)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        LOG.exception("未处理异常 %s %s", request.method, request.url.path)
        return JSONResponse({"error": f"服务内部错误：{type(exc).__name__}"}, status_code=500)

    # ---------- 领域路由 ----------

    register_all(app)

    # ---------- 静态资源（SPA） ----------

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "未知路径")
        index = DIST_DIR / "index.html"
        if not index.exists():
            return HTMLResponse(
                "<!doctype html><meta charset='utf-8'>"
                "<h1>前端尚未构建</h1>"
                "<p>请先在本机执行：<code>cd web</code> → <code>npm install</code> → "
                "<code>npm run build</code>，生成 <code>web/dist</code> 后重试。</p>"
                "<p>后端接口不受影响，<code>/api/health</code> 可直接访问。</p>",
                status_code=503,
                headers={"Cache-Control": "no-store"},
            )
        root = DIST_DIR.resolve()
        target = (root / path.lstrip("/")).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            raise HTTPException(404, "路径越界")
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            # 带后缀的资源缺失就是缺失，不能拿入口 HTML 冒充脚本/图片
            if Path(path).suffix:
                raise HTTPException(404, "静态资源不存在")
            target = index
        cache = "public, max-age=31536000, immutable" if "assets" in target.parts else "no-store"
        return FileResponse(target, headers={"Cache-Control": cache})

    return app
