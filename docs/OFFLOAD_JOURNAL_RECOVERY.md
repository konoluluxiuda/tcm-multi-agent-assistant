# 外置证据与执行journal：发布边界验证

> 后续进度（2026-10-29）：发布边界已在[正式A循环](RESEARCH_EVIDENCE_OFFLOAD.md)另行验证，并接入配套备份与容量限制。本文的固定原型、19项测试及两边界结果保留原阶段范围。

2026-10-28。隔离实验完成；未改聊天入口、大纲、业务数据库或正式模型循环。

## 本轮实现

[offload_journal.py](../experiments/architecture_candidates/offload_journal.py) 复用RunJournal，执行固定的fetch→保存完整证据→发布引用→受限回读流程。它没有调用LLM，尚非原模型循环的大结果接入。

先将运行状态标记pending并累计一次底层调用，再查询和保存证据；证据提交成功后才发布引用。恢复时校验引用的owner/run、来源版本、证据ID与内容哈希。证据库路径、读取范围及实验代码哈希纳入运行契约。

已发布引用缺失或正文损坏时返回partial并清除结果，不自动重新查询，不把缓存终态当作仍可用证据。未知调用结果沿用原journal的停止策略。

## 实际结果

19项相关测试通过（本轮6项发布恢复＋先前13项证据存储），不是19个真实用户业务问题。另完成两个真正SIGKILL边界，由独立新进程恢复：

| 强杀位置 | 恢复结果 | 底层查询累计 |
| --- | --- | --- |
| 证据已提交，引用尚未写入journal | partial / uncertain_tool_dispatch，无发布引用 | 1次 |
| 引用已写入journal，尚未回读 | complete，准确恢复合成目标行 | 1次 |

第一种场景保留1条未引用记录，不自动清理，也不在恢复时推断并发布它。第二种场景没有悬空引用、没有重复查询。两种场景不能证明所有故障下都不产生未引用记录。

[结果摘要](../audits/offload_journal_recovery_v1/summary.json)，外部模型调用0次，约400KB资料全部合成。

## 运行

```bash
/tmp/tcm-ci-mail-env/bin/python -m unittest \
  tests.test_offload_journal_candidate tests.test_evidence_offload_candidate -q
/tmp/tcm-ci-mail-env/bin/python -m scripts.check_offload_journal_recovery \
  --out var/offload_journal_new
```

out须不存在。脚本只终止其自行创建的合成worker。测试覆盖发布前/后中断、已发布记录丢失、内容损坏、owner/版本不匹配、终态重复读取不查询。强杀验证为进程崩溃，不是整机断电。

## 边界与下一阶段

两个SQLite文件之间没有分布式事务。本原型以“先存证据、后发布引用、未知则停止”保证保守恢复，代价是可能遗留未引用证据。尚需引用关系盘点、总容量限制和可审阅清理；不应在活跃任务未锁定时按时间直接删除。

固定流程还没有按阈值路由，也没有让现有research_run接受外置结果Schema。下一项应在实验模型循环适配器中验证：小结果内联、大结果生成受限索引、模型按句柄读取证据、结束时核验出处覆盖，以及沿用本轮发布协议恢复。此后才有依据考虑正式接入，不因存储测试通过提前修改大纲。
