# P0-3：新聊天后台生命周期验收

2026-10-29。复用原SQLite队列/worker、SSE与同一A journal，交付异步聊天、取消、幂等与受控恢复。设计、配置、备份与回滚见[生命周期说明](../../docs/RESEARCH_PLAN_LIFECYCLE.md)。当前默认预览7896已启用，模型、联网和追踪关闭。

| 验收 | 结果与原始证据 |
|---|---|
| 最终完整离线CI | [ci_final.json](ci_final.json)：143模块1209项、462份Python语法检查，0跳过/联网尝试 |
| 新增17项开发测试 | worker/恢复/备份11项（本地保留：`test_research_plan_jobs.log`）、API4项（本地保留：`test_research_plan_job_api.log`）、真实聊天2项（本地保留：`test_research_plan_job_chat.log`）；包含忙碌时保留原任务焦点 |
| 四种真正SIGKILL | [recovery_final.json](recovery_final.json)：初次领取前后0→6次读取、首个结果保存后1→2、未知派发1→1并停止、完整报告保存后6→6；同任务ID和原预算，无已保存读取重放 |
| 真实浏览器 | [browser_final.json](browser_final.json)、[截图](browser_final.png)：关页后完成、同任务刷新、终态SSE重放、取消、网页恢复不重读、完整证据、其他会话404；未评中文字体布局 |
| 真实本地资料 | [real_data_smoke.json](real_data_smoke.json)：当归/川芎入队后完成两字段、172项共同UniProt及双方见证，13次内部读取，约406KB外置；刷新与同key复用，终态游标只收done |
| 结果与执行摘要 | [summary.json](summary.json)、[source_manifest.json](source_manifest.json)、[文档与语法检查](documentation_checks.json) |

本轮外部模型调用0。真实资料烟测只记录数量、状态、耗时和下载摘要，不保存药典正文；单次提交约0.343秒是本次观察，不是延迟基准或p95。其余测试使用临时合成目录。新聊天的PostgreSQL/多机、负载、机器断电、真实模型质量和独立业务集未验收。

## 原始失败与修复

failures/（本地保留：`failures/`）保留启动、语法与浏览器日志，[开发问题说明](development_findings.json)区分测试设置错误和产品修复：

- 手动启动离线worker时使用文件入口，未保留项目导入路径；改用正式CI提供的环境与模块入口。另一次直接worker没有模型占位配置，未把初始化失败冒充业务失败。
- 给读取API加队列回看时，文本替换错误影响了删除处理器缩进，出现SyntaxError；已局部修正并完整回归。
- 浏览器测试专用暂停接口放在研究路径之外，被现有访问规则拒绝。调整仅测试fixture路径，不放开正式应用访问规则。
- 第二浏览器context只访问首页，未初始化会话；测试改为先访问`/conversation`再验证其他会话404。[browser_v3.json](browser_v3.json)为随后通过的阶段结果。
- 首轮全仓[ci_initial.json](ci_initial.json)已通过；检查又发现忙碌时会清空原任务焦点，修复后增加原任务焦点断言，并在最终源码上重新运行全仓、四种强杀与浏览器，未覆盖首轮记录。

## 复现

```bash
.venv/bin/python -m scripts.run_offline_ci --out var/plan-lifecycle-ci
.venv/bin/python -m scripts.check_research_plan_job_recovery --out var/plan-lifecycle-recovery
.venv/bin/python -m uvicorn tests.research_plan_job_http_fixture:app --host 127.0.0.1 --port 7904
```

另用安装Playwright的Python运行：

```bash
/path/to/browser-python -m scripts.check_research_plan_job_browser \
  --url http://127.0.0.1:7904/ --browser /path/to/chromium \
  --out var/plan-lifecycle-browser.json
```

强杀脚本只杀自己创建的worker；等待其退出后用测试时钟推进租约，随后明确恢复。`recovery/`保留分场景验收、读取计数与日志；临时合成来源结束后被清除，历史路径本身不能直接续跑。浏览器fixture的暂停和中断接口只存在于测试模块；正式app不导入该模块。

没有新增依赖或来源库Schema迁移，未提交Git或构建/发布Docker。未知派发不会重试；已取消任务不可恢复；复合报告不恢复内部单个读取；journal保留原到期时间，重启不会重置预算。
