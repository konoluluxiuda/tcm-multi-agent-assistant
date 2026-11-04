# 统一查询交付、恢复与页面验收

2026-11-03。本轮关闭[第二轮查询交付审查](../requirements_v3_answer_review_v2/README.md)中的实现缺口。验收针对当前工作区、合成数据库与模拟模型响应；不代表真实模型泛化能力或运行中实例已更新。

## 实际完成

| 问题 | 修复及落点 |
| --- | --- |
| 聊天与工作台执行行为不同、缺少稳定会话与问题 | `tcm/dataops/answer_delivery.py` 的 `deliver_saved_query` 统一协调，传递原问题、会话及逐步请求键；`app.py` 与 `/api/dataops/answer` 共用 |
| 多路只展示首路、刷新丢结果 | `assets/web/answer_delivery.js` 逐路展示；聊天保留原有工具卡片和证据按钮；工作台和聊天均可恢复完整交付 |
| 部分证据不足被判成功、异常丢掉已成功结果 | 各步保留状态与文本，缺失或异常生成 `unfinished`；部分成功为 `partial`，全部不可用为 HTTP 503；没有输出不能算成功 |
| 澄清后按钮锁住、旧工作区残留 | 工作台明确区分查询、工作区和澄清，清理旧显示，恢复提交按钮 |
| 重复执行工具、重复或只存首路对话 | 结果先写 `answer_deliveries.sqlite3`；工具阶段不写对话，最终提交一次简要对话及交付引用；同 key 重放不重执行 |
| 结果已完成但对话提交失败 | 回读交付后补提交；SQLite `completed_deliveries` 标记与对话写入同事务，历史截断后仍可去重 |
| 权限、来源与诊断不完整 | 从 owner 绑定的提案取路由，禁止客户端注入路由；执行前核对来源版本，逐步记录诊断；未知执行保持冲突，不自动重试 |
| 旧调用方与备份兼容 | 单路保留旧结果字段，多路以 `answer_steps` 为准；备份校验兼容旧库及新增去重表 |

共享协调器覆盖查询提案与模型 query handoff；既有本地快捷查询路径保持原逻辑。它不是新增多轮自主规划能力。

## 验证与证据

- 全仓离线 CI：**203 个模块、1752 项通过，0 跳过、0 联网尝试**。见 [原始汇总](offline_ci_summary.json)。
- 本轮查询交付测试 17 项；备份测试共 15 项（新增 2 项）。包含跨来源与同类多路、部分失败、单路兼容、重放、所有权、未知执行、对话提交中断恢复和逐步诊断。
- Chromium 浏览器 **9 项通过**，无 JavaScript 错误、无外部浏览器请求。见 [浏览器记录](browser_result.json) 与 [复现脚本](browser_check.py)。验证实际页面文本、原始 PDF 页图、刷新恢复、澄清后重提、重放及部分失败提示。
- 浏览器请求由 Playwright 转入真实 ASGI TestClient；使用合成 ETCM、有效合成 PDF 与对应索引，模型 SDK 返回固定提案。没有调用真实模型、没有重启现有服务；不是独立部署验收。
- [源码摘要与验证口径](verification.json) 固化本轮相关文件 SHA256；[前版验收](prior_acceptance.json) 保留不覆盖，最新汇总在 [requirements_v3/acceptance.json](../requirements_v3/acceptance.json)。

最终 CI 命令：

```bash
.venv/bin/python -m scripts.run_offline_ci --out var/answer_delivery_v2/full_ci_final
```

浏览器脚本当前使用本机 Chromium 路径，复用前需提供 Playwright 与调整 `executable_path`。本次 Playwright 安装在 `/tmp/tcm-answer-browser-tools`，未改项目依赖；通过离线 CI 的 `test_environment()` 设置隔离环境运行。截图留在 `var/answer_delivery_v2/browser/`，未将字体或浏览器二进制加入仓库。

## 失败过程保留

初次定向检查发现诊断函数参数 `status` 冲突，已改用 `tool_status/result_status`。首轮全仓 CI 又发现备份严格表清单拒绝新增去重表，以及旧单路调用方依赖 `assistant` 字段；分别补兼容与回归后，最终全仓通过。首轮失败日志仍在 `var/answer_delivery_v2/full_ci/`，最终结果另存 `full_ci_final/`，没有用重跑覆盖首轮。

浏览器初次用 HTTP 测试地址，页面依赖的 `crypto.randomUUID` 不可用；测试传输改为 HTTPS 测试地址后通过。这是测试环境修正，不是生产功能修复。上述过程不能作为真实模型成功率统计。

## 使用、恢复与回滚边界

1. 新查询提案必须保存原问题和来源版本；旧的不完整提案返回 409，需重新提交新请求。
2. POST 执行会核对当前来源；GET 历史交付只读已保存快照，不重新执行，也不把历史内容冒充当前来源。
3. 备份应同时保存会话库和完整 `DATAOPS_ROOT`，仅备份会话库不能恢复外置完整答案。新增表采用增量创建；回滚应用前保留数据库副本，旧版本备份工具可能不认识新表。
4. 幂等与恢复按单服务进程及 SQLite 事务验证，不宣称分布式任务 exactly-once。执行结果未知时返回冲突，不自动重发工具请求。
5. 多对象结果不默认为最后一个对象；后续追问应明确药名，避免错误继承范围。
6. 默认理解策略、既有端口实例与 Docker 镜像未变。本轮未提交、推送 Git，也未调真实模型。

## 剩余任务

- 按[下一阶段评测准备](../../docs/UNDERSTANDING_EVALUATION_NEXT.md)收集未参与调优的问句，冻结参考答案、数据、代码与评分器，对照本地规则、一次模型兜底及 requirements_v3。
- 本地语义依据优化根据新失败证据决定；全量分页 `completeness=all` 仍显式不支持。
- 真实模型验证后决定默认策略，再做干净环境启动、查询、交付、恢复与镜像验收。

因此可以确认统一查询交付的本地工程验收完成，不能据此宣布整个项目或新策略真实效果已验收。
