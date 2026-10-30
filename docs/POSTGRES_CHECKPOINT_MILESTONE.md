> 后续更新：两药报告已接入持久化节点并通过强杀、网页与真实资料验收，见[两药恢复增量](PAIR_CHECKPOINT_MILESTONE.md)。本文保留原单药阶段的实现范围和历史结果。

# PostgreSQL运行队列与单药报告checkpoint恢复

2026-10-26。本轮交付可选PostgreSQL运行库、LangGraph节点持久化、网页恢复入口及实际进程强杀验收。默认SQLite部署仍可用；本轮没有迁移用户已有任务，没有将ETCM/药典来源数据库改成PostgreSQL。

## 实际范围

`FastAPI /api/research/jobs/report` → PostgreSQL队列/领取/租约 → `ResearchWorker` → `AsyncPostgresSaver` → 现有LangGraph文本与结构化并行分支 → 合并报告 → 队列结果与SSE → 历史页。

- `PostgresJobStore`复用既有队列契约，通过连接适配使用参数化SQL、事务和全局事务advisory lock，保持幂等键、owner隔离、revision CAS、30秒租约、队列20/运行4上限和取消优先。这是保守的串行写入设计，不是高吞吐SKIP LOCKED队列。
- 官方 `langgraph-checkpoint-postgres==2.0.16` 与现有 `langgraph-checkpoint==2.0.19`、`langgraph==0.3.9`兼容，本轮实际安装验证；psycopg/binary/pool固定3.2.6。没有升级整套LangGraph。
- 只对 **cross_source单药只读报告** 保存/恢复checkpoint。两药报告、疾病任务继续使用原流程，不具有节点恢复承诺。模型循环和外部工具调用不自动重放。
- 线程ID由owner与job ID组合哈希，客户端不传thread ID。恢复持有独立会话锁；队列租约和revision同时防止旧worker提交。只允许interrupted且标有checkpoint的任务恢复；取消任务、无checkpoint旧任务不自动恢复。
- checkpoint绑定请求、当前`tcm/*.py`代码、ETCM来源SQLite内容、药典索引/manifest内容及检索配置。绑定改变时拒绝恢复，不混用不同版本已完成节点。当前路径也进入绑定，所以多主机不同路径部署尚不能直接保证恢复。模型权重不单独全量散列，沿用检索器的资产校验。
- 数据库中保存有界报告状态及原文片段，不保存整份PDF。删除终态PostgreSQL任务时在同一事务删除任务、事件、checkpoint、pending writes、blobs和绑定；队列和checkpoint必须配置为同一专用数据库。

实现文件：`postgres_jobs.py`、`report_checkpoint.py`，以及`research_jobs.py`、`cross_source_report.py`、`research_job_api.py`、`research_runs.js`的可选接入。采用官方[异步PostgresSaver的setup与持久化接口](https://raw.githubusercontent.com/langchain-ai/langgraph/main/libs/checkpoint-postgres/langgraph/checkpoint/postgres/aio.py)；本轮以安装的固定版本和实际验收为准，不假设主分支新API存在。

## 已完成的真实验收

1. **进程强杀**：合成文本分支写入pending checkpoint后SIGKILL worker；回收过期租约并恢复。文本仅执行1次，结构化分支执行2次。完成节点不重跑，未完成只读步骤允许重试，不宣称外部副作用exactly-once。
2. **隔离与一致性**：其他owner不能恢复；旧worker不能提交；资料指纹改变会在调用分支前被拒绝；事件revision连续；删除时checkpoint和绑定一并消失。
3. **数据库并发**：8个独立连接并发领取只得到4个不同任务；迁移重复运行不改变schema。未做多主机压测、容灾或吞吐提升对照。
4. **迁移回滚**：合成终态SQLite历史导入空PostgreSQL目标，再导出新SQLite文件，任务/事件保持一致；非空目标拒绝导入，活跃任务不能迁移。
5. **真实HTTP与网页**：合成分支运行在真实预览服务，强杀整个HTTP进程，等待真实31秒租约过期，再启动同一状态目录。原cookie在历史页点击“继续执行”，正常完成；文本1次、结构化2次，JavaScript错误0。
6. **真实本地资料**：7884使用PostgreSQL运行库，三条当归报告的保存/用途/展示优先级、出处和刷新回看通过，无外部模型调用。

首次网页测试暴露历史列表漏传recovery标记，已修复并补API回归。第二次合成测试与真实预览共用了队列，任务被另一个配置的worker领取；最终改用独立`tcm_browser`验收数据库通过。这说明同一个运行队列的worker必须使用同一代码、资料配置和执行模式，不能把合成测试worker混入真实预览池。失败日志保留在本地var目录，审计摘要明确记录这两次失败。

## 当前本机部署

- 真实资料预览：http://127.0.0.1:7884/ 。普通聊天仍是原来的本地模式；checkpoint能力在本地研究的单药报告中。
- PostgreSQL 16.15从Ubuntu官方软件包解包至 `/tmp/tcm-postgres-runtime`，没有安装系统服务。
- 数据目录 `var/postgres_checkpoint_v1/pgdata`；Unix socket目录`var/postgres_checkpoint_v1/socket`权限700；不监听TCP。仅供此用户本机验收。
- `tcm_runtime`用于可选预览，`tcm_browser`用于合成强杀验收；验收HTTP服务已退出。
- Docker的WSL连接当前不可用，因此没有Docker发布或容器验收。`/tmp`中的二进制和虚拟环境不是稳定生产安装，系统重启后需重新准备运行环境与启动数据库。

本机服务已启动时，重新启动预览：

```bash
export RESEARCH_JOB_DSN='host=/home/zry/workspace/Multi-Agent-Medical-Assistant/var/postgres_checkpoint_v1/socket port=55432 user=zry dbname=tcm_runtime'
export RESEARCH_CHECKPOINT_DSN="$RESEARCH_JOB_DSN"
/tmp/tcm-ci-mail-env/bin/python -m scripts.preview_local_tasks \
  --port 7884 --state var/postgres_checkpoint_v1/preview_state \
  --diseases var/disease_catalog_v1/build/diseases.sqlite3
```

停止后恢复本机PostgreSQL（目录已经初始化，不重复initdb）：

```bash
LD_LIBRARY_PATH=/tmp/tcm-postgres-runtime/usr/lib/x86_64-linux-gnu \
/tmp/tcm-postgres-runtime/usr/lib/postgresql/16/bin/pg_ctl \
  -D var/postgres_checkpoint_v1/pgdata -l var/postgres_checkpoint_v1/postgres.log \
  -o "-k /home/zry/workspace/Multi-Agent-Medical-Assistant/var/postgres_checkpoint_v1/socket -p 55432 -c listen_addresses=''" start
```

## 新环境迁移与回滚

1. 安装 `requirements-checkpoint-postgres.txt`，准备专用PostgreSQL数据库。生产环境认证/权限/备份需要自行部署；不要照搬本机Unix socket trust配置开放网络。
2. 设置两个相同DSN环境变量，执行 `python -m scripts.migrate_research_postgres`。它显式建立版本化队列表及官方checkpoint表；运行时不自动迁移未知schema。
3. 保留会话数据库和cookie配置，停止所有worker、排空队列；如需迁移历史，执行 `python -m scripts.transfer_research_jobs --sqlite 原队列.sqlite3`。目标必须为空，原SQLite只读、不会删除。当前本机仅验证了合成历史迁移，未执行用户历史迁移。
4. 回滚前停止worker，执行 `python -m scripts.transfer_research_jobs --export --sqlite 新回滚队列.sqlite3`。然后清除两个DSN，设置`RESEARCH_JOB_DB`为导出的文件，再启动原模式。回滚保留历史，但SQLite模式不恢复PostgreSQL中的中间checkpoint；不要把其“继续执行”失败理解为数据丢失。

数据库必须与工作者匹配，活跃任务须先正常停止/等待租约回收。导出导入不是在线双写，不是全数据库备份；保留PostgreSQL原库以便恢复checkpoint，备份策略仍需补齐。不同owner访问权依赖原会话身份，移动队列时不能丢弃原会话库。

本轮没有Git提交、push或发布镜像。原始资料、数据库目录、模型权重不进入Git。审计摘要见 `audits/postgres_checkpoint_v1/`。

## 尚未完成

两药嵌套子图和模型工具循环的checkpoint、外部调用不确定状态的幂等策略、多主机/滚动升级恢复、生产凭证管理与数据库备份演练、高吞吐领取策略与负载对照。J04已补上本地可恢复单药报告闭环，不能标为整个执行体系生产化完成。

## 最终回归与审计

119模块998项离线测试全部通过，0跳过；358个Python文件语法检查通过。离线回归不等于真实模型业务准确率，全量私有数据评测和在线模型评测未执行。本轮外部模型调用0次。

审计见[总览](../audits/postgres_checkpoint_v1/summary.json)、[回归](../audits/postgres_checkpoint_v1/regression.json)、[强杀恢复](../audits/postgres_checkpoint_v1/acceptance.json)、[网页恢复](../audits/postgres_checkpoint_v1/browser.json)、[并发与迁移](../audits/postgres_checkpoint_v1/queue.json)和[本地资料页面](../audits/postgres_checkpoint_v1/real_report_browser.json)。源文件哈希记录在同目录source_manifest.json；不归档数据库或原始资料。
