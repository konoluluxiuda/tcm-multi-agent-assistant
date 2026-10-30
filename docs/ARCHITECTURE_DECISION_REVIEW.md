# 架构选择：A为生产主线，B/C保留隔离对照

更新：2026-10-29。此文合并早期候选审查、动态依赖原型、初版B恢复对照和上下文实验结论。过时原型已清理，原始结果未改写；代码删除范围及本地回滚包见[清理记录](PROJECT_CLEANUP_20261029.md)。

> 当前业务已调整为DataOps资料核对与持续交付，见[实施方案](DATAOPS_WORKBENCH_PLAN.md)。下文已实现范围仍是旧研究查询链路；D1固定流程与D2持久图/增量修订已接入，见[D2记录](DATAOPS_D2_DELIVERY.md)。D2网页仅使用合成模型替身，真实模型效果未验收。

## DataOps执行决策（D2工程链路已验收）

保持A的单Agent＋受控工具路线，新建业务StateGraph承载目标、动态计划、观察、验证和人工消歧；复用现有工具/检索、证据外置、队列、SSE和归属校验。A是架构路线，不要求永久保留旧图的所有代码。新模式的完成条件按交付目标核验，不能继续仅以精确调用清单是否执行为准。

新DataOps图采用原生持久Checkpointer，首版单机SQLite；旧A RunJournal只管理旧运行，旧报告PostgreSQL checkpoint不迁移。队列控制租约/取消、Checkpointer控制新图恢复、工作区保存版本化结果与产物；同一新运行不由两套机制推进。等待用户需要新的非终态及受版本约束的resume。原生checkpoint不保证外部调用恰好一次，保留未知派发停止、写产物幂等和来源版本检查。SQLite适配3.1.2已安装并锁定，与checkpoint 4.3.1兼容；原生Tool Calling适配器通过离线契约测试，HTTP尚未开放真实资料模型外发。

未直接换用`create_agent`或Deep Agents SDK：它们可提供基础循环/中间件，但本项目已有来源准入、预算和恢复语义，直接替换会扩大迁移面。首版不需要通用文件系统、沙箱或委派。后续出现明确缺口再评估，不把当前选择写成SDK质量排名。

本轮核验了`langchain-ai/docs@c08c01a65acbf06aab96efc02ec954aab78791e9`的从零组装教程，以及`langchain-ai/lca-deepagents@c39d80a2c81bfc4879cbbd3a552c9b37b4f81937`课程。逐文件路径、SHA与取舍见[固定源码清单](../audits/dataops_plan_review_v1/upstream_sources.json)和[方案第2节](DATAOPS_WORKBENCH_PLAN.md#2-两个参考项目的核验与取舍)。课程的MemorySaver、代码执行eval和自由SQL例子不能直接当持久恢复、业务评测和受限查询实现。

## 已作出的选择

使用现有LangGraph 1.x＋单Agent受控工具循环，复用已有程序子图。已接入八个业务工具（原五分析＋药典、属性、两药报告）、多步骤聊天、来源依赖、受控批次与完成校验；调用链和边界见[A执行核心](CANDIDATE_A_BUSINESS_INTEGRATION.md)、[聊天接入](CANDIDATE_A_CHAT_INTEGRATION.md)及[报告工具统一](RESEARCH_REPORT_TOOLS.md)。新聊天已复用原队列/SSE/取消和同一A journal，支持单机受控恢复，见[生命周期](RESEARCH_PLAN_LIFECYCLE.md)。

A是实施选择，不是已证明质量全面领先。当前聊天使用问句意图提案＋本地声明式驱动，尚未启用真实工具观察外发的自主循环。B Deep Agents、C主管留在`experiments/`，不参与默认聊天路径。

## 选型证据与限制

| 证据 | 观察与决定 |
|---|---|
| [同环境A/B/C准备](ARCHITECTURE_ABC_V1_REVIEW.md) | 保留最新协议适配器、可选依赖锁和显式测试；不同Pydantic/Tenacity环境差异单列，不混为生产基线 |
| [首轮真实模型结果](ARCHITECTURE_ABC_LIVE_V1_RESULT.md) | A完成，B/C被单动作协议拦截；实际9请求，不是9道问题 |
| [V2真实结果](ARCHITECTURE_ABC_LIVE_V2_RESULT.md) | A仍收到合法批次而被拦截，B/C完成；实际19请求。之后只做本地批次修复，没有V3真实结果 |
| [早期A/C对照](PAIR_LIVE_SYNTHETIC_RESULT.md) | 原始18请求及usage保留；环境旧，不能直接与新B比较成本 |
| [动态依赖原型](../audits/architecture_dependency_v1/summary.json)、[恢复原型](../audits/architecture_dependency_recovery_v1/summary.json) | 机制已迁入正式scope/journal。早期独立门禁与其测试已退役；生产隔离、越权、版本、批次恢复测试继续保留 |
| [初版B恢复](../audits/deepagents_candidate_b_v1/summary.json) | 原生pending工具可重放，需要应用层适配才能满足本项目未知派发停止策略。不是通用可靠性排名；旧脚本已退役，记录保留 |

不根据单题选赢家，不把替身步骤当真实模型调用，不把低token的未执行任务当成本优势。已执行失败、usage和执行源码快照保留在`audits/`，不为清理而重写历史哈希。

## 上下文与外置资料

[原上下文对照](../audits/context_candidates_v1/README.md)使用100/2000/14000项合成资料，最大约406KB。它验证了外置与回读机制，未验证语义选择或计费优势；初版B状态文件也不等同独立持久磁盘。

本轮复用[证据外置存储](EVIDENCE_OFFLOAD_CANDIDATE.md)、[发布与恢复](OFFLOAD_JOURNAL_RECOVERY.md)、[外置循环](OFFLOAD_LOOP_CANDIDATE.md)。这些实验及其共享大观察fixture保留；机制已接入[正式A循环](RESEARCH_EVIDENCE_OFFLOAD.md)，超过16KB的已验证返回保存完整证据（每条最多4MB），不另维护第二套生产循环。

## 仍保留的设计约束

- 计划描述对象、要求、依赖和结束条件；模型不能猜来源ID或生成SQL。
- 普通只读步骤直接执行，必要歧义才澄清。新业务按事前目标契约区分任务完整与数据完整；正确列出缺失可完成“审查任务”，但不能完成“全部字段必须补齐”。旧流程状态评分保留。
- 版本、owner/run、预算及内部子调用要统一计量；未知派发不自动重发。
- 固定集合运算交给工具；仅下一步依赖观察时使用循环，不要求输出隐藏思维链。
- 16题架构开发集仍为not_run，不借其他回归结果将其标成已通过，也不再作为新业务前置。DataOps执行策略F/P/A按[统一大纲](TCM_PROJECT_ROADMAP.md)推进，与旧框架A/B/C实验分开。

## 参考项目保留为机制来源

LCC用于理解循环与工作流，DeerFlow/Open Deep Research用于任务委派与上下文隔离，Deep Agents用于中间件和结果外置，Onyx/RAGFlow用于检索范围与资料组织，Letta用于记忆生命周期。它们不是必须同时安装的依赖。

前次审查记录的源码入口如下，未重新联网核验或冻结提交，不能当作当前版本保证：

- `learn-claude-code/s16_workflow_runtime/`
- `deer-flow/backend/packages/harness/deerflow/agents/lead_agent/agent.py`
- `deer-flow/backend/packages/harness/deerflow/agents/middlewares/tool_declarations.py`
- `deepagents/libs/deepagents/deepagents/graph.py`
- `open_deep_research/src/open_deep_research/deep_researcher.py`
- `onyx/backend/onyx/context/search/pipeline.py`

是否引入组件以真实业务缺口、同条件验证和维护成本决定，不能因面试术语或流行度定死技术。
