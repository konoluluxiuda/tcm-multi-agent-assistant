# D3：本地评测与真实模型V1/V2记录

更新：2026-10-30。范围和使用方式见[D3报告](../../docs/DATAOPS_D3_EVALUATION.md)、[3–5分钟演示](../../docs/DATAOPS_DEMO.md)。本地验收已交付；真实模型V2已执行，F/P各3/3、A为0/3，Docker运行未完成，因此D3不标整体完成。

## 已经运行的结果

| 范围 | 结果 | 口径 |
|---|---|---|
| 完整离线回归 | 150模块、1272项通过，0跳过 | 此前12项、诊断后37项、单动作改进后45项受影响检查均通过，计数重叠不相加 |
| 开发18题首轮 | F/P/A各16/18 | 两题均为评测器重复candidate_id参数错误；原失败保留 |
| 开发18题修复后 | F/P/A各18/18 | 三种程序策略替身，不是LLM准确率 |
| 作者前瞻保留12题首测 | F/P/A各12/12 | 事前冻结，运行后已转回归，不再是未见题 |
| 真实50条v1/v2 | 200/250个字段来源对照通过 | v2复用200个旧字段，逻辑读取63→13；来源和v1保持不变 |
| 同候选池6题排序 | 词面/混合首位3/6，重排/字面规则6/6 | 已知开发题，不是全库Recall或独立业务准确率 |
| 三题真实模型首轮 | F 3/3，P/A各0/3 | 实际6次请求全部HTTP400，未进入有效模型业务执行；不解释成模型能力准确率 |
| 三题真实模型V2 | F/P各3/3，A为0/3 | 实际8次请求全部HTTP200；A未闭环，精确中断原因未确认 |

30题为结构化Goal业务契约，不测自由文本Goal编译。等待、正确部分交付、源不可用时明确失败和范围外拒绝，都按各题事前契约评分；“契约通过”不意味着全部字段补齐或全部生成表格。逐题F/P/A结果见[CSV](case_results.csv)。

## 真实模型失败与修复

原始协议及6次HTTP收据见[live_v1.json](live_v1.json)，私有全记录在`var/dataops_d3_live_v1/`。没有自动重试，也没有将未使用的调用上限转成修复后重测。首轮未保存错误正文，不能确认400的唯一根因。

代码未显式禁用默认thinking，同时强制required工具选择；[官方Chat API](https://api-docs.deepseek.com/api/create-chat-completion/)明确禁止该组合，[thinking说明](https://api-docs.deepseek.com/guides/thinking_mode/)说明默认启用。已显式禁用thinking，并将输出上限映射为DeepSeek文档中的max_tokens；用真实SDK构造请求交给MockTransport验证线上JSON，另加脱敏错误收据。**离线修复通过不等于外部兼容重测通过。**

原始[v1预览](live_preview.json)保留，[v2协议](live_preview_v2.json)获单独授权后已执行一次。V2实际8次HTTP200，F/P各通过3/3，A为0/3；不能因剩余上限自动重跑。结果与冻结源码hash见[live_v2.json](live_v2.json)。A三次未接受的响应缺少原始动作诊断，不能确认是零/多工具调用或SDK解析问题。请求中只允许固定合成数据；真实ETCM、药典、PDF与用户历史未外发。价格与有效usage未知时保留null，不填写0费用。

## 证据文件

| 文件 | 内容 |
|---|---|
| [ci_summary.json](ci_summary.json) | 完整回归及后续12/37/45项受影响复测的原始摘要 |
| [offline_comparison.json](offline_comparison.json) | 开发首轮、修复后、作者保留首测；各自协议/参考SHA保留 |
| [case_results.csv](case_results.csv) | 30题逐策略契约与实际终态 |
| [scale50.json](scale50.json) | 真实50条单次本机规模、上下文、增量与文件逐格校验 |
| [retrieval.json](retrieval.json) | 同候选池词面/混合/重排/简单规则 |
| [development_failures.json](development_failures.json) | 开发失败及修复，未覆盖原轮次 |
| [live_v1.json](live_v1.json) | 已执行的真实模型首轮失败 |
| [live_v2.json](live_v2.json) | V2逐题结果、8次HTTP收据、运行源码hash和usage缺口 |
| [v2_diagnostics_followup.json](v2_diagnostics_followup.json) | 后续本地诊断补充及受影响回归，未外部重测 |
| [native_action_contract.json](native_action_contract.json) | 单动作校验、受限纠正、SDK模拟接口和父任务失败报告；未外部重测 |
| [live_preview_v3.json](live_preview_v3.json) | 包含显式格式纠正机会的后续提案，未运行 |
| [docker_status.json](docker_status.json) | 配置静态检查与引擎不可用事实；无镜像构建/运行/push |
| [image_source_manifest.json](image_source_manifest.json) | Docker允许源码的实际树hash，包含未提交/未跟踪代码 |
| [implementation_hashes.json](implementation_hashes.json) | 最终实现、测试、题集和关键文档SHA |
| [final_checks.json](final_checks.json) | 语法、链接、版本与记录核对 |

完整私有目录：`var/dataops_d3_v1/development`（首轮）、`var/dataops_d3_v2/development`（修复后）、`var/dataops_d3_v1/holdout_first`、`var/dataops_d3_v2/scale50`、`var/dataops_d3_v2/retrieval`、`var/dataops_d3_live_v1`、`var/dataops_d3_live_v2`、`var/dataops_d3_v2_diagnostics`及`var/dataops_d3_final_after_live`。公开目录不复制真实资料片段、原始JSON、数据库、密钥、会话历史或运行XLSX。

后续已完成单动作错误处理的本地验收，见[契约记录](native_action_contract.json)；新增V3仅预览，真实A结果仍是V2的0/3。本轮没有证据证明A比F更有业务收益，也没有证据支持为精确字段默认启用重排。当前优先保留确定性查询，把LLM用于后续待验的自由目标提案和真正需要观察决策的任务；容器引擎恢复后再做实际部署验收。未创建Git提交、未push、未发布镜像。
