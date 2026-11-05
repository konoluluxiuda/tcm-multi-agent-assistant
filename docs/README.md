# 文档导航

**最新本地交付（2026-11-06）：** 聊天请求恢复实现`2e9d50c`已本地提交并部署7934；114项针对性离线检查与7组合成浏览器检查通过，部署复验13条预设、4轮对话及3组真实页面恢复检查通过，模型关闭。未推送GitHub、无新远程CI。见[交付与回滚](../audits/chat_request_recovery_v1/release.md)、[资料整理业务流程](PRESET_QUESTION_SCOPE.md#业务背景与一次完整交付)。

**上一版交付闭环（2026-11-05）：** [提交、CI与部署记录](../audits/conversation_scope_v1/release_closure.md)。实现`954776b`已推送，GitHub全量1862项及镜像构建通过，当时部署至7934；该远程CI成绩不代替最新维护提交的验证。

**当前收尾范围（2026-11-04）：** 只完善单药字段、多药同字段、跨来源、分页关系查询和固定模板表格五类预设问题。暂停通用列顺序、更多理解策略及扩大模型评测。见[范围、13条走查与完成标准](PRESET_QUESTION_SCOPE.md)；下方历史功能描述不扩大本轮开发范围。

**当前入口（2026-11-04）：** [统一大纲与技术细节](TCM_PROJECT_ROADMAP.md)、[工具反馈理解与离线验收](../audits/requirements_feedback_v1/README.md)、[上一阶段真实结果](../audits/requirements_completion_v1/README.md)。此前[暂停交接](NEXT_SESSION_HANDOFF_20261103.md)作为历史背景。

执行交接：[工具反馈回归操作手册](REQUIREMENTS_FEEDBACK_EXECUTION_HANDOFF.md)（含零调用检查、授权后命令、停止条件和报告模板）。

**真实反馈验证（2026-11-05）：** [真实调用记录](../audits/conversation_scope_v1/live_feedback_result.json)15/16次，首测3/4，失败修复后单案通过；尚未证明优于本地规则，默认部署不变。

**连续对话增量（2026-11-05）：** [会话上下文与feedback衔接](../audits/conversation_scope_v1/README.md)补充未成功请求记忆、两药性味及代词追问；140项受影响测试通过。

**预设范围交付（2026-11-05）：** [预设问题走查](../audits/preset_questions_v1/README.md)13/13通过，123项针对性回归通过，模型调用0次；7934已更新并通过同一13项部署验收。固定范围交付已完成，不扩展Agent策略。

**最新入口（2026-11-01）：** 7921已接统一理解V2，普通问句先理解、参数与资料需求分别校验，最多2次请求，fixed执行；7920本地。离线与保存提案验证已完成，真实新策略收益待验收。见[实现与回滚](../audits/understanding_v2/README.md)、[使用说明](NEW_USER_GUIDE.md)及[统一大纲](TCM_PROJECT_ROADMAP.md)。以下保留各阶段历史口径。

更新：2026-10-31。当前主线为**版本化数据与benchmark → 同版本检索对照 → 带引用RAG → Agent任务整合与部署**。统一大纲管理R1–R4状态，新数据RAG方案定义契约，DataOps方案保留为工作区/交付子方案。R1/R2已交付（R2首测9/10保留），R3受限引用页和小样本真实调用已验收，整体模型收益仍未证；**R4工程/本地镜像已验收，真实A未通过；执行上下文V2已离线修复，有界Goal问句已接通，新8题理解模型6/8仍有失败；组合新两题G/A均完成交付，尚无多轮增益；演示材料已收敛，下一项本人排练与新用户验收**。普通聊天/DataOps保持原固定/合成模式，7912新引用页可由按钮发起一次生成。方剂研究等旧候选与早期排期不覆盖当前主线。

真实模型原始成绩保留：V4三题F/P各3/3、A为2/3；G V1十二题规则/模型各8/12，尚未稳定接入网页。G2字段语义与名称候选问题并入R4，不作为R1–R3前置任务。

**阅读顺序：** 开发先读统一大纲→数据/RAG/benchmark方案；涉及工作区/表格时再看DataOps子方案，选型追溯看架构决策。运行现有功能看使用说明和[DataOps演示](DATAOPS_DEMO.md)，新RAG运行与限制见[引用资料页说明](RAG_CITED_ANSWER.md)。

当前求职材料与一条命令排练已更新，见[三分钟演示](PROJECT_DEMO.md)、[简历/架构/追问](INTERVIEW_EVIDENCE.md)和[本地交付验收](../audits/recruitment_delivery_v2/README.md)。

## 当前入口

| 需要了解 | 文档 |
|---|---|
| 安装、能力和目录 | [项目README](../README.md) |
| 普通用户怎么问、怎么看结果 | [使用说明与示例](NEW_USER_GUIDE.md) |
| 当前进度与后续任务 | [统一大纲](TCM_PROJECT_ROADMAP.md) |
| 当前数据、RAG、benchmark与四阶段契约 | [数据/RAG/benchmark方案](DATA_RAG_BENCHMARK_PLAN.md) |
| 输入与执行组合、真实调用及策略取舍 | [组合验收](../audits/research_combined_v1/README.md)、[当前运行](RESEARCH_SELECTION_AGENT.md) |
| 问句Goal、两种核对语义与真实理解失败 | [使用说明](RESEARCH_SELECTION_AGENT.md)、[本轮验收](../audits/research_question_v1/README.md) |
| Harness V2执行层修复（离线） | [已完成与边界](../audits/research_harness_v2/README.md) |
| R4候选任务、真实负结果和本地容器 | [使用与边界](RESEARCH_SELECTION_AGENT.md)、[实际验收](../audits/data_rag_r4/README.md) |
| R3引用回答、真实调用与收益边界 | [使用与运行](RAG_CITED_ANSWER.md)、[实际验收](../audits/data_rag_r3/README.md) |
| R2同版本检索、新向量和真实成绩 | [运行与限制](RAG_RETRIEVAL_COMPARISON.md)、[实际验收](../audits/data_rag_r2/README.md) |
| R1发布、30题参考和数据回读 | [构建与维护](DATA_RELEASE_BENCHMARK.md)、[实际验收](../audits/data_rag_r1/README.md) |
| 贮藏注释边界、新索引与回滚 | [质量修复验收](../audits/storage_boundary_v1/README.md) |
| 本地Langfuse部署、采集与关闭 | [追踪说明](LANGFUSE_OBSERVABILITY.md) |
| V4原始模型结果与失败 | [有界复测与技术取舍](../audits/dataops_live_v4/README.md) |
| 单次模型目标理解、实测与边界 | [G对照结果](../audits/dataops_goal_v1/README.md)、[开发与复现](DATAOPS_GOAL_UNDERSTANDING.md) |
| V3原始失败与后续反馈改进 | [兼容对照与诊断](../audits/dataops_live_v3/README.md) |
| D1如何运行、下载和查看验收 | [D1交付说明](DATAOPS_D1_DELIVERY.md) |
| D2人工等待、动态执行及增量v2怎么用 | [D2交付与使用](DATAOPS_D2_DELIVERY.md) |
| 直接问句整理、增加字段与未识别要求 | [DataOps演示](DATAOPS_DEMO.md)、[问句入口验收](../audits/dataops_question_v1/README.md) |
| 聊天首页整理、下载与刷新续改 | [聊天接入验收](../audits/dataops_chat_v1/README.md) |
| D3题集、对照、规模与模型失败怎么看 | [D3评测](DATAOPS_D3_EVALUATION.md)、[DataOps演示](DATAOPS_DEMO.md) |
| 已有工作区、表格、持久化与增量契约 | [DataOps工作台子方案](DATAOPS_WORKBENCH_PLAN.md) |
| 为什么选择A、B/C为何保留 | [架构决策](ARCHITECTURE_DECISION_REVIEW.md) |
| A的五工具、依赖和批次实现记录 | [业务执行核心](CANDIDATE_A_BUSINESS_INTEGRATION.md) |
| 多步骤聊天接入记录及其真实边界 | [聊天接入](CANDIDATE_A_CHAT_INTEGRATION.md) |
| 本地演示与面试解释 | [演示指南](PROJECT_DEMO.md)、[面试证据](INTERVIEW_EVIDENCE.md) |
| 框架版本和恢复兼容性 | [LangGraph升级](LANGGRAPH_V1_UPGRADE.md) |
| 大结果如何保存、回读和备份 | [正式证据外置](RESEARCH_EVIDENCE_OFFLOAD.md) |
| 八个业务工具、跨来源对照与内部预算 | [报告工具统一](RESEARCH_REPORT_TOOLS.md) |
| 聊天关页、进度、取消和恢复 | [后台聊天生命周期](RESEARCH_PLAN_LIFECYCLE.md) |
| 旧业务闭环筛查与原始首轮失败 | [业务闭环核验](BUSINESS_CLOSURE_REVIEW.md) |
| 问句契约、路由修复及原题17/18开发回归 | [问句契约改进](QUESTION_CONTRACT_MILESTONE.md) |
| 本次删除了什么 | [仓库整理清单](PROJECT_CLEANUP_20261029.md) |

## 文档的职责与维护方式

| 类别 | 用途与更新规则 |
|---|---|
| 当前方案 | 统一大纲管理状态；数据/RAG方案定义新业务与benchmark契约，DataOps子方案管理工作区/交付；架构决策保留选型证据，不另设排期 |
| 当前使用说明 | 用户/演示/部署文档描述已经交付的功能，不能提前把新方案写成可运行入口 |
| 专题与阶段记录 | 数据、检索、记忆、恢复等机制与原始验收；数字只适用于当时，顶部提示后续去向 |
| 历史计划/候选 | 旧方剂研究、结构化分析和旧评测方案保留追溯；不再列为当前待办，不物理移动造成旧链接失效 |
| ADR/发布记录 | 保留决策与发布事实，不用旧镜像说明代替当前代码状态 |

前次144份文档的分类、变更前后摘要及核验结果见[文档清单](../audits/documentation_review_20261029/inventory.json)及[审阅结果](../audits/documentation_review_20261029/review.json)。清单是文档盘点，不是144份历史内容均经重新实验的声明。

D1已交付批量核对、持久工作区和表格下载，完整离线回归147模块1243项及后续范围修复16项检查通过，见[验收摘要](../audits/dataops_d1_v1/README.md)。旧作者8/18、17/18仅属历史开发成绩。D2已接通观察循环、持久人工等待和增量修订，见[D2验收](../audits/dataops_d2_v1/README.md)；D3本地30题与50条规模已验收；模型V2实际8次HTTP200，F/P各3/3、A为0/3；原始失败和诊断限制见[D3记录](DATAOPS_D3_EVALUATION.md)，动态A闭环和容器验收未完成。此前方案整理见[审查记录](../audits/dataops_plan_review_v1/review.json)。

## 数据与检索

- [数据发布使用](DATA_RELEASE_BENCHMARK.md)、[30题原始参考](../eval/rag_v1/README.md)、[数据/检索/RAG方案](DATA_RAG_BENCHMARK_PLAN.md)。R1已在独立目录重建8665片段词法索引；旧8654片段向量仍不可用于新索引；[R2](RAG_RETRIEVAL_COMPARISON.md)新建8665条向量/Qdrant代并完成检索对照，R3受限引用页已交付，R4限定候选任务已接入并验收本地容器；有界Goal入口已接通，开放语义稳定性与真实A收益仍待验证。
- [ETCM原始数据审计](ETCM2_DATA_AUDIT.md)、[字段映射](ETCM2_FIELD_MAPPING.md)、[来源关系与实体对应](ETCM_LOCALIZATION_AND_LINKS.md)。
- [MinerU导入](MINERU_RESULTS_IMPORT.md)、[药典构建](MINERU_RETRIEVAL_BASELINE.md)、[提取审核](MINERU_REVIEW.md)。
- [疾病来源目录](DISEASE_GENE_CATALOG.md)、[名称搜索](DISEASE_NAME_SEARCH.md)、[相似度与候选排序](DISEASE_CANDIDATE_ANALYSIS.md)。
- [Qdrant本地模式](QDRANT_LOCAL_MILESTONE.md)、[重排](LOCAL_RERANKER_MILESTONE.md)、[召回口径](SCOPED_RECALL_EVALUATION.md)。

原始PDF/JSON、OCR和派生数据库位于本地`data/`、`var/`或配置的资料根目录；这些不是可删除的调试文件，也不会随镜像自动获得。

## 运行、记忆与部署

- [研究Docker](RESEARCH_DOCKER.md)、[可选功能](OPTIONAL_FEATURES.md)、[资料访问范围](SOURCE_ACCESS.md)。
- [后台队列与SSE](BACKGROUND_RESEARCH_MILESTONE.md)、[PostgreSQL恢复](POSTGRES_CHECKPOINT_MILESTONE.md)、[两药恢复](PAIR_CHECKPOINT_MILESTONE.md)。
- [工具循环journal](RESEARCH_LOOP_DURABLE_MILESTONE.md)、[合成循环生命周期](RESEARCH_LOOP_LIFECYCLE_MILESTONE.md)、[备份恢复](RUNTIME_BACKUP_MILESTONE.md)。
- [会话记忆](PERSISTENT_MEMORY.md)、[项目记忆](PROJECT_MEMORY_MILESTONE.md)、[语义候选](PROJECT_SEMANTIC_MEMORY_MILESTONE.md)、[策略边界](PROJECT_MEMORY_POLICY_MILESTONE.md)。
- [MCP工具与客户端](MCP_READONLY_MILESTONE.md)。

## 实验与历史证据

- [离线CI规则](OFFLINE_CI.md)、[新R阶段分层评测](DATA_RAG_BENCHMARK_PLAN.md#6-比较方法与验收口径)、[DataOps任务契约评测](DATAOPS_WORKBENCH_PLAN.md#8-评测协议小规模但能解释结果)、[历史业务评测协议](APPLICATION_EVALUATION_PROTOCOL.md)。
- [最新A/B/C实验设置](ARCHITECTURE_ABC_V1_REVIEW.md)、[首轮失败](ARCHITECTURE_ABC_LIVE_V1_RESULT.md)、[V2结果](ARCHITECTURE_ABC_LIVE_V2_RESULT.md)。
- [历史证据外置原型](EVIDENCE_OFFLOAD_CANDIDATE.md)、[发布恢复](OFFLOAD_JOURNAL_RECOVERY.md)、[外置循环](OFFLOAD_LOOP_CANDIDATE.md)。
- `audits/`保留原始成功、失败、usage和历史源码哈希。已清理脚本的旧路径可能作为历史记录出现，不等于当前可执行命令；删除映射见清理清单。
- `releases/`和[变更记录](CHANGELOG.md)保留历史交付信息，不能用旧镜像说明证明最新本地代码已经发布。
- 较早的[证据建设计划](PROJECT_EVIDENCE_PLAN.md)、[业务完整度快照](BUSINESS_COMPLETENESS_REVIEW_20261020.md)、[结构化分析计划](RESEARCH_ANALYTICS_PLAN.md)和[模型实验日志](LLM_APPLICATION_EXPERIMENTS.md)供追溯使用，不另立当前排期。已有内容不重复迁移成长篇新文档，也不删除关键失败证据。
- [方剂研究候选与数据审计](OBSERVATION_DRIVEN_RESEARCH_REQUIREMENTS.md)已退出主线；其中原始数据统计继续有效，不等于方剂研究功能已实现。统一大纲仍是唯一排期，新数据RAG方案和工作区子方案各管对应契约，不维护并行任务清单。
