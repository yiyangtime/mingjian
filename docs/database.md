# 数据库与表结构

一句话结论：默认本地 SQLite，零配置即可跑；要接 PostgreSQL / MySQL 只改一个连接串，
业务代码不动。表结构定义一份，渲染成三种方言，既是初始化脚本的来源，也是运行期
建表的依据。

模型接入配置也存在同一个库里（`settings` 表，键 `llm`，单行 JSON）：页面保存即
落库，重启自动读回；「停用」也会落库，不会被环境变量或 `config.json` 顶回。旧
`config.json` 的 `llm_*` 只在库里还没有配置时迁移一次。

换到新库时库里没有这条记录，会从 `config.json` 再迁一次；不想让旧配置回流，
迁移后把 `llm_*` 清空即可。

## 一、为什么默认 SQLite

| 理由 | 具体说明 |
|---|---|
| 零配置 | 不装服务、不建库、不配连接串，`python -m mingjian serve` 就能跑 |
| 单文件备份 | 停服后复制 `data/mingjian.db` 即完整备份，不需要 DBA 参与 |
| 写入模式匹配 | 单进程多线程、写少读多；WAL + `synchronous=NORMAL` 已够用 |
| 可审计 | 十三张表全是普通表，没有存储过程与触发器，`sqlite3` 命令行即可查证 |

代价也要写清楚：**并发写入上限就是单进程**；库大了以后 `VACUUM` 会短暂锁表；
多实例部署必须换库（见第七节）。

## 二、切换到 PostgreSQL / MySQL

### 1. 装驱动

| 目标 | 安装 | 连接串前缀 |
|---|---|---|
| PostgreSQL 12+ | `pip install -e ".[postgres]"` | `postgresql://` |
| MySQL 8.0+ | `pip install -e ".[mysql]"` | `mysql://` |
| SQLite | 无需安装（标准库） | `sqlite://` |

### 2. 建库

脚本只建表与索引，不建库。PostgreSQL 先 `createdb mingjian`；MySQL 先

```sql
CREATE DATABASE mingjian CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
```

### 3. 配连接串

`config.json`：

```json
{ "db_url": "postgresql://user:pass@127.0.0.1:5432/mingjian" }
```

或环境变量（优先级高于 `config.json`，旧部署可用 `AGENT_DB_URL`）：

```bash
MINGJIAN_DB_URL=mysql://user:pass@127.0.0.1:3306/mingjian
```

留空或删掉即回到 `data/mingjian.db`。密码在运行治理页、`mingjian db` 输出与日志里
都会隐去，只显示 `user:***@host`。

### 4. 建表

三种方式等价，都是 `CREATE TABLE IF NOT EXISTS`，重复执行安全：

- 首次启动自动建（`store.init()`）；
- `python -m mingjian db --init`；
- 手工执行 `sql/postgres.sql` 或 `sql/mysql.sql`。

### 5. 验证

```bash
python -m mingjian db --counts                  # 方言、目标、版本、各表行数
python tools/dbcheck.py --url "postgresql://..." --init
```

`dbcheck` 走的是服务运行期同一套连接层（转译、行工厂、保留字处理都一致），
做一轮中文写入—读取—删除探针，退出码：0 通过、2 缺驱动、3 连接失败、
4 建表失败、5 探针失败。

### 6. 回退

删掉 `db_url` 重启即回到 SQLite。两套库的数据不互通，迁移需自行导出导入——
项目目前没有内置迁移工具，这是有意的：避免在缺少真实库验证时先写一套半成品。

## 三、初始化脚本

`sql/` 下三份，全部由表结构定义生成，不要手工改：

| 文件 | 目标 | 用法 |
|---|---|---|
| `sql/sqlite.sql` | SQLite 3 | `sqlite3 data/mingjian.db < sql/sqlite.sql` |
| `sql/postgres.sql` | PostgreSQL 12+ | `psql "$DATABASE_URL" -f sql/postgres.sql` |
| `sql/mysql.sql` | MySQL 8.0+ | `mysql -u USER -p DB < sql/mysql.sql` |

MySQL 的方言差异已在生成器里处理：保留字 `key` 加反引号、文本主键落
`VARCHAR(255)`、正文与载荷落 `LONGTEXT`、`memory.refs` 落 `VARCHAR(2048)`
以支持默认值；MySQL 不支持 `CREATE INDEX IF NOT EXISTS`，索引因此内联在建表语句里。

## 四、表结构只有一个来源

```text
mingjian/infra/schema.py   TABLES：列、类型、默认值、主键、唯一约束、索引
        ├── 渲染 sqlite   → sql/sqlite.sql
        ├── 渲染 postgres → sql/postgres.sql
        └── 渲染 mysql    → sql/mysql.sql
```

运行期建表语句仍在 `mingjian/infra/store.py` 的 `SCHEMA`（带中文列注释，便于阅读）。
两份定义之间有强一致校验：

```bash
python tools/dbinit.py --check     # 建两个内存库，逐表比对列与索引
```

不一致时返回非零并打印差异，CI 会跑这一步。**改表结构必须同时改两处**，否则
构建失败——这是有意的，防止运行时和脚本各说各话。

重新生成脚本：

```bash
python tools/dbinit.py --all                          # 写回 sql/
python tools/dbinit.py --dialect postgres --stdout    # 只看不写
```

## 五、适配层处理了哪些方言差异

业务代码只写一种 SQL（`?` 占位符、`sqlite3.Row` 风格取值），差异收敛在
`infra/db.py`：

| 差异 | SQLite | PostgreSQL | MySQL |
|---|---|---|---|
| 占位符 | `?` | `%s`（执行时统一转译，字符串字面量里的 `?` 不动） | 同 PG |
| 自增主键 | `INTEGER PRIMARY KEY AUTOINCREMENT` | `BIGSERIAL PRIMARY KEY` | `BIGINT AUTO_INCREMENT` |
| 覆盖写 | `INSERT OR REPLACE` | `ON CONFLICT (...) DO UPDATE` | `REPLACE INTO` |
| 保留字 `key` | 无需处理 | 不保留 | 反引号，按完整标识符替换，不误伤 `dedupe_key` |
| 模糊搜索 | `LIKE`（ASCII 不区分大小写） | `ILIKE`（保持行为一致） | `LIKE`（默认排序规则不区分） |
| 事务 | 隐式事务 | 连接自动提交 + `tx()` 显式 `BEGIN` | 自动提交 + `START TRANSACTION` |
| 行对象 | `sqlite3.Row` | `dict_row` | `DictCursor` |
| 库体积 | 文件大小 | 0（无单一文件） | 0 |
| 字面量 `%` | 原样 | 转义成 `%%` | 同 PG |

另外两点运行时处理：连接按线程缓存（连接数等于常驻线程数）；建表 DDL 每进程
只执行一次（`store.init()` 有缓存，`force=True` 可强制重建）。

## 六、验证到什么程度

- **SQLite**：全链路实测——自测 54 项、服务启动冒烟、`dbinit.py --check`
  比对 13 张表 / 21 个索引。
- **PostgreSQL / MySQL**：DDL、占位符转译、保留字与百分号处理都有单元断言，
  但**没有在真实实例上跑过**（开发机没有这两个服务）。首次接库请务必
  `tools/dbcheck.py --init`；失败会打印方言、目标、版本与具体错误。

换句话说：代码路径已就绪，生产可用性要由目标环境的那次自检来证明。

## 七、换库不会解除单进程限制

作业队列、取消信号、调度线程都在进程内存里，定时任务也依赖进程常驻，
`--workers N` 依旧禁止。换库解决的是「运维统一、备份走 DBA、数据量更大」，
不是「水平扩展」。

## 八、生产建议

- **备份**：SQLite 停服复制单文件；服务端库走各自的备份体系。备份前确认
  `data/mingjian.db-wal` 已合并。
- **权限**：SQLite 只需文件读写；服务端库给 `SELECT/INSERT/UPDATE/DELETE`
  与建表权限即可，不需要 DDL 以外的超级权限。
- **连接数**：每个常驻线程一条连接（API 线程池、作业线程、调度线程），
  按部署规模预留；连接不跨线程共享。
- **迁移**：没有版本化迁移，首次启动一次建全表。已有库新增字段请改
  `store.SCHEMA` 与 `schema.TABLES` 两处，并给存量库补 `ALTER TABLE` 语句。
- **审计**：`audit` 表只追加，不要为了瘦身删历史；需要归档时导出 CSV 再清。
- **密钥**：`settings` 表里的模型配置含 API Key 明文，数据库备份、只读账号与
  慢查询日志都应按密钥对待；页面与接口不会回传明文，只给尾号。

## 九、新增字段或表的流程

1. 改 `mingjian/infra/schema.py` 的 `TABLES`（列、类型、默认值、索引）。
2. 同步改 `mingjian/infra/store.py` 的 `SCHEMA`。
3. `python tools/dbinit.py --check` 直到通过。
4. `python tools/dbinit.py --all` 重新生成 `sql/*.sql`。
5. `python tools/selftest_all.py` 与 `python tools/selftest_arch.py`。
6. 在 `docs/architecture.md` 的存储模型表里补一行，在 `CHANGELOG.md` 记一条。