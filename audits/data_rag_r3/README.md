# R3 实际验收（2026-10-31）

受限摘录RAG及网页/API已交付：有界证据→可选一次真实生成→引用/逐字摘录校验→原文回退。当前7912允许用户点击整理，7896只查原文；原有聊天/DataOps模式保持原配置。R2字段别名状态已修复，旧保留首测9/10及参考未改写。

- [原始冻结协议](protocol.json)：4次上限、每题一次、原始代码/正文hash、资料与预算；执行源码快照在私有`live/frozen_source/`。
- [原始逐题指标](live_metrics.json)：4次HTTP200、4/4受限生成契约、4/4零调用控制；已暴露开发题，不是独立业务准确率。
- [模型价值复核](model_value_review.json)：原文已有全部要求字面信息；生成更长，G01词串不自然，两题重复摘录，未证明整体收益。
- [确定性展示回放](presentation_replay.json)：只去重和调整共有片段标签，0新增请求，原模型回答保留。
- [99项离线检查](offline_checks.json)：12模块，0跳过、0网络尝试。
- [Langfuse回读](langfuse_readback.json)：8条轨迹、4个generation及父子关系与原记录对应。
- [资产和最终代码回执](artifact_receipt.json)：区分原始冻结版本和运行后展示/存储处理改动；私有原始来源与响应不随此目录公开。

两端口运行/浏览器记录见本目录`service_acceptance.json`与`browser_checks.json`。原始/最终展示字数及失败评价均保留；“引用存在/原文包含”不冒充语义推理验证，自由医学解释和自主循环仍未验收。使用、边界、真实负结果与下一阶段见[R3说明](../../docs/RAG_CITED_ANSWER.md)。
