# D2本地工程验收

日期：2026-10-29。使用方式、实际调用链和限制见[D2交付](../../docs/DATAOPS_D2_DELIVERY.md)。

- 完整离线回归：149模块、1262项通过，0跳过、0联网尝试。随后最终变更按受影响模块复测39项通过：D2核心17、D2队列/API 6、D1 API 4、D1工具12。**这些数字不相加为独立测试总数**，完整回归不是最终代码的另一轮全量重跑。
- 真实本地20条：v1核对80个字段，v2核对100个字段；v2复用80个旧字段结果，只增加贮藏读取，12次逻辑来源读取（1次查询＋11条有候选的记录）。原v1与来源库hash不变。
- 合成多候选：两个不同Python进程完成等待与人工续跑；同一任务累计7次替身决策，没有重跑前三次决策。候选选择仍不等于身份签核。
- 浏览器：多候选等待、刷新、选择、v2、五表下载hash、旧v1保留、owner隔离通过，0 JavaScript错误、0外部请求。主应用D1真实20条浏览器兼容检查也通过；旧聊天页的5个外部资源请求被拦截，未加载。
- 外部模型调用为0。原生`AIMessage.tool_calls`解析通过离线契约测试；本轮**未测真实DeepSeek决策收益、成本或业务准确率**。

| 文件 | 内容 |
|---|---|
| [ci_summary.json](ci_summary.json) | 完整回归原始摘要，另列最终复测，不覆盖前次结果 |
| [source_consistency.json](source_consistency.json) | 20条来源对照、增量读取与跨进程HITL |
| [browser.json](browser.json) | 最终源码的合成页面验收 |
| [d1_browser.json](d1_browser.json) | 主应用真实20条兼容验证 |
| [development_failures.json](development_failures.json) | 开发失败、修复与重测说明；不把首次失败改成通过 |
| [implementation_hashes.json](implementation_hashes.json) | 最终源码、测试、依赖与关键文档SHA256 |
| [final_checks.json](final_checks.json) | 语法、文档链接及验收版本核对 |

私有完整记录位于`var/dataops_d2_complete/`；包含事前冻结协议/参考、工作区、checkpoint、v1/v2和截图。此前`var/dataops_d2_v1/`、`var/dataops_d2_final/`与`var/dataops_d2_release/`是开发过程记录，保留但不混充独立评测集。公开目录不复制药典片段、原始JSON或数据库。

复现时使用新输出目录：

```bash
.venv/bin/python -m scripts.run_offline_ci --out var/ci_dataops_new
.venv/bin/python -m scripts.check_dataops_d2 --out var/dataops_d2_new
```

后者需要D1冻结参考、本地原始资料/索引和当前虚拟环境；阶段结果仍为`source_reconciled_pending_human_signoff`。30题冻结评测、F/P/A对照、50条规模、真实模型授权与新镜像属于D3，不在本记录中标完成。中文截图环境缺少完整字体，DOM文本和交互已验，不宣称字体布局已人工审阅。
