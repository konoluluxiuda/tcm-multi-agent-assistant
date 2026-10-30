> 后续P0-3新增`--plans`保存新聊天journal，并核对SQLite任务中的外置证据引用；中断新聊天遗漏journal时拒绝备份。范围与恢复限制见[新聊天生命周期](RESEARCH_PLAN_LIFECYCLE.md)。

> 后续更新：现可通过可选`--loops`参数联合备份循环journal；未传时不包含该文件。本文保留原阶段验收，新增恢复验证见[循环生命周期与备份](RESEARCH_LOOP_LIFECYCLE_MILESTONE.md)。

# 核心运行状态备份与恢复演练

2026-10-27。本轮补齐会话身份、项目记忆、任务和checkpoint的联合备份/恢复，不新增框架，不修改现有业务数据库。验收全部使用隔离的合成资料。

## 覆盖范围

| 数据 | 是否包含 | 恢复后验证 |
|---|---|---|
| ConversationStore | 是，完整SQLite快照 | 原cookie签名、消息、revision及删除标记保留 |
| ProjectStore | 是，完整SQLite快照 | owner、项目内容、revision保留，过期revision仍被拒绝 |
| SQLite任务队列 | 可选，与PostgreSQL二选一 | 任务、事件、终态结果及出处保留 |
| PostgreSQL队列与LangGraph checkpoint | 可选，完整专用运行库 | jobs/events、绑定、pending writes、blobs及checkpoint一起恢复 |
| 药典、ETCM、PDF、模型资产 | 不包含 | 继续由来源目录管理，恢复执行须保持兼容的路径、版本和配置 |
| 旧版研究任务/历史、收藏、PubMed缓存 | 本轮不包含 | manifest明确列出，不宣称全站备份 |

签名密钥保存在会话数据库内，不能只迁移项目和队列后创建新的会话库。新目录默认生成的cookie名称也可能不同，恢复部署必须使用manifest记录的`cookie_name`，否则浏览器不会提交原cookie。项目语义向量索引是请求级临时索引，无需备份。

## 一致性策略

采用停机备份，不宣称在线多数据库事务：先停止所有写入该状态的HTTP服务和worker，处理queued/running任务，再传`--workers-stopped`声明。工具不能替用户判断所有服务是否已停机。

SQLite源保持`BEGIN IMMEDIATE`写锁，使用SQLite backup API读取已提交WAL，不复制主文件凑快照。PostgreSQL拒绝queued/running记录、检查专用运行库表集合、持有共享表锁，并将导出的事务snapshot传给`pg_dump`。中断任务和pending checkpoint可一起备份；数据库仍标记running时须先按现有租约回收流程处理，再停机备份。

输出目录700、数据库/dump/manifest文件600。manifest包含SHA-256、行数、cookie名称和范围，不记录DSN、签名密钥正文、聊天内容或模型密钥。完整备份文件本身含会话签名和资料，应存放在受控位置；本轮没有加密、异地复制、自动轮换或到期删除。

## 恢复策略

只接受本机可信来源的备份包。摘要校验用于发现损坏，不是备份发布者身份签名；PostgreSQL dump是可执行数据库归档，不能用来接收陌生人上传的备份。

恢复目标必须是不存在的新目录，以及已创建但无用户对象的空数据库。工具不会清空已有库；使用`pg_restore --single-transaction --exit-on-error`，校验表和行数后写恢复回执，不自动启动服务。SQLite后端不需要PostgreSQL。

恢复目录与PostgreSQL之间没有跨系统原子提交：若数据库已恢复但后续校验/回执写入失败，可能留下已填充的隔离目标库，工具不会自动删库。检查后换一个新空目标重试，不能把失败目录接入生产。

恢复不会改变消息时间戳，应用继续按原TTL判断过期。恢复旧备份是回到旧时间点，不会自动合并备份之后的删除或修改。原cookie还需存在浏览器中；没有跨设备账号恢复承诺。

checkpoint恢复还受请求、代码、资料与配置指纹约束。本轮演练的源库和目标库使用同一套合成执行契约；跨版本、跨路径、多主机滚动升级恢复未实现。

## 使用命令

使用已装项目依赖的Python；PostgreSQL模式还需既有`requirements-checkpoint-postgres.txt`与匹配版本的`pg_dump`/`pg_restore`。本机演练使用PostgreSQL16工具，路径在`/tmp/tcm-postgres-runtime/usr/lib/postgresql/16/bin`，运行时需要其`LD_LIBRARY_PATH`。

以下均为模板，不会自动识别或备份所有预览目录。必须填写本次部署实际使用的数据库文件和cookie名称。

```bash
# SQLite运行库：停机并处理活跃任务后执行。
python -m scripts.backup_runtime backup \
  --memory /实际目录/conversations.sqlite3 \
  --projects /实际目录/research_projects.sqlite3 \
  --jobs /实际目录/research_jobs.sqlite3 \
  --cookie-name 实际会话cookie名称 \
  --workers-stopped --out /新备份目录

python -m scripts.backup_runtime inspect --bundle /备份目录
python -m scripts.backup_runtime restore \
  --bundle /备份目录 --target /不存在的恢复目录

# PostgreSQL运行库：将--jobs替换为--dsn-env；DSN只通过环境读取。
# RUNTIME_SOURCE_DSN为现有专用库；RUNTIME_TARGET_DSN必须是新空库。
python -m scripts.backup_runtime backup \
  --memory /实际目录/conversations.sqlite3 \
  --projects /实际目录/research_projects.sqlite3 \
  --cookie-name 实际会话cookie名称 --workers-stopped \
  --dsn-env RUNTIME_SOURCE_DSN --pg-bin /PostgreSQL工具目录 \
  --out /新备份目录
python -m scripts.backup_runtime restore \
  --bundle /备份目录 --target /不存在的恢复目录 \
  --dsn-env RUNTIME_TARGET_DSN --pg-bin /PostgreSQL工具目录
```

恢复后部署设置：`CHAT_MEMORY_DB`指向`memory.sqlite3`，`RESEARCH_PROJECT_DB`指向`projects.sqlite3`；SQLite队列用`RESEARCH_JOB_DB=.../jobs.sqlite3`，PostgreSQL用两个相同的`RESEARCH_JOB_DSN`和`RESEARCH_CHECKPOINT_DSN`；`CHAT_COOKIE_NAME`沿用manifest。保留相同来源资产和执行配置。在隔离实例验证后再切换服务；本轮未切换现有7892预览。

## 验收证据

- 43项回归通过：本轮备份8、现有会话备份13、项目8、两药checkpoint5、恢复权限3、队列6。
- SQLite验证：签名连续、owner隔离、revision冲突、出处保留、WAL、原库不变、文件权限、活跃任务拒绝、已有目录拒绝、SHA损坏/路径穿越/符号链接拒绝。
- 实际PostgreSQL演练：新建两个隔离库，保存两药任务的三个已完成查询，停止执行后备份到custom dump，恢复到新库，在新进程用原cookie继续执行。
- 查询次数为左药文本1、左药属性1、右药文本1、右药属性2、共同靶点1。恢复前后的事件及绑定一致，恢复完成后双方出处存在；原库任务仍为interrupted。
- 真实验证运行中队列不能备份、非空库不能恢复。外部服务调用0次，无真实资料外发。
- 合成中断时固定版本psycopg曾记录一次pipeline回滚警告；已落盘写入、关闭锁、恢复后的绑定与结果核验均通过，未通过屏蔽错误来绕过测试。该警告记录为依赖层后续排查项。

原始本地演练目录`var/runtime_backup_v1/final_drill/`，不进入Git。脱敏验收摘要和测试输出见`audits/runtime_backup_v1/`。核心实现为`tcm/runtime_backup.py`、CLI为`scripts/backup_runtime.py`，复现脚本为`scripts/check_runtime_backup.py`。

## 下一主任务

推进模型工具循环的持久化执行设计和合成故障验收：区分未发出、已发出未确认、已收到并落盘的调用状态，保存预算与工具结果，避免恢复后重复外部调用。先使用合成模型和本地只读工具，不追加外发真实资料；不把已有只读图恢复包装成任意Agent的可靠恢复。
