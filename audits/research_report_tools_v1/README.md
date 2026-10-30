# P0-2：既有报告作为A循环工具

2026-10-29。本地交付八工具统一入口，复用既有LangGraph报告，不增加框架或模型请求。设计、完成语义、预算及回滚见[实现说明](../../docs/RESEARCH_REPORT_TOOLS.md)。

| 验收 | 原始记录与结论 |
|---|---|
| 完整离线回归 | [ci_final.json](ci_final.json)：140模块1192项，455份Python语法检查，0跳过、0联网尝试；合成资料/模型替身 |
| 新增测试 | 工具14项（本地保留：`test_research_report_tools.log`）、聊天5项（本地保留：`test_research_report_chat.log`）：字段/来源/覆盖、预算、原生回读、取消、journal写入、来源选择与变化 |
| 浏览器 | [browser_v2.json](browser_v2.json)、[截图](browser_v2.png)：三行字段，出处折叠，23项完整遍历六页、下载、同run刷新和缺项；未评中文字体布局 |
| 真实SIGKILL | [recovery.json](recovery.json)：pending恢复停止、已保存报告不重读；各边界原始结果/日志在`recovery/`，不是模拟抛异常 |
| 实际本地资料 | [real_data_smoke.json](real_data_smoke.json)：当归/川芎两字段、172项共同标识，1外层动作/13内部读取，约406KB外置并下载；不保存真实药典正文 |
| 当前结果与执行源码 | [summary.json](summary.json)、[source_manifest.json](source_manifest.json) |

保留开发失败：[首轮选定回归摘要](targeted_initial.json)记录78项中的5失败/3错误，原因是新增测试误用底层默认8次预算，随后新测试显式采用16次业务预算；没有更改旧测试断言或默认8次限制。字段表丢失失败（本地保留：`chat_initial.log`）促成紧凑出处及5项靶点预览，避免16KB页面预算删除字段表。[浏览器首轮摘要](browser_initial.json)记录测试误假设每页一定20条，后按实际游标检查全部23条，未放大6KB预算。原始工具输出未完整落盘的失败明确只保留摘要，不冒充原始日志。后续成功见targeted_v2.log（本地保留：`targeted_v2.log`）与chat_v2.log（本地保留：`chat_v2.log`）；targeted_v2的13项早于最后新增的计量伪造测试，最终14项见完整CI日志。

## 复现

项目依赖环境中：

```bash
.venv/bin/python -m unittest tests.test_research_report_tools tests.test_research_report_chat
.venv/bin/python -m scripts.check_research_report_recovery --out var/report-recovery-check
.venv/bin/python -m scripts.run_offline_ci --out var/report-ci-check
```

另启仅合成资料的临时HTTP服务：

```bash
.venv/bin/python -m uvicorn tests.research_report_http_fixture:app --host 127.0.0.1 --port 7903
```

在安装Playwright的环境执行（替换本机解释器/Chromium路径，不自动下载浏览器）：

```bash
/path/to/browser-python -m scripts.check_research_report_browser \
  --url http://127.0.0.1:7903/ --browser /path/to/chromium \
  --out var/report-browser-check.json
```

SIGKILL脚本只终止自身worker，临时合成来源在结束后清除；保存的旧路径是执行证据，不能直接用历史临时目录恢复。真实资料烟测需准备README所列本地源库，不作为CI或公开数据分发。本次总外部模型调用0，未测模型泛化准确率、成本或多机运维；没有构建Docker、提交Git或上传远程。
