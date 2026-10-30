# Deep Agents候选B合成对照

原候选B专题文档已退役，当前取舍见[架构决策](../../docs/ARCHITECTURE_DECISION_REVIEW.md)，后续同环境对照见[A/B/C审查](../../docs/ARCHITECTURE_ABC_V1_REVIEW.md)。本目录保留下述历史合成结果。

summary.json为最终同步checkpoint、禁止worker网络的6场景观察，observation_checks_passed=true，但application_recovery_policy_met=false。初始异步、扩展异步和同步中间结果分别保留，不合并成通过率。calls.jsonl是实际底层派发收据；重建状态的内存回放不产生底层派发。

所有模型均为固定替身，外部模型调用0次。未评测真实问法、医学事实、成本或答案质量。未复制SQLite状态文件。
