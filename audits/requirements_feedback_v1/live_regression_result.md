# live_regression_v1 执行结果

运行：`live_regression_v1`
实现基线：`74f2521`；manifest：`64907979d098bad5f3884d06fb738aa4ca89b389eb893f783ce775d542d75884`
用户授权：用户明确「完成所有阶段任务并进行记录，可以使用调用」
状态：完成
进程退出码：实际进程回执未独立核实。原报告记0，但脚本对本次未全通过评分应返回1；报告生成不能证明退出0。
冻结校验：执行前通过；执行后通过
组合：应有 16；已落盘 16；中断 0；未开始 0
真实派发：G 9/9，W 14/27，合计 23/36（未用满预算）
HTTP 错误：0；最终未知派发：0；已知 token：89,504；用量未知：0；`provider_stopped=false`
业务：G 2/6，W 3/6；边界：G 2/2，W 2/2；合计 G 4/8，W 5/8

## 逐场景

| 场景 | G 结果 | W 结果 | 失败维度 | 本地证据 |
|---|---|---|---|---|
| HV2-01 | success ✓ | success ✓ | — | `cases/HV2-01_{G,W}/` |
| HV2-07 | clarification ✗ | success ✓ | G: terminal/objects/requirements/sources/answer_values | `cases/HV2-07_{G,W}/` |
| HV2-13 | clarification ✗ | success ✓ | G: 同上 | `cases/HV2-13_{G,W}/` |
| HV2-20 | success ✓ | clarification ✗ | W: terminal/objects/requirements/sources | `cases/HV2-20_{G,W}/` |
| HV2-29 | clarification ✗ | failed ✗ | G: +table；W: turns/execution/terminal/objects/requirements/sources/answer_values/table | `cases/HV2-29_{G,W}/` |
| HV2-32 | clarification ✗ | clarification ✗ | G/W: terminal/objects/requirements/sources/answer_values/table | `cases/HV2-32_{G,W}/` |
| HV2-35 | clarification ✓ | clarification ✓ | —（边界题，期望澄清） | `cases/HV2-35_{G,W}/` |
| HV2-36 | clarification ✓ | clarification ✓ | —（边界题，期望澄清） | `cases/HV2-36_{G,W}/` |

## W 胜 / 负

- **W 胜**：HV2-07、HV2-13（G 澄清失败，W 成功）。
- **W 负**：HV2-20（G 成功，W 澄清失败）。
- **双方均失败**：HV2-29（W 为 `correction_exhausted`：工具反馈纠正未形成有效需求；G 亦澄清）、HV2-32（双方均澄清，table 维度失败）。

## 效率

| 策略 | 派发 | token | P50 | P95 |
|---|---:|---:|---:|---:|
| G | 9 | 39,358 | 4.45s | 16.48s |
| W | 14 | 50,146 | 6.54s | 8.46s |

## 证据

- 正式报告：`var/requirements_feedback_v1/live_regression_v1/reports/0001.json`
- 账本：`var/requirements_feedback_v1/live_regression_v1/ledger.sqlite3`（16 案 finished；G 9 / W 14 派发均 HTTP 200）
- 私有执行日志：`var/requirements_feedback_v1/live_regression_v1-execute.private.log`
- 本地执行观察：`var/requirements_feedback_v1/live_regression_v1/cases/<场景ID>_{G,W}/`
- 冻结边界：`manifest.json`、`protocol.json`、`preflight.json`

## 限制

- 这是**已见题故障回归**，不是独立未见题；题目来自已看过的 live_v2，仅 split 改为 dev。
- W 相对 G 在 8 个场景上 2 胜 1 负，样本过小，**不能**推出统计显著或泛化结论。
- 默认策略、部署、镜像、Git 均未改动；未推送远程。
- 私有问句、原始返回、数据库与 PDF 均保留本地，未纳入公开证据。

## 逐案审核（2026-11-04）

- HV2-20 W首次提取把关系范围设为 `all`，触发 `full_relation_read_unsupported`；仅1次请求，未进入修正或复核。问题定位为范围解释与全量能力门禁的交互，不能仅凭G通过就断言所有澄清不合理。
- HV2-29与HV2-32 W首次返回均为ETCM单来源字段表格；适配器补入药典默认列，新策略以 `table_adapter_adds_unrequested_fields` 拒绝。模型随后返回 `needs_input`，却保留非空 `requirements`，违反Schema并触发 `correction_exhausted`。是首次提取加一次纠正，共2次请求，不是纠正两轮。
- HV2-29首轮未交付父表，因此未进入第二轮，`run_status=failed`；它与正常结束但澄清的单轮题必须区分。
- 两道表格题同时暴露单来源适配限制和澄清协议格式问题；未发现可据此判定为资料值错误的证据。修复后的离线回放不覆盖本次真实分数。

后续源码修复会使旧manifest与当前工作区不匹配，这是预期隔离；不再执行旧目录的execute/resume/score。原始16组合与23次回执完整保留。
