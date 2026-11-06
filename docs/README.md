# 文档导航

更新：2026-11-06。当前正式产品聚焦中草药资料问答；候选功能与历史实验分开记录。配置存在、单次走通、离线测试通过，均不自动等于完整产品验收。

## 当前使用与维护

| 需要了解 | 文档 |
|---|---|
| 当前产品与本地启动 | [项目README](../README.md) |
| 保留、隐藏、实验及待删除模块 | [功能边界与保留清单](FEATURE_STATUS.md) |
| 问答示例与结果说明 | [使用说明](NEW_USER_GUIDE.md) |
| 架构、运行开关和维护记录 | [统一大纲](TCM_PROJECT_ROADMAP.md) |
| 问句、工具与失败定位 | [诊断说明](REQUEST_DIAGNOSTICS.md) |
| 本次入口与介绍收敛 | [验证记录](../audits/qa_product_scope_v1/README.md) |

## 数据与检索

| 内容 | 文档 |
|---|---|
| 来源登记、索引、版本与回查 | [数据构建与发布](DATA_RELEASE_BENCHMARK.md) |
| 同版本精确/BM25/向量/RRF对照 | [检索实验与限制](RAG_RETRIEVAL_COMPARISON.md) |
| 药典正文与注释边界 | [数据修复记录](../audits/storage_boundary_v1/README.md) |

## 候选功能与历史实验

这些文档描述对应阶段的开发与验证，不扩大当前正式功能范围。

- 固定模板表格及工作区：[运行与边界](RESEARCH_SELECTION_AGENT.md)。表格是候选能力，当前首页不主动推荐。
- 引用RAG：[研究页说明](RAG_CITED_ANSWER.md)。不等于当前聊天已启用向量检索或生成。
- native Agent与策略对照：[工具反馈实验](../audits/requirements_feedback_v1/README.md)、[历史完成记录](../audits/requirements_completion_v1/README.md)。
- Langfuse：[接入说明](LANGFUSE_OBSERVABILITY.md)。当前是否启用以运行开关为准。
- 历史演示与面试材料：[演示](PROJECT_DEMO.md)、[证据](INTERVIEW_EVIDENCE.md)。其中表格和复杂研究故事只作为历史技术经历，不作为当前产品承诺。
- 原文档索引：[收敛前快照](history/DOCS_INDEX_BEFORE_QA_SCOPE_20261106.md)，保留其他设计文档和验收链接。
