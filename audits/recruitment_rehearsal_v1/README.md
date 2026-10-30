# 求职演示排练记录（2026-10-27）

本轮由 `scripts/rehearse_recruitment_demo.py` 串行运行，结果见 [summary.json](summary.json)。

| 场景 | 结果 | 证据 |
| --- | --- | --- |
| 循环 API、两药 checkpoint、运行备份代表性回归 | 18 项通过 | 日志（本地保留：`contracts.log`） |
| 合成循环真实进程 SIGKILL | 3 个边界通过 | [记录](loop_crashes/summary.json) |
| 本地当归/川芎报告 | 172 个共同来源标识、下载及历史刷新通过 | [记录](real_pair/browser.json) |
| 合成循环网页 | 暂停、刷新恢复、会话隔离、删除及会话轮换通过 | [记录](loop_browser.json) |

外部模型调用 0 次；合成循环中的 model_calls 是本地替身调用计数。真实资料报告是确定性工具流程。未测业务准确率、生产吞吐、真实模型费用或延迟；未重新执行 PostgreSQL 强杀和全库恢复。代表性测试不能代替全仓回归，也不能与历史测试数相加。

[source_manifest.json](source_manifest.json) 记录排练所用核心文件哈希，运行前后未变；它不能证明既有预览进程加载了这些文件。仅归档摘要和日志，不归档浏览器截图、下载的原始资料或运行数据库。

操作步骤见 [演示指南](../../docs/PROJECT_DEMO.md)，技术讲解见 [面试证据](../../docs/INTERVIEW_EVIDENCE.md)。
