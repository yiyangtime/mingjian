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
| `infra` | 外部世界适配器：数据库（SQLite / PostgreSQL / MySQL）、巨潮 / 东财 / 百度资讯、工商登记接口、模型服务、定时调度 | `core` | 新增数据源、换存储实现 |
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
│   ├── registry.py          工商登记接口：预设、鉴权、字段映射、本地缓存（落 settings / companies）
│   ├── optstore.py          离线候选版本、评测报告与审计的本地文件存储
│   ├── scheduler.py         每天 / 每周表达式
│   └── sources/
│       ├── cninfo.py        巨潮：实体解析、公告列表与 PDF
│       ├── eastmoney.py     东财：公司档案、主营构成、指标、资讯（检索词可传入）
│       └── baidu.py         百度资讯：东财失败时的首屏回退
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
│   ├── optimizer.py         离线候选评测、待标注采集、召回评测、深度对照、门禁与回滚
│   ├── searchterms.py       检索词版本：文件校验与基线回退
│   ├── coverage.py          公告覆盖对照：披露事项与资讯的代理匹配
│   └── skills.py            技能注册表
├── runtime/
│   ├── pipeline.py          七步编排 + 事件流 + 检查点
│   ├── compare.py           竞品矩阵与判断
│   └── jobs.py              作业队列、事件推送、取消 / 续跑 / 调度回调
├── api/
│   ├── app.py               应用装配、中间件、统一错误形状、SPA 托管
│   ├── deps.py              跨路由共享依赖
│   └── routes/              七个领域接口（九个路由模块）
├── cli.py                   命令行
├── server.py                uvicorn 启动器
└── __main__.py              支持 python -m mingjian

web/                         前端工程
├── src/app/                 App.vue 与 router.ts
├── src/shared/              api / components / composables / lib / styles / types
├── src/features/            company / policy / competitor / metrics / agent / system / settings
└── dist/                    构建产物，服务端唯一托管目录
```

## 三、一次企业洞察运行的数据流

```text
用户输入公司名
  → api/routes/runs.py 建作业（runs 表，status=running）
  → runtime/jobs.py 入队，线程池执行
  → runtime/pipeline.py 七步：
      1 实体解析   infra/sources/cninfo.py + eastmoney.py → entities 表
      2 多源采集   公告列表 + 资讯检索（检索词版本固定、逐路召回与被拒条目留痕；
                    东财失败时回退百度资讯首屏） → 检查点 1
      3 正文解析   PDF 文本层 / HTML 正文 → 检查点 2（按文档记录 evidence_level）
      4 事实抽取   services/extract.py → 检查点 3（confidence 计算与合并）
     （第 4 步结束存检查点 4）
      5 变化比对   services/diff.py 对 snapshots 上一份快照
      6 报告成稿   services/report.py（可选模型只润色判断段）
      7 留痕入库   facts / snapshots / audit / output/*.md + csv
  → 事件逐条落 run_events，前端按 seq 增量拉取
```

失败与取消发生在步骤边界；续跑从最近的检查点恢复，第 5–7 步重算。

### 公司搜索链路

监测对象、竞品名单与档案补全共用 `/api/company/entity/search`，按以下顺序合并并去重：

```text
输入公司名 / 简称
  → 工商登记   companies 缓存 → 已配置的正式接口（按需消耗额度）
  → 证券主体库 巨潮（仅上市）
  → 本地档案   subjects 自述档案 + 历史运行出现过的名称
  → 资讯线索   东财检索，失败时回退百度资讯首屏
  → 按输入名称建档（兜底，保证搜索框本身是入口）
```

上市主体优先展示；名称互含或 3-gram 高度重合视为同一主体，不重复出候选。
工商接口未配置、超时或报错时，后四路仍照常返回，失败原因写入响应的 `notes`。

## 四、存储模型

| 表 | 作用 | 关键约束 |
|---|---|---|
| `entities` | 实体解析结果（key / 代码 / 全称 / 行业 / 注册地） | `key` 唯一 |
| `companies` | 工商登记缓存（信用代码 / 法人 / 注册资本 / 经营状态 / 经营范围 / 官网） | `key` 唯一；接口按次计费，查过就留档 |
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
| `settings` | 运行期配置（模型接入、工商数据源） | 键唯一，值为 JSON 原文 |

运行期默认连本地 SQLite，也可通过 `db_url` 切到 PostgreSQL / MySQL，业务代码只写一种
SQL。表结构在 `infra/schema.py` 里定义一份，可渲染成三种方言，生成 `sql/` 下的
初始化脚本；运行期 SQLite 建表语句仍写在 `infra/store.py`，两边由
`python tools/dbinit.py --check` 做强一致校验。方言差异与切换步骤见
[database.md](database.md)。

模型接入配置也存在 `settings` 表（键 `llm`）：页面保存即落库，重启由
`llmcfg.bootstrap()` 读回；`config.json` 的 `llm_*` 与 `MINGJIAN_LLM_*` 只在库里
还没有配置时兜底，页面里「停用」会落库，重启后不会被环境变量顶回。

工商数据源同样存在 `settings`（键 `registry`）：端点、鉴权、结果路径与字段映射
都可配，密钥只落库、不回传前端。检索结果写入 `companies` 表并缓存在进程内 300 秒，
同一关键词不会反复消耗接口额度；未配置时整条链路关闭，不影响原有搜索。检索结果里的
官网随 `companies` 一并缓存，旧库启动时自动补列。

离线优化不新增数据库表：生效规则指针、版本归档、评测报告与审计都在
`data/optimizer/`。每次运行开始时读取一次版本，版本号写入 `runs.params` 与检查点，
保证历史运行和续跑不被后续上线串味。候选只允许增量关键词，不能执行代码；
详见 [optimizer.md](optimizer.md)。

资讯检索词走单独的轻量版本：内置基线在 `infra/sources/eastmoney.py`，可选的
`data/optimizer/search_terms.json` 按整份覆盖（非法则回退基线）。运行起点固定版本，
每路召回条数写入 `runs.stats.search_recall`；因为还没有带标注的召回语料，检索词
不入门禁，也没有 promote。

采集过滤同时留痕：每路的候选 / 入围 / 重复 / 不相关条数，以及成功 / 空结果 / 失败
状态与耗时写入 `runs.stats.collect`；失败路不计入召回，全部失败记入运行错误。
被丢弃条目的标题、链接与拒因落在 `output/collect/<run_id>.jsonl`（不存正文），
`optimize collect <run_id>` 可回看。它只能区分“召回了但被过滤误杀”与“检索失败”，
接口正常却没召回的仍不可见。每路还记录实际来源 `via`：东财失败时回退百度
资讯首屏，`fallback_from` 保留主源失败原因；主源正常空结果不换源。

采集完成后还做一次公告覆盖对照：逐条公告找最相近的资讯，按公告核心词被覆盖的
比例打分，缺口清单写入 `output/collect/<run_id>-gap.jsonl`。公告与媒体资讯不是
同一集合，小公告无人报道很正常，因此覆盖比例只供人工抽样，不设门禁。

同一公司的两次运行可用 `optimize depth <浅> <深>` 对照候选 / 入围 / 文档 / 事实
增量；它只读运行记录与文档，不联网。两次运行的时间窗不同，增量只能作观察，
不是严格对照，也不据此自动改页数或检索词。

文档级召回评测用 `optimize harvest --kind docs` 导出未产出事实的文档，人工标
`expected`（fact / none），`optimize recall` 离线回放并比对漏检与误报。它只覆盖
已入库文档，被拒候选虽有留痕但无正文、不能自动回放，因此不设门禁。

## 五、并发模型

- 单进程多线程：uvicorn 同步路由跑在线程池，作业队列、取消信号、调度线程都在进程内存里。
- **不要**用 `uvicorn --workers N` 或多进程部署：轮询会打到没有作业状态的进程。
- 作业状态以数据库为准，内存只做加速；服务重启后历史运行仍可回看。

## 六、新增能力改哪里

| 你要做的事 | 落点 | 注意 |
|---|---|---|
| 加接口 | `api/routes/<领域>.py` + `routes/__init__.py` 登记 | 路由路径唯一、按领域打 tag |
| 改判定规则 | `services/` | 必须同时加断言到 `tools/selftest_all.py`；分类词表候选走 `optimizer` 的 draft / eval / promote 门禁 |
| 改检索词 | `data/optimizer/search_terms.json` | 2—8 路、每路不超过 40 字；整份覆盖，非法回退基线；逐路召回与被拒条目留痕；尚未纳入门禁 |
| 接新数据源 | `infra/sources/<源>.py` | 只做取数与归一化，不写业务判断；失败要可降级 |
| 换工商接口 | 接入中心「工商数据源」，或 `infra/registry.py` 的 `PRESETS` | 只改请求形态与字段映射；密钥不写进代码与文档 |
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