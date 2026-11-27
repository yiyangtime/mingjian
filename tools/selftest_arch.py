"""架构边界自测：把分层规则固化成可执行断言，而不是写在 README 里靠自觉。

跑法：python tools/selftest_arch.py

覆盖：
  后端依赖方向  —— api → runtime → services → infra → core，只允许单向；反向、跨层都报错
  包根白名单    —— mingjian/ 根目录只允许入口与配置，业务模块必须落到某一层
  路由纪律      —— 方法+路径唯一、统一 /api 前缀、按域打 tag、路由之间互不 import
  前端依赖方向  —— features 只依赖 shared 与自身；shared 不得反向依赖；视图不得直连 fetch
  路由可达性    —— features 下每个视图都必须在 router.ts 中挂载，防止拆分后成孤儿
  import 目标    —— 相对 import 的目标模块必须真实存在，防重构后遗留 `from . import 旧模块`
  视图体积棘轮  —— 每页行数上限只降不升，新逻辑先抽组件/composable 再谈加行

纯静态检查（AST + 路径解析），不依赖网络与数据库；配合 selftest_all.py 一起跑。
"""
import ast
import pathlib
import posixpath
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PKG = ROOT / "mingjian"
SRC = ROOT / "web" / "src"

FAILS = []


def fail(msg: str) -> None:
    FAILS.append(msg)


# ---------------------------------------------------------------- 后端分层

# 每一层允许依赖的层：只能向下，不能回指，也不能跳过中间层访问更下层以外的分支
ALLOWED = {
    "core": {"core"},
    "infra": {"core", "infra"},
    "services": {"core", "infra", "services"},
    "runtime": {"core", "infra", "services", "runtime"},
    "api": {"core", "infra", "services", "runtime", "api"},
}

# 包根只放装配与入口；新增业务代码必须选择层，不能继续堆在根上
ROOT_WHITELIST = {"__init__.py", "__main__.py", "cli.py", "config.py", "server.py"}


def _module_name(path: pathlib.Path) -> str:
    # 统一带上包名：分层判断与 import 目标校验都按 mingjian.xxx 的绝对名工作
    rel = path.relative_to(ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _pkg_of(path: pathlib.Path) -> list[str]:
    """文件所在包的分段；__init__.py 自身就是包，不能再去掉末段。"""
    parts = _module_name(path).split(".")
    return parts if path.name == "__init__.py" else parts[:-1]


def _imported_modules(path: pathlib.Path) -> list[tuple[str, int]]:
    """把 import / from ... import 统一解析成绝对模块名，相对导入按包深度回推。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    pkg = _pkg_of(path)
    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:                      # from ..infra import store
                base = pkg[: len(pkg) - (node.level - 1)]
                target = ".".join(base + ([node.module] if node.module else []))
                for alias in node.names:
                    out.append((f"{target}.{alias.name}" if target else alias.name, node.lineno))
            elif node.module and node.module.split(".")[0] == "mingjian":
                out.append((node.module, node.lineno))
                for alias in node.names:        # from mingjian.infra import store
                    out.append((f"{node.module}.{alias.name}", node.lineno))
        elif isinstance(node, ast.Import):
            for alias in node.names:            # import mingjian.infra.store
                if alias.name.split(".")[0] == "mingjian":
                    out.append((alias.name, node.lineno))
    return out


def check_backend_layers() -> None:
    for path in PKG.iterdir():
        if path.suffix == ".py" and path.name not in ROOT_WHITELIST:
            fail(f"包根不允许放业务模块：mingjian/{path.name}，请归入 core/infra/services/runtime/api")

    for path in sorted(PKG.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        mod = _module_name(path)
        layer = mod.split(".")[1] if "." in mod else ""
        if layer not in ALLOWED:                # config / 包根入口不参与单向检查
            continue
        for dep, lineno in _imported_modules(path):
            seg = dep.split(".")
            if len(seg) < 2 or seg[0] != "mingjian":
                continue
            target = seg[1]
            if target == "config" or (PKG / f"{target}.py").is_file():
                continue                        # config 属于全局基础配置，各层可读
            if target.startswith("__"):
                continue                        # from .. import __version__：包元数据不是模块
            if target not in ALLOWED[layer]:
                fail(f"{mod}:{lineno} 依赖越层：{layer} -> {dep}")
            # 同级路由之间不许互相 import：共享逻辑下沉到 deps.py 或更低的层；
            # routes/__init__ 是注册表，汇总各路由模块不在此列
            if mod.startswith("mingjian.api.routes.") and dep.startswith("mingjian.api.routes."):
                here = mod.split(".")[3] if len(mod.split(".")) > 3 else ""
                there = dep.split(".")[3] if len(dep.split(".")) > 3 else ""
                if here and there and here != there and there != "deps":
                    fail(f"{mod}:{lineno} 路由横向耦合：{dep}，请把共用逻辑放到 api/deps.py")


# ---------------------------------------------------------------- 路由纪律

def check_routes() -> None:
    from mingjian.api.routes import _MODULES

    seen: dict[tuple[str, str], str] = {}
    for module in _MODULES:
        for route in module.router.routes:
            name = module.__name__.rsplit(".", 1)[-1]
            for method in route.methods - {"HEAD", "OPTIONS"}:
                key = (method, route.path)
                if key in seen:
                    fail(f"路由重复注册：{method} {route.path}（{seen[key]} 与 {name}）")
                seen[key] = name
            if not route.path.startswith("/api/"):
                fail(f"{name} 的 {route.path} 未使用 /api 前缀")
            if not route.tags:
                fail(f"{name} 的 {route.path} 未声明 tag，OpenAPI 会散成一堆无分组接口")
    if len(seen) < 30:
        fail(f"路由总数异常：只有 {len(seen)} 条，疑似拆分时漏注册")


# ---------------------------------------------------------------- 前端分层

IMPORT_RE = re.compile(r"""(?:from\s+|import\s*\(\s*|import\s+)['"]([^'"]+)['"]""")
FETCH_RE = re.compile(r"\bfetch\s*\(|XMLHttpRequest")


def _layer_of(parts: tuple[str, ...]) -> str:
    if not parts:
        return ""
    if parts[0] == "features":
        return f"feature:{parts[1]}" if len(parts) > 1 else "feature"
    return parts[0] if parts[0] in {"app", "shared"} else "root"


def check_frontend_layers() -> None:
    router_text = (SRC / "app" / "router.ts").read_text(encoding="utf-8")

    for path in sorted(SRC.rglob("*")):
        if path.suffix not in {".ts", ".vue"} or "__" in path.name:
            continue
        rel = path.relative_to(SRC)
        here = _layer_of(rel.parts)
        text = path.read_text(encoding="utf-8")

        if here.startswith("feature:") and FETCH_RE.search(text):
            fail(f"{rel.as_posix()} 直接发起请求，必须收敛到 shared/api")

        for spec in IMPORT_RE.findall(text):
            if not spec.startswith("."):
                continue                        # 第三方包（vue / vue-router）不参与分层
            target = posixpath.normpath(posixpath.join(rel.parent.as_posix(), spec))
            if target.startswith(".."):
                continue                        # 指向 src 之外（如 node_modules 别名），不归分层管
            parts = tuple(target.split("/"))
            there = _layer_of(parts)

            if here == "shared" and there != "shared":
                fail(f"{rel.as_posix()} 反向依赖：shared 不得 import {spec}")
            elif here.startswith("feature:"):
                if there not in {"shared", here}:
                    fail(f"{rel.as_posix()} 跨功能/逆层依赖：{here} -> {spec}，功能之间只共享 shared")

    # 每个视图都必须在路由表里挂载，否则就是拆分留下的孤儿文件
    for view in sorted((SRC / "features").glob("*/*.vue")):
        if view.name not in router_text:
            fail(f"视图未挂载：{view.relative_to(SRC).as_posix()} 不在 router.ts 中")


# ---------------------------------------------------------------- import 目标

def _module_exists(mod: str) -> bool:
    p = PKG.joinpath(*mod.split(".")[1:])
    return p.with_suffix(".py").is_file() or (p / "__init__.py").is_file()


def check_import_targets() -> None:
    """相对 import 必须指向真实存在的模块。

    分层检查只看「属于哪一层」，`from . import net`（net 早已下沉到 core）之类若仍合层
    就会漏网，只在运行到该分支时才炸——往往是构造、取数、正文抓取这类少走的路。
    """
    for path in sorted(PKG.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        pkg = _pkg_of(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.level:
                continue
            base = pkg[: len(pkg) - (node.level - 1)]
            if node.module:
                target = ".".join(base + [node.module])
                if not _module_exists(target):
                    fail(f"{_module_name(path)}:{node.lineno} import 目标不存在：{target}")
            else:
                # from . import x：x 必须是同包的模块；__version__ 这类包属性不在此列
                for alias in node.names:
                    if alias.name.startswith("__"):
                        continue
                    target = ".".join(base + [alias.name])
                    if not _module_exists(target):
                        fail(f"{_module_name(path)}:{node.lineno} import 目标不存在：{target}")


# ---------------------------------------------------------------- 视图体积

# 体积棘轮：数值只允许调低。拆出组件/composable 后把上限跟着降；
# 新视图必须在这里登记预算，否则视为未受控代码。
VIEW_BUDGET = {
    "agent/AgentView.vue": 500,
    "company/CompanyView.vue": 1245,
    "competitor/CompetitorView.vue": 1245,
    "metrics/MetricsView.vue": 890,
    "policy/PolicyView.vue": 1265,
    "settings/SettingsView.vue": 370,
    "system/SystemView.vue": 90,
}


def check_view_budget() -> None:
    for view in sorted((SRC / "features").glob("*/*.vue")):
        rel = view.relative_to(SRC / "features").as_posix()
        lines = len(view.read_text(encoding="utf-8").splitlines())
        cap = VIEW_BUDGET.get(rel)
        if cap is None:
            fail(f"新视图未登记体积预算：{rel}（先拆子组件，再在 selftest_arch.py 登记上限）")
        elif lines > cap:
            fail(f"视图超预算：{rel} {lines} 行 > {cap} 行，请先抽组件/composable 再调整上限")


# ---------------------------------------------------------------- 入口

def main() -> int:
    checks = [
        ("后端分层：依赖方向与包根白名单", check_backend_layers),
        ("路由纪律：唯一性、前缀、tag、横向解耦", check_routes),
        ("import 目标：相对导入必须存在", check_import_targets),
        ("前端分层：features/shared 边界与请求收敛", check_frontend_layers),
        ("前端体积：视图行数棘轮", check_view_budget),
    ]
    for title, fn in checks:
        before = len(FAILS)
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            fail(f"{title} 执行失败：{type(e).__name__}: {e}")
        print(f"  {'PASS' if len(FAILS) == before else 'FAIL'}  {title}")

    for msg in FAILS:
        print(f"        {msg}")
    print()
    print(f"{'通过' if not FAILS else f'{len(FAILS)} 处越界'}：架构边界检查")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    sys.exit(main())
