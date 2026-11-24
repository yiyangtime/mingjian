# 离线候选与门禁上线

这套机制解决的是“规则改了以后，怎么证明它真的更好，而不是把另一批样本弄坏”。

它只做受控优化：候选是声明式配置，不是代码；模型或人可以提出候选，但候选必须经过
离线评测和门禁，才能切换为生效版本。当前支持事件分类词表的增量候选。

## 一、边界

允许的候选：

- 给某个事件类型增加关键词；
- 版本号、基线版本、简短说明。

不允许的候选：

- 执行代码、SQL、任意正则或动态导入；
- 替换完整规则集；
- 直接修改事实、证据、置信度公式；
- 修改报告结构、数据库结构或权限边界；
- 由模型直接决定上线。

候选可以由人、外部工具或 `optimize draft` 生成。`draft` 从评测集的判错样本里按双字
切分抽词，每条错误最多贡献一个词，并在全量样本上预筛：必须修好来源样本，且不弄坏
基线判对的样本。草案只落到 `tools/optimizer/`，不写版本库、不改生效规则，仍需人工
复核后走 eval / promote。无论候选来自哪里，生成器都不接触运行期数据库。

## 二、目录

运行产物都在 `data/optimizer/`，不提交到 Git：

```text
data/optimizer/
  active_event_rules.json       当前生效版本（唯一指针）
  versions/<version>.json       通过门禁后归档的完整规则
  evaluations/<version>-*.json  每次离线评测的完整结果
  audit.jsonl                   只追加的评测、上线、回滚记录
  search_terms.json             可选：人工编辑的检索词（见第八节）
```

没有 `active_event_rules.json` 时，系统使用内置规则，版本视为 `baseline`。
文件损坏或规则校验失败时也回退到内置规则，不阻断情报运行。

## 三、候选格式

```json
{
  "kind": "event_rules",
  "version": "cand-20261124-01",
  "base_version": "baseline",
  "description": "补充联合研发与战略投资的判类词",
  "add_keywords": {
    "product": ["联合研发"],
    "finance": ["战略投资"]
  }
}
```

约束：

- `version` 只能含字母、数字、点、下划线和短横线，3—64 位；
- `add_keywords` 只能使用已有事件类型，不能写 `other`；
- 单个类型一次最多新增 20 个词，总数最多 60 个，单词不超过 40 字；
- 候选不能重复，不能包含换行等控制字符；
- `base_version` 建议显式填写。填写后必须与当前生效版本一致，否则拒绝上线。

## 四、评测集

评测集是人工标注资产，默认样例在 `tools/optimizer/event_cases.jsonl`，每行一条：

```json
{"id":"C01","title":"某某企业发布新一代控制器","expected":"product","split":"dev"}
```

- `title` 必填，`body` 可选；
- `expected` 是事件类型，也允许 `other`；
- `split` 为 `dev` 或 `holdout`；保留集用于检验是否过拟合；
- 评测只读本地样本，不联网、不启动采集管道。

实际使用时，应把真实运行中确认过的事实和人工修正逐步补进评测集。不要用未经验证的
模型输出当标准答案。

`optimize harvest` 负责把已有运行事实导出成待标注样本：`expected` 一律留空，
`hint.predicted` 只是当时的运行期判定，供人工对照与发现分歧，不能直接当作答案。
标注时读 `hint.url` 原文后填写 `expected`，保留集样本用 `--split holdout`。
待标注文件的 `expected` 为空，直接拿去跑评测会被拒绝——这是有意的：评测集不能被
模型自己的判定回填。

### 文档级召回集

分类评测只覆盖“已经抽出事实”的文档。要评“该抽到而没抽到”，另有
一套文档级样本，用 `optimize harvest --kind docs` 导出：

```json
{"id":"D0001","title":"...","body":"...","expected":"","split":"dev",
 "hint":{"kind":"news","evidence_level":"full","url":"https://...",
         "query":"某某企业 扩产 产能","fact_count":0,"predicted":"none",
         "names":["某某企业"]}}
```

- 默认只导“未产出事实”的文档（盲区）；`--include-covered` 连同已产出事实的
  文档一起导，用于检查误报；
- 跳过例行公告、正文过短、无主体名的文档：要么没有标注价值，要么无法离线重跑；
- `expected` 只能填 `fact` 或 `none`，问的是“该文档是否应产出事实”，不是事件类型；
- `hint.names` 是采集时的主体名，评测时用同一组名字做主体核验；
- 判定与运行期同口径：抽得出事实且置信度不低于 `min_fact_conf` 才算产出。

`optimize recall` 读取该评测集，输出准确率、漏检清单与误报清单。它不是门禁：
检索词或抽取规则调整前后各跑一次，用来比较是否把漏检补上、是否引入新的误报。

## 五、命令

```bash
# 只看当前生效规则的基线表现
python -m mingjian optimize baseline

# 把运行事实导出成待标注样本；expected 留空
python -m mingjian optimize harvest

# 把未产出事实的文档导出成召回待标注样本
python -m mingjian optimize harvest --kind docs

# 文档级召回评测：只判是否产出事实，输出漏检 / 误报对照
python -m mingjian optimize recall

# 查看某次运行的采集留痕：每路召回与被拒条目
python -m mingjian optimize collect R20261118103000-1234

# 对照同一公司加深检索前后的候选、文档与事实增量
python -m mingjian optimize depth R20261118103000-1234 R20261119103000-5678

# 从评测集判错样本生成候选草案；只落盘，不改生效规则
python -m mingjian optimize draft

# 离线对照评测；不改变生效规则
python -m mingjian optimize eval --candidate tools/optimizer/candidate.example.json

# 重新评测，门禁通过后归档并切换为生效版本
python -m mingjian optimize promote \
  --candidate tools/optimizer/candidate.example.json --actor 运营

# 回滚到 baseline 或任一已归档版本
python -m mingjian optimize rollback baseline --actor 运营

# 查看当前版本、版本数、检索词版本和最近评测
python -m mingjian optimize status

# 查看版本、评测与审计记录
python -m mingjian optimize history
```

`eval` 在门禁不通过时返回非零退出码，但会保留评测报告，便于定位错误。
`promote` 不读取上一次评测的结论，而是重新跑一遍；因此不能用旧报告绕过门禁。
`draft` 的输出文件可直接作为 `--candidate` 传给 `eval`；它不会触发 `promote`。
`harvest` 需要数据库里有已完成的运行事实或文档；它只读事实与来源文档，不联网、
不改规则。
`harvest --kind docs` 的样本同样不能直接评测：`expected` 为空会被拒绝。
`recall` 的评测报告以 `recall-` 开头，与其他评测报告同目录。
`collect` 只读库里的运行统计和 `output/collect/` 下的明细，不访问网络。
`depth` 只读两次运行的统计与文档，报告以 `depth-` 开头；它不联网，也不改页数。

## 六、门禁

所有条件必须同时满足：

| 条件 | 要求 |
|---|---|
| 样本量 | 至少 12 条，其中保留集至少 4 条 |
| 新增错误 | 不允许出现基线正确、候选错误的样本 |
| 整体准确率 | 不低于基线 |
| 宏平均 F1 | 不低于基线减 0.01 |
| 保留集准确率 | 不低于基线减 0.02 |
| 分类型召回 | 任一类型下降不得超过 15 个百分点 |
| 类型分布 | 任一类型的预测条数偏移不超过 max(2, 样本量 × 10%) |
| `other` 比例 | 不高于基线加 5 个百分点 |

门禁没有“强制通过”参数。需要放宽阈值时，应先改评测集或门禁代码并重新评审，不能
在单次命令里绕过。

文档级召回评测不参与门禁：它只有“产出 / 不产出”两档，且样本依赖人工标注，
先作为对照基线。

## 七、运行期行为

- 每次运行开始时读取一次生效版本；
- 版本号写入 `runs.params` 与检查点；续跑使用原版本，不被中途上线串味；
- 历史运行的事实与报告不重算、不覆盖；
- 新版本只影响之后开始的运行；
- 分类标签仍使用内置类型名；候选只增加匹配词，不改变证据句、事实归属和置信度公式。

检索词同样在运行起点固定，但不走门禁，见下一节。

## 八、检索词版本与采集留痕

检索词决定采集阶段能发现什么，比分类词更靠前。它目前只做到“声明式 +
运行期固定 + 召回与过滤留痕”，**没有上线门禁**：判断“该召回而未召回”需要带标注的
真实语料，现在还没有。

- 内置基线：`infra/sources/eastmoney.py` 的 `DEFAULT_TERMS`（公司名一路 +
  事件后缀四路）；
- 本地覆盖：`data/optimizer/search_terms.json`，人工编辑，不提交到 Git；

```json
{"version": "terms-20261124-01", "terms": ["扩产 产能", "订单 合作 中标", "业绩 财报", "价格 降价"]}
```

- 校验：2—8 路，每路不超过 40 字，重复项自动去重；文件缺失或任一项非法时，
  整份回退内置基线，不做部分生效；
- 运行期：起点读取一次，版本写入 `runs.params` 与检查点；每路实际召回条数写入
  `runs.stats.search_recall`；
- 采集留痕：每路的候选 / 入围 / 重复 / 不相关条数，以及 `status`（ok / empty /
  error）、`error`（含 HTTP 状态码）与 `ms`，写入 `runs.stats.collect.queries`；
  失败路数与无结果路数汇总到 `collect.failed` / `collect.empty`；
  被丢弃的条目（标题、链接、来源、发布时间、拒因 `duplicate` / `irrelevant`）
  逐行写入 `output/collect/<run_id>.jsonl`，不存正文；
- 失败语义：搜索失败不再与“没有结果”同形——失败路不计入召回，日志与
  `optimize collect` 都会标出；全部路失败时运行错误里记一条；
- 公告覆盖对照：采集完成后逐条公告找最相近的资讯，按“公告核心词的 bigram 被
  资讯覆盖的比例”打分；低于阈值的缺口写入 `output/collect/<run_id>-gap.jsonl`，
  汇总写入 `runs.stats.coverage`（公告数 / 覆盖数 / 比例 / 阈值）；
- 回看：`optimize collect <run_id>` 同时输出每路状态、公告覆盖缺口与被拒条目，
  用于区分“检索失败”“确实没结果”和“召回了但被主体过滤误杀”；
- 深度对照：`optimize depth <浅> <深>` 比较同一公司两次运行的候选 / 入围 / 文档 /
  事实增量与逐路差异，报告存为 `depth-*.json`；加深要在运行前用 `run --pages`
  指定（只作用于首路公司名查询），事后不能补；
- 边界：公告与媒体资讯不是同一集合，小公告无人报道很正常，覆盖比例只供人工
  抽样，不参与门禁；接口失败已可见，接口正常但根本没召回的仍不可见；
- 续跑：若第 2 步已完成（材料已入库），保留当次采集的版本号；若要从第 2 步重跑，
  则取当次生效的检索词并同步版本号；
- 没有 `promote` / `rollback`：检索词改动的风险（多召回噪声、漏掉主题）目前无法
  离线度量，先只攒对照数据。

## 九、当前限制

- 只覆盖事件分类词表；检索词可版本化并记录召回，但尚未纳入门禁；政策字段、
  提示词和报告模板仍未覆盖；
- 评测集与规则版本都在本机文件目录，适合当前单实例运行；多实例部署应把版本指针
  与评测集迁到共享存储，并加并发写保护；
- `optimize draft` 是规则抽词，不是模型提议，也不判断词在真实语料里的泛化程度；
  草案仍需人工复核，并由门禁决定能否上线；
- 深度对照不是严格对照：两次运行的时间窗与新闻流不同，增量可能来自新资讯而非
  翻页加深，只能看边际，不能当因果；翻页也只作用于首路公司名查询；
- 召回评测只覆盖已入库文档；采集留痕虽有被拒条目，但只有标题与链接、没有正文，
  不能离线重跑相关性过滤，也还不能自动生成评测集；
- 还没有基于历史文档的端到端回放与影子流量，这两项需要单独的运行时能力。

## 十、与数据库的关系

离线优化不新增数据库表，也不依赖模型配置。除 `harvest` 与 `collect` 需要读取
运行库外，`optimize` 子命令不初始化数据库，评测和上线都在本地文件完成；情报管道
仍按原方式使用 SQLite / PostgreSQL / MySQL。