# 明鉴 MingJian · 产业情报

> 输入一个公司名，程序自己联网采集、读原文、抽事实、做比对、写报告、留痕。
> **每条结论都能翻回原文。**

明鉴是一个开源的产业情报 Agent：面向产业研究与企业情报场景，把分散在公告、资讯、
政府网站里的公开信息，整理成**可核验、可追溯、可复核**的情报报告。

它不是聊天机器人，也不是行情软件。它做的是一件更笨的事：把每一条结论都绑到
一份有 URL 的文档和一句原文证据上，抽不到证据就不说。

**它不做什么**：不提供股价、涨跌幅、估值、目标价与买卖建议；不生产原始信息；
不编造数字；不用推测值填充缺失字段。

## 为什么可信

这五条是硬约束，删掉任何一条，它就退化成「看起来很聪明的玩具」：

1. **没有证据句，就不产事实。** 抽不到可引用句子时退回标题并降置信度，绝不编。
2. **缺失就留空。** 找不到日期、金额，字段就是空的，不用推测值填充。
3. **事实与判断结构分离。** 判断单独成节并挂事实编号，读者可以精确反驳某一条。
4. **模型只改写，不造事实。** 判断句的引用、数字（含中文数字）、否定与方向逐项核对，任一不过就整批回退；规则版与采用版都留痕，可在页面逐条对照。
5. **单份失败不阻断整批。** 图片型 PDF、反爬、超时只影响那一份，记入缺口、保留链接。

置信度是算出来的，不是拍的：

```text
置信度 = 证据等级基线（公告全文 0.90 / 公告标题摘要 0.70 / 资讯正文 0.72 / 资讯摘要标题 0.55 / 其它 0.52）
        + 0.04 × 事件判定强度 + 0.03（含金额）+ 0.02（公告全文且正文超 300 字）
        + 0.015—0.02 ×（多源佐证数 - 1）
        - 0.06 ×（多源口径冲突数）
        - 0.12（归不进事件类型）- 0.06（证据句退回标题）
```

取值 0.30—0.98；低于 0.45 不进事实区，只进「待验证与数据缺口」。

## 30 秒上手

### 方式一：源码运行（推荐）

```bash
git clone https://github.com/yiyangtime/mingjian.git
cd mingjian
python -m venv .venv                          # 项目依赖只装进 .venv
# Windows: .venv\Scripts\activate
# macOS / Linux: source .venv/bin/activate
python -m pip install -r requirements.txt     # 仅 FastAPI + uvicorn
cd web
npm ci
npm run build                                # 生成 web/dist（Node 18+）
cd ..
python -m mingjian serve                      # 打开 http://127.0.0.1:8765
```

`web/dist` 是构建产物，不入库；首次运行需先构建前端。Python 依赖统一装在项目根目录的 `.venv`，不要装到全局环境；不配置模型也能完整运行（规则引擎）。

也可以只用命令行：

```bash
python -m mingjian run 某某企业          # 跑一次，报告落 output/
python -m mingjian run 某某企业 --days 60  # 自定义回溯窗口
python -m mingjian optimize harvest    # 运行事实导出待标注评测样本
python -m mingjian optimize harvest --kind docs  # 未产出事实的文档导出为召回样本
python -m mingjian optimize recall     # 文档级召回评测：漏检 / 误报对照
python -m mingjian optimize collect R20261118103000-1234  # 回看某次采集的每路召回与被拒条目
python -m mingjian optimize depth R20261118103000-1234 R20261119103000-5678  # 对照加深前后的增量
python -m mingjian optimize draft      # 从评测集判错样本生成候选草案
python -m mingjian optimize eval --candidate tools/optimizer/candidate.example.json  # 离线评测候选
```

### 方式二：Docker

```bash
docker compose up -d          # http://127.0.0.1:8765
```

数据、报告、日志分别挂载到 `data/`、`output/`、`logs/`，容器重建不丢。

### 方式三：作为包安装

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS / Linux: source .venv/bin/activate
python -m pip install -e ".[dev]"
mingjian serve
```

服务端托管 `web/dist`，首次使用需 `cd web && npm ci && npm run build`；Docker 镜像已内置构建。

## 七个页面

| 页面 | 路由 | 回答什么问题 |
|---|---|---|
| 企业洞察 | `/` | 这家公司最近发生了什么 |
| 政策雷达 | `/policy-radar` | 所在产业拿到了什么政策与补贴 |
| 竞品追踪 | `/competitor-tracking` | 同一件事上谁在动、动得多快 |
| 指标透视 | `/metric-insight` | 当期最值得追的问题是什么，归因到哪块业务 |
| 研究助理 | `/research-assistant` | 能执行哪些动作、跨运行记住了什么 |
| 运行治理 | `/governance` | 服务运行得怎样、谁改了什么 |
| 接入中心 | `/access-center` | 接哪家模型、哪家工商接口、报告推送到哪里 |

七个页面各对应一组后端接口，支持深链接：`?run=<编号>`、`?tab=report`、`?fact=F01`。

公司搜索按「工商登记 → 证券主体库 → 本地档案 → 资讯线索」合并去重，最后仍保留一条
「按输入名称建档」。工商登记要接一套正式接口（天眼查、企查查、爱企查、启信宝或
同类云市场商品）——国家企业信用信息公示系统不提供开放 API：接口未配置时自动退回
后三路，配置与边界见 [docs/data-sources.md](docs/data-sources.md)。

## 它自动做了什么

| 步骤 | 实际动作 |
|---|---|
| 1 实体解析 | 公司名 → 实体档案（orgId、全称、行业、主营、注册地） |
| 2 多源采集 | 近 30 天公告 + 按检索词检索资讯，按主体相关度过滤，记录每路状态；资讯主源失败时回退百度资讯首屏 |
| 3 正文解析 | 公告 PDF 还原中文、资讯正文抽取，记录证据等级 |
| 4 事实抽取 | 事件分类、金额日期抽取、证据句定位、置信度、跨源合并 |
| 5 变化比对 | 与同一公司上一份快照比较：新增 / 升温 / 本轮未见 |
| 6 报告成稿 | 八节结构，判断逐条挂事实编号 |
| 7 留痕入库 | 事实、来源、快照、审计全部落库（默认 SQLite，可切 PostgreSQL / MySQL） |

运行治理：事件逐条落库可回放、取消在步骤边界生效、第 1–4 步检查点续跑、
每天/每周定时任务、确认与修正只追加不覆盖。

事件分类词表另有一套离线优化闭环：候选只允许增量关键词，先在冻结评测集上
做基线对照，通过门禁后才切换生效版本；历史运行按原版本留痕，可一键回滚。
评测集可用 `optimize harvest` 从运行事实导出待标注样本，标准答案仍只能人工填写。
文档级召回集用 `optimize harvest --kind docs` 导出未产出事实的文档，
`optimize recall` 只输出漏检 / 误报对照，不设门禁。
资讯检索词也支持版本固定：`data/optimizer/search_terms.json` 整份覆盖内置基线，
每路召回条数落运行统计；被判重复或不相关的候选也留痕（标题 / 链接 / 拒因，不含
正文），每路状态（成功 / 空结果 / 失败）与实际来源（东财 / 百度回退）也落统计，
可用 `optimize collect` 回看；
采集完成后还会做一次公告覆盖对照，缺口清单写入 `output/collect/<run_id>-gap.jsonl`，
同样只供抽样；同一公司的两次运行可用 `optimize depth <浅> <深>` 对照候选、文档与
事实增量，但新闻流随时间变化，只能作观察。检索词与留痕暂不参与门禁。
命令与门禁说明见 [docs/optimizer.md](docs/optimizer.md)。

## 报告结构

- **企业洞察报告（八节）**：公司速览 / 本期要点 / 事实清单 / 变化对比 / 判断 / 
  建议动作 / 待验证与数据缺口 / 方法与口径。
- **竞品追踪报告（五节）**：本期事实 / 横向对比 / 判断 / 建议 / 待验证与缺口。 
  横向对比是按事件维度（产能/订单/产品/价格/资本/业绩/政策/研发）的矩阵，
  单元格是该维度本期的事实条数，不是周环比。
- **专题研究（四段）**：事实 → 判断 → 建议 → 待验证与缺口。

细节与判定规则见 [docs/methodology.md](docs/methodology.md)。

## 架构

```text
api → runtime → services → infra → core
```

- `core`：日志、HTTP、文本、PDF、上下文预算
- `infra`：数据库（SQLite / PostgreSQL / MySQL）、巨潮 / 东财 / 百度资讯回退、模型服务、调度、离线版本存储
- `services`：抽取、分类、归因、成稿、政策解析、检索词版本、离线候选评测、召回评测、
  采集留痕、公告覆盖与深度对照（不感知 HTTP）
- `runtime`：七步管道、竞品对比、作业队列与断点
- `api`：七个领域接口（九个路由模块）、统一错误形状、SPA 托管

反向导入会被 `tools/selftest_arch.py` 拦下；前端 `features → shared`，功能之间互不 import。
详见 [docs/architecture.md](docs/architecture.md)。

## 配置

复制 `config.example.json` 为 `config.json`，或用环境变量（前缀 `MINGJIAN_`）：

| 变量 | 默认 | 作用 |
|---|---|---|
| `MINGJIAN_DAYS` | 30 | 回溯天数 |
| `MINGJIAN_MAX_COMPANIES` | 6 | 竞品对比家数上限 |
| `MINGJIAN_MAX_NEWS` / `MINGJIAN_MAX_ANN` | 45 / 40 | 资讯 / 公告条数上限 |
| `MINGJIAN_MAX_FETCH` | 12 | 正文抓取条数上限 |
| `MINGJIAN_LLM_BASE` / `_KEY` / `_MODEL` | 空 | 可选模型；仅当库里没有模型配置时作为兜底 |
| `MINGJIAN_DB_URL` | 空（本地 SQLite） | 可选数据库连接串，见 [docs/database.md](docs/database.md) |

大模型只用于润色「判断段」，事实与引用始终由程序生成并校验：引用编号必须真实存在
且与原判断逐条对应，句中的数字（中文数字先归一）与单位必须能在引用事实中找到，
否定与方向不得反转，整句与逐子句都要有足够重合；任一条不过就整批回退规则判断。
送进模型的事实清单只装被判断引用的事实，未被引用的不进 prompt——输入 token 随引用
数增长而非事实总数，引用事实也不会因上下文预算截断而缺失。
每轮运行都会把规则判断、采用版本、被拒原因与模型原始输出写入 `runs.judgments`
与 `output/judgments_<run_id>.json`，报告与运行统计同步标注；企业洞察页的
「判断留痕」可逐条对照，接口为 `/api/company/judgments/<run_id>`。
接入中心的「模型服务」内置 11 家 OpenAI 兼容服务商预设。

报告推送同样在接入中心配置（`settings` 表键 `push`）：飞书 / 钉钉 / 企业微信 /
Telegram / 通用 Webhook 五类渠道可同时启用多个，运行结束自动推送摘要（默认关闭），
企业洞察页也可对任一历史报告手动推送。摘要只带事实数、高置信与口径冲突、前三条
要点、缺口数与报告深链接，正文 1200 字内截断；单个渠道失败不影响其它渠道，逐渠道
结果写日志与审计。Webhook 地址与密钥只回尾号，编辑时留空沿用原值；飞书 / 钉钉填了
签名密钥就自动加签，Telegram 用 Bot Token + Chat ID。

页面里保存的配置与生效项存在同一个数据库的 `settings` 表（键 `llm`），重启自动
恢复，「停用」也会落库；旧 `config.json` 的 `llm_*` 只在库里还没有配置时迁移一次，
之后以库为准。数据库切换见 [docs/database.md](docs/database.md)。

## 开发与测试

以下命令均在项目根目录的 `.venv` 中执行（先按上面的方式激活虚拟环境）：

```bash
python -m pip install -e ".[dev]"
ruff check .
python tools/selftest_all.py        # 71 项断言：抽取 / 归因 / 矩阵 / 政策 / 调度 / 落库 / 官网 / 方言 / 推送 / TLS / 限速 / 离线优化
python tools/selftest_arch.py       # 分层边界、路由唯一性、前端隔离、视图体积棘轮
python -m mingjian db --counts      # 当前数据库、版本与各表行数
cd web && npm ci && npm run build   # vue-tsc 类型检查 + 构建
```

改动约定见 [CONTRIBUTING.md](CONTRIBUTING.md)。CI 在 Python 3.10 / 3.12、
Node 20 上跑同样的门禁，并做一次服务冒烟。

## 数据来源与合规

| 来源 | 用途 |
|---|---|
| 巨潮资讯 | 上市公司公告列表与公告 PDF |
| 东方财富 | 公司档案、主营构成、定期指标、资讯检索 |
| 百度资讯 | 东方财富资讯检索失败时的首屏回退 |
| 企业工商登记（可配置） | 企业检索、信用代码、法人、注册资本、经营状态与官网 |
| 工业和信息化部及省级工信主管部门官网 | 产业政策、专项资金等公告 |

内置同域请求间隔（默认 0.45 秒）、超时与重试；不含验证码识别、登录态伪造、
代理轮换等绕过访问控制的手段。使用时请遵守目标站点条款与所在地法律。

**输出不构成投资建议。** 完整条款见 [DISCLAIMER.md](DISCLAIMER.md)，
数据源细节见 [docs/data-sources.md](docs/data-sources.md)。

## 已知局限

- **只覆盖公开信息**，不能替代财务报表或内部经营数据。
- **非上市企业**没有公告与财报，只能靠自述档案、品类词反查线索与政府政策；
  财经媒体不报道的中小对手基本抓不到，缺口里会写明。
- **图片型 PDF 读不出**（无 OCR），保留链接供人工查阅；表格型 PDF 会退化为数字串。
- **事件分类是关键词规则**，对否定句（如「并未扩产」）可能误判，重要结论需人工抽检。
- **多源冲突只覆盖可量化的口径**：同一事件金额/规模不一致会被标记并扣分，
  跨指标（产能 vs 产量）与「谁更强」这类定性分歧仍需人工判断。
- **语义校验是词表级的**：中文数字、否定、方向与子句重合度能拦住常见改写，
  复杂同义替换仍需抽检。
- **检索受上游影响**：搜索引擎收录与限流会改变返回集合，两次结果可能不同。
- **单期不能判断趋势**：变化比对需要跑过两次以上，首次会标注「无历史基线」。
- **政府站点非标准接口**：31 个省级站点常有 1—3 个不可达（近期为甘肃、海南、安徽），
  程序记录健康状态，并区分「站点不可达」与「确实没有政策」。
- **指标缓存 30 分钟**（失败缓存 2 分钟）；看到数字没变先确认是不是缓存。
- **服务必须单进程运行**：作业状态与调度线程在进程内存里，不要用 `--workers N`。
- **定时任务依赖服务常驻**，停机期间错过的时点不补跑。
- **智能体是受控工作流**：技能按白名单执行，模型不选工具、不规划；记忆检索是字面词匹配。
- **离线优化目前只覆盖事件分类词表**：候选可由 `optimize draft` 从判错样本抽词生成，
  也可人工编写；都要先过离线评测与门禁，草案不会自动上线；
  检索词可版本化并记录每路召回，但暂不参与门禁；其他规则、提示词和报告模板尚未覆盖。
- **召回评测只覆盖已入库文档**：采集留痕虽有被拒条目，但只有标题与链接、没有正文，
  不能离线重跑相关性过滤，也还不能自动生成评测集；检索失败与空结果已分开留痕，
  但接口正常却没召回的仍不可见。
  文档级召回集仍需人工标注 `expected`（fact / none），也只能反映已采到的材料。
- **公告覆盖是代理指标**：公告有、资讯无是常态，覆盖比例低不等于漏采；缺口清单
  只用于人工抽样，不参与门禁。
- **深度对照不是严格对照**：两次运行的时间窗与新闻流不同，候选增量可能来自新资讯
  而非翻页加深，只能看边际，不能当因果；`--pages` 也只加深首路公司名查询。

## 项目结构

```text
mingjian/            后端包（core / infra / services / runtime / api / cli / server）
web/                 前端（Vue 3 + TypeScript + Vite，dist 为本地构建产物）
tools/               自测、架构门禁与离线评测样例
docs/                architecture / methodology / data-sources / policy-import / database / optimizer
sql/                 SQLite / PostgreSQL / MySQL 初始化脚本（由表结构定义生成）
materials/           政策材料投放目录（真实材料不入库）
data/ output/ logs/  运行时数据，已在 .gitignore 中排除
```

## 贡献

欢迎 issue 与 PR。提交前请跑通上面三条门禁，并遵守「不用真实公司名、不编事实、
缺失留空」的约定。安全问题请走私密渠道，见 [SECURITY.md](SECURITY.md)。

## 许可

[Apache License 2.0](LICENSE)。第三方组件与数据来源见 [NOTICE](NOTICE)，
行为准则见 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。