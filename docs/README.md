# 文档导航

更新：2026-10-30。当前主线收敛为**DataOps跨源资料核对与持续交付**。统一大纲管理任务状态，DataOps方案管理需求/技术/评测；D1固定交付与D2持久观察循环/人工等待/增量v2已做本地验收；真实模型V2仅三道合成开发题：F/P各3/3、A为0/3；网页明确标注合成替身。方剂研究、旧结构化分析及早期收题方案均作为历史参考，不能覆盖当前排期。

**阅读顺序：** 开发先读统一大纲→DataOps方案→架构决策；运行现有功能看使用说明。旧演示指南保留历史业务；新工作台使用[DataOps演示](DATAOPS_DEMO.md)。无需顺序阅读全部里程碑。

## 当前入口

| 需要了解 | 文档 |
|---|---|
| 安装、能力和目录 | [项目README](../README.md) |
| 普通用户怎么问、怎么看结果 | [使用说明与示例](NEW_USER_GUIDE.md) |
| 当前进度与后续任务 | [统一大纲](TCM_PROJECT_ROADMAP.md) |
| D1如何运行、下载和查看验收 | [D1交付说明](DATAOPS_D1_DELIVERY.md) |
| D2人工等待、动态执行及增量v2怎么用 | [D2交付与使用](DATAOPS_D2_DELIVERY.md) |
| 直接问句整理、增加字段与未识别要求 | [DataOps演示](DATAOPS_DEMO.md)、[问句入口验收](../audits/dataops_question_v1/README.md) |
| 聊天首页整理、下载与刷新续改 | [聊天接入验收](../audits/dataops_chat_v1/README.md) |
| D3题集、对照、规模与模型失败怎么看 | [D3评测](DATAOPS_D3_EVALUATION.md)、[DataOps演示](DATAOPS_DEMO.md) |
| 现行需求、参考源码、技术、模块与评测 | [DataOps工作台实施方案](DATAOPS_WORKBENCH_PLAN.md) |
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
| 当前方案 | 统一大纲管理状态；DataOps方案定义需求与验收；架构决策记录取舍，不重复维护三份排期 |
| 当前使用说明 | 用户/演示/部署文档描述已经交付的功能，不能提前把新方案写成可运行入口 |
| 专题与阶段记录 | 数据、检索、记忆、恢复等机制与原始验收；数字只适用于当时，顶部提示后续去向 |
| 历史计划/候选 | 旧方剂研究、结构化分析和旧评测方案保留追溯；不再列为当前待办，不物理移动造成旧链接失效 |
| ADR/发布记录 | 保留决策与发布事实，不用旧镜像说明代替当前代码状态 |

前次144份文档的分类、变更前后摘要及核验结果见[文档清单](../audits/documentation_review_20261029/inventory.json)及[审阅结果](../audits/documentation_review_20261029/review.json)。清单是文档盘点，不是144份历史内容均经重新实验的声明。

D1已交付批量核对、持久工作区和表格下载，完整离线回归147模块1243项及后续范围修复16项检查通过，见[验收摘要](../audits/dataops_d1_v1/README.md)。旧作者8/18、17/18仅属历史开发成绩。D2已接通观察循环、持久人工等待和增量修订，见[D2验收](../audits/dataops_d2_v1/README.md)；D3本地30题与50条规模已验收；模型V2实际8次HTTP200，F/P各3/3、A为0/3；原始失败和诊断限制见[D3记录](DATAOPS_D3_EVALUATION.md)，动态A闭环和容器验收未完成。此前方案整理见[审查记录](../audits/dataops_plan_review_v1/review.json)。

## 数据与检索

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

- [离线CI规则](OFFLINE_CI.md)、[现行DataOps评测设计](DATAOPS_WORKBENCH_PLAN.md#8-评测协议小规模但能解释结果)、[历史业务评测协议](APPLICATION_EVALUATION_PROTOCOL.md)。
- [最新A/B/C实验设置](ARCHITECTURE_ABC_V1_REVIEW.md)、[首轮失败](ARCHITECTURE_ABC_LIVE_V1_RESULT.md)、[V2结果](ARCHITECTURE_ABC_LIVE_V2_RESULT.md)。
- [历史证据外置原型](EVIDENCE_OFFLOAD_CANDIDATE.md)、[发布恢复](OFFLOAD_JOURNAL_RECOVERY.md)、[外置循环](OFFLOAD_LOOP_CANDIDATE.md)。
- `audits/`保留原始成功、失败、usage和历史源码哈希。已清理脚本的旧路径可能作为历史记录出现，不等于当前可执行命令；删除映射见清理清单。
- `releases/`和[变更记录](CHANGELOG.md)保留历史交付信息，不能用旧镜像说明证明最新本地代码已经发布。
- 较早的[证据建设计划](PROJECT_EVIDENCE_PLAN.md)、[业务完整度快照](BUSINESS_COMPLETENESS_REVIEW_20261020.md)、[结构化分析计划](RESEARCH_ANALYTICS_PLAN.md)和[模型实验日志](LLM_APPLICATION_EXPERIMENTS.md)供追溯使用，不另立当前排期。已有内容不重复迁移成长篇新文档，也不删除关键失败证据。
- [方剂研究候选与数据审计](OBSERVATION_DRIVEN_RESEARCH_REQUIREMENTS.md)已退出主线；其中原始数据统计继续有效，不等于方剂研究功能已实现。未创建第二份DataOps大纲，需求细节集中维护在现行方案。
