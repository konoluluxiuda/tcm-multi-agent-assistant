# J04准备：本地运行审计日志

2026-10-24。解决独立进程结束后运行结果丢失的问题，先定义状态写入/冲突/中断语义，沿用项目现有SQLite路线；不将其包装成已接PostgreSQL或完整checkpoint恢复。

新增 `tcm/research_run_store.py`、`tcm/research_run_durable.py`；执行器增加可选 `on_state` 回调，本地适配器透传。未传回调时保持原运行方式。`scripts/run_local_research.py` 增加持久化、历史和中断标记命令。

## 使用

```bash
/tmp/tcm-ci-mail-env/bin/python -m scripts.run_local_research \
  --diseases var/disease_catalog_v1/build/diseases.sqlite3 \
  --disease 'Dermatosis Papulosa Nigra' \
  --state-db var/research_journal_v1/runs.sqlite3

# 可在关闭前一个进程后运行
/tmp/tcm-ci-mail-env/bin/python -m scripts.run_local_research \
  --state-db var/research_journal_v1/runs.sqlite3 --history

# 仅处理超过180秒未更新的running记录，不重跑
/tmp/tcm-ci-mail-env/bin/python -m scripts.run_local_research \
  --state-db var/research_journal_v1/runs.sqlite3 --recover RUN_ID
```

记录包括run ID、版本号、问题、状态、更新时间、节点快照、工具观察、最终结果和引用ID。执行器快照排除messages/calls/seen，不保存模型自由文本或密钥；问题和本地工具资料本身仍是落盘内容。文件权限0600，WAL SQLite数据库，拒绝使用其他业务库作为日志。CLI使用local-cli单一所有者，不是Web身份认证。

每次节点完成，事务提交最新状态与累积工具事件；执行失败保留此前观察，取消记录cancelled。版本号CAS与running状态条件防止陈旧写入覆盖。身份键参与查询，未匹配记录不可见。仅返回最近20条历史，尚未添加清理/删除政策，磁盘总量未设限。

崩溃后记录可能仍为running。显式恢复仅在超过180秒无更新时标记interrupted，CAS保证与后续写入冲突时拒绝覆盖；不在启动时把其他worker的任务全部判死。当前步骤最长30秒、运行最长120秒。由于没有保存模型消息和待执行调用，不能从中间节点续跑，也不承诺exactly-once；被杀死时最后一步是否完成可能未知，需要新建运行重新执行。

工具/模型执行后与日志落盘之间仍有崩溃窗口。写入失败会阻止继续执行，而不是悄悄以无日志方式继续。节点快照不是独立追加事件表，也不是LangGraph checkpointer。下一阶段再设计PostgreSQL事务、运行租约、幂等键及可恢复事件序号。

## 验证与范围

31项定向测试通过（存储5、执行器12、范围9、API5），无跳过或网络尝试。覆盖重开、所有者隔离、版本冲突、过期恢复、终态禁止覆盖、磁盘错误中止、取消/失败和无关数据库拒绝。

独立CLI进程实际查询本地疾病，第二个进程读回complete、283个靶点、最终引用obs-1。报告 `audits/research_journal_v1/summary.json`。未向外部模型发送数据。

聊天面板尚未接此日志，刷新丢失提示仍准确；未更新Docker、未修改默认部署。回滚可停止传入 `--state-db` 并停用回调，已有业务数据库没有迁移。下一项将日志接入有所有者校验的运行查询API和页面历史，再推进后台任务/SSE；SSE断线语义不能直接沿用当前请求取消即停的接口。

## 聊天历史接入（后续增量）

聊天页本地研究面板现接入同一日志机制。默认日志位于会话库同目录的 `research_runs.sqlite3`，可用服务端 `RESEARCH_RUN_DB` 指定专用库；客户端不能指定路径。POST研究运行开始前创建记录，成功/待补充/失败/取消均落盘；记录不能保存时不启动查询。

新增只读 `GET /api/research/runs`（最近20条摘要）、`GET /api/research/runs/{id}` 和同源 `DELETE /api/research/runs/{id}`。身份来自已校验的会话Cookie，仅保存其SHA256作为所有者键，不把Cookie正文存入日志。GET响应no-store；跨所有者返回404。运行中的记录禁止删除，取消后可删。历史是当时快照，回看不自动重新查库；旧资料是否已变化不影响读取旧结果，页面明确标注历史。

使用：打开 http://127.0.0.1:7879/ → 本地研究 · 疾病靶点 → 查询 → 刷新页面 → 研究历史 → 回看/删除。清除会话或Cookie会更换身份，旧记录不会转移给新会话；这不等于从磁盘删除旧记录。当前无账号级跨设备恢复、自动清理、历史超过20条分页或SSE。崩溃后running记录仍需维护者按过期语义处理，浏览器没有自动重跑入口。

24项定向回归通过，新增验收覆盖所有者隔离、删除权限、运行中删除冲突、取消落盘和no-store。浏览器实测真实本地查询后刷新、恢复20条原始行、另一会话404和删除，无页面错误。报告 `audits/research_history_v1/summary.json`。本轮未发送外部模型请求，Docker未更新。

下一项是后台运行生命周期与SSE：先明确浏览器断线不取消后台任务、显式取消API、事件序号和重连回放，再选择PostgreSQL/checkpointer接入方式。当前仍是请求绑定执行，不能称后台队列或断点续跑已实现。

后续完整里程碑已交付：[后台研究、持久队列与SSE](BACKGROUND_RESEARCH_MILESTONE.md)。新页面提交改用独立jobs API，旧同步runs API继续兼容；默认页面断连不再取消任务。
