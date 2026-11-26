# 贡献指南

感谢你愿意参与。明鉴的核心约束是「每条结论都能翻回原文」，所以这里的
贡献标准也围绕一件事：**不要让系统说出没有证据的话。**

## 一、开发环境

| 项 | 要求 |
|---|---|
| Python | 3.10+（CI 跑 3.12） |
| 虚拟环境 | 项目根目录 `.venv`，依赖只装这里，不使用全局 Python |
| Node | 18+（仅改前端时需要，CI 跑 20） |
| 运行时依赖 | `.venv` 中 `python -m pip install -r requirements.txt` |
| 开发依赖 | `.venv` 中 `python -m pip install -e ".[dev]"` |

```bash
git clone https://github.com/yiyangtime/mingjian.git
cd mingjian
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS / Linux: source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m mingjian serve          # http://127.0.0.1:8765
```

前端：

```bash
cd web
npm ci
npm run dev                       # 5173，/api 代理到 8765
npm run build                     # vue-tsc + 产出 dist，服务端直接托管
```

## 二、提交前必须通过的门禁

以下命令均在已激活的 `.venv` 中执行：

```bash
ruff check .
python tools/selftest_all.py      # 67 项断言
python tools/selftest_arch.py     # 分层边界、路由唯一性与前端体积棘轮
cd web && npm run build           # 类型检查 + 构建
```

四项都过再开 PR。CI 会重复执行同样的命令。

## 三、改哪里：分层边界

依赖方向只有一条：`api → runtime → services → infra → core`。

| 你要做的事 | 改哪里 |
|---|---|
| 加一个接口 | `mingjian/api/routes/<领域>.py`，并在 `routes/__init__.py` 登记 |
| 改抽取/判定/归因规则 | `mingjian/services/` |
| 接一个新数据源 | `mingjian/infra/sources/`，实现取数与归一化，不要在 service 里写 HTTP |
| 调流水线步骤、加检查点 | `mingjian/runtime/` |
| 加页面 | `web/src/features/<域>/`，路由表登记，请求收敛到 `shared/api` |

`tools/selftest_arch.py` 会拦截反向导入、重复路由、跨功能 import。
详见 [docs/architecture.md](docs/architecture.md)。

## 四、本项目的硬性约定

1. **禁止真实公司名进代码与文档。** 示例统一用「某某企业」「甲公司」「乙公司」等虚构名称；
   不使用真实公司名或证券代码，测试样本也用虚构名称。提交前自查。
2. **事实必须可追溯。** 新增任何产出事实的规则，必须同时能给出 `doc_id` 与证据句；
   抽不到证据句时退回标题并降置信度，不允许编造。
3. **缺失就留空。** 不用推测值、默认值或跨期数据填充空字段。
4. **单份失败不阻断整批。** 采集失败要记入缺口并保留链接，不要让异常冒泡打断整个运行。
5. **页面不放说明性文字。** 界面只呈现数据与操作，规则解释写在文档里。
6. **视图体积棘轮只降不升。** 新增视图必须先登记行数预算；加功能时把逻辑抽成
   composable 或域内组件，而不是把视图撑大。
7. **不引入重型依赖。** 后端运行时只允许 FastAPI 与 uvicorn，业务逻辑用标准库与 SQLite。
   需要新依赖时先在 issue 里说明理由。
8. **中文注释，解释「为什么」。** 不要复述代码在做什么。

## 五、测试要求

- 纯函数规则（抽取、分类、归因、矩阵、调度时间）在 `tools/selftest_all.py` 里加断言。
- 每个 case 用 `@case("中文描述")` 注册，写清楚「行为 + 期望」。
- 涉及数据库的用例必须把 `store.DB_PATH` 指到临时目录，并在 `finally` 里还原。
- 不允许为了让测试通过而放宽生产规则。

```python
@case("事件分类：标题不带信息时不硬判")
def _classify_fallback():
    assert extract.classify("关于近期经营情况的公告", "董事会审议。")[0] == "other"
```

## 六、提交与 PR

- 提交信息用 `类型: 简述`，类型取 `feat` / `fix` / `refactor` / `docs` / `test` / `chore`。
- 一个 PR 只做一件事；行为变化要同时更新文档与测试。
- PR 描述里写清楚：改了什么、为什么、怎么验证（贴命令与结果）。
- 涉及报告结构、置信度公式、判定阈值的改动，必须在 PR 里说明对既有报告的影响。

## 七、许可

本项目采用 Apache License 2.0。提交 PR 即表示你同意以同一许可证授权你的贡献
（inbound = outbound），并确认你有权提交这些代码。

安全问题请勿开公开 issue，见 [SECURITY.md](SECURITY.md)。