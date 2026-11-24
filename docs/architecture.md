# 架构

本文说明代码怎么分层、数据怎么流动、新增能力应该改哪里。
方法论与判定规则见 [methodology.md](methodology.md)。

## 一、分层与依赖方向

依赖方向只有一条，反向导入会被 `tools/selftest_arch.py` 拦下：

```text
api → runtime → services → infra → core
```

| 层 | 职责 | 只允许依赖 | 典型改动 |
|---|---|---|---|
| `core` | 与业务无关的基础能力：日志、HTTP、文本清洗、PDF 文本还原、上下文预算 | 标准库 + `config` | 换 HTTP 客户端、调预算算法 |
| `infra` | 外部世界适配器：数据库（SQLite / PostgreSQL / MySQL）、巨潮 / 东财、模型服务、定时调度 | `core` | 新增数据源、换存储实现 |
| `services` | 领域规则：抽取、分类、归因、成稿、政策解析，不感知 HTTP | `core` + `infra` | 改判定规则、加对比维度 |
| `runtime` | 长流程编排：七步管道、竞品对比、作业队列与断点 | 以上全部 | 调步骤顺序、加检查点 |
| `api` | 协议层：路由、参数校验、统一错误形状与缓存头 | 以上全部 | 加接口、改返回形状 |

前端同理：`features → shared`，功能之间互不 import，页面只通过 `shared/api` 访问后端。

## 二、目录

```text
mingjian/                    后端包
├── config.py                运行配置（config.json + 环境变量）
├── core/
│   ├── log.py               日志与轮转
│   ├── net.py               HTTP 会话、重试、限速、字符集处理
│   ├── textproc.py          清洗、截断、句子与标题识别
│   ├── pdftext.py           PDF 文本层还原
│   └── context.py           模型上下文预算（中文按字，其它按 4 字符）
├── infra/
│   ├── store.py             十二表与全部 SQL（运行期）
│   ├── db.py                连接层：方言识别、占位符转译、事务与行工厂
│   ├── schema.py            表结构的单一来源，渲染 SQLite / PostgreSQL / MySQL 方言
│   ├── llm.py               OpenAI 兼容调用与返回校验
│   ├── llmcfg.py            服务商预设、多套配置、连接测试（落 settings 表）
│   ├── scheduler.py         每天 / 每周表达式
│   └── sources/
│       ├── cninfo.py        巨潮：实体解析、公告列表与 PDF
│       └── eastmoney.py     东财：公司档案、主营构成、指标、资讯
├── services/
│   ├── extract.py           事件分类、金额日期抽取、置信度、跨源合并
│   ├── diff.py              与上一份快照比对
│   ├── report.py            八节成稿
│   ├── policy.py            政策公告字段抽取
│   ├── govpolicy.py         按产业查询部委与省级站点，含健康记录
│   ├── metrics.py           经营指标与主营构成（30 分钟缓存）
│   ├── topic.py             专题四段成稿与归因
│   ├── subject.py           非上市主体自述档案
│   ├── discover.py          品类词反查候选竞品
│   ├── memory.py            跨运行记忆
│   └── skills.py            技能注册表
├── runtime/
│   ├── pipeline.py          七步编排 + 事件流 + 检查点
│   ├── compare.py           竞品矩阵与判断
│   └── jobs.py              作业队列、事件推送、取消 / 续跑 / 调度回调
├── api/
│   ├── app.py               应用装配、中间件、统一错误形状、SPA 托管
│   ├── deps.py              跨路由共享依赖
│   └── routes/              九个领域路由
├── cli.py                   命令行
├── server.py                uvicorn 启动器
└── __main__.py              支持 python -m mingjian

web/                         前端工程
├── src/app/                 App.vue 与 router.ts
├── src/shared/              api / components / composables / lib / styles / types
├── src/features/            company / policy / competitor / metrics / agent / settings
└── dist/                    构建产物，服务端唯一托管目录
```

## 三、一次公司情报运行的数据流

```text
用户输入公司名
  → api/routes/runs.py 建作业（runs 表，status=running）
  → runtime/jobs.py 入队，线程池执行
  → runtime/pipeline.py 七步：
      1 实体解析   infra/sources/cninfo.py + eastmoney.py → entities 表
      2 多源采集   公告列表 + 资讯检索 → 检查点 1
      3 正文解析   PDF 文本层 / HTML 正文 → 检查点 2（按文档记录 evidence_level）
      4 事实抽取   services/extract.py → 检查点 3（confidence 计算与合并）
     （第 4 步结束存检查点 4）
      5 变化比对   services/diff.py 对 snapshots 上一份快照
      6 报告成稿   services/report.py（可选模型只润色判断段）
      7 留痕入库   facts / snapshots / audit / output/*.md + csv
  → 事件逐条落 run_events，前端按 seq 增量拉取
```

失败与取消发生在步骤边界；续跑从最近的检查点恢复，第 5–7 步重算。

## 四、存储模型

| 表 | 作用 | 关键约束 |
|---|---|---|
| `entities` | 实体解析结果（key / 代码 / 全称 / 行业 / 注册地） | `key` 唯一 |
| `subjects` | 非上市主体自述档案 | `key` 唯一，JSON 全字段 |
| `runs` | 每次运行的状态与统计 | 状态机 running / done / failed / cancelled |
| `documents` | 采集到的原文（公告 / 资讯 / 网页） | 证据等级 full / snippet / title |
| `facts` | 抽取后的事实 | 必须带 `doc_id`；`confidence` 由规则计算 |
| `snapshots` | 每次运行的事实全量快照 | 变化比对与竞品复用的基础 |
| `audit` | 只追加的操作留痕 | 不覆盖原值 |
| `run_events` | 步骤与日志事件 | 同运行内 `seq` 单调递增 |
| `checkpoints` | 第 1–4 步检查点 | 主键 `(run_id, step)` |
| `schedules` | 定时任务 | 由 serve 进程的调度线程触发 |
| `memory` | 跨运行记忆（摘要 + 引用） | 只存摘要，不替代 `facts` |
| `settings` | 运行期配置（当前是模型接入配置） | 键唯一，值为 JSON 原文 |

运行期默认连本地 SQLite，也可通过 `db_url` 切到 PostgreSQL / MySQL，业务代码只写一种
SQL。表结构在 `infra/schema.py` 里定义一份，可渲染成三种方言，生成 `sql/` 下的
初始化脚本；运行期 SQLite 建表语句仍写在 `infra/store.py`，两边由
`python tools/dbinit.py --check` 做强一致校验。方言差异与切换步骤见
[database.md](database.md)。

模型接入配置也存在 `settings` 表（键 `llm`）：页面保存即落库，重启由
`llmcfg.bootstrap()` 读回；`config.json` 的 `llm_*` 与 `MINGJIAN_LLM_*` 只在库里
还没有配置时兜底，页面里「停用」会落库，重启后不会被环境变量顶回。

## 五、并发模型

- 单进程多线程：uvicorn 同步路由跑在线程池，作业队列、取消信号、调度线程都在进程内存里。
- **不要**用 `uvicorn --workers N` 或多进程部署：轮询会打到没有作业状态的进程。
- 作业状态以数据库为准，内存只做加速；服务重启后历史运行仍可回看。

## 六、新增能力改哪里

| 你要做的事 | 落点 | 注意 |
|---|---|---|
| 加接口 | `api/routes/<领域>.py` + `routes/__init__.py` 登记 | 路由路径唯一、按领域打 tag |
| 改判定规则 | `services/` | 必须同时加断言到 `tools/selftest_all.py` |
| 接新数据源 | `infra/sources/<源>.py` | 只做取数与归一化，不写业务判断；失败要可降级 |
| 加报告节 | `services/report.py` | 同步 `docs/methodology.md` 与前端渲染 |
| 加页面 | `web/src/features/<域>/` | 路由表登记；视图行数预算只降不升 |
| 加技能 | `services/skills.py` | 参数契约 + 白名单；内部步骤只登记不可执行 |
| 加指标 | `services/metrics.py` | 只允许白名单指标 × 报告期，不开放自由 SQL |

## 七、前端约束

- 请求与类型收敛在 `shared/api`、`shared/types.ts`，页面不直接拼 URL。
- 轮询状态机在 `shared/composables/runPolling.ts`，两个运行页共用。
- localStorage 读写收敛在 `shared/lib/storage.ts`。
- 视图体积棘轮：`selftest_arch.py` 记录每个视图的行数上限，只降不升。
  新功能优先抽 composable 或域内组件（`features/<域>/components/`）。
- 页面不出现说明性文字，规则解释写在文档里。