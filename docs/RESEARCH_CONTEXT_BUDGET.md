# Agent循环上下文预算第一版

2026-10-24。新模块 `tcm/research_context.py`，在 `tcm/research_run.py` 每次模型调用前执行。无需新增模型、网络或tokenizer下载。主聊天旧单次路由不因此变成循环，新循环自动应用默认策略。

## 行为

默认总大小64000字节，预留8000字节，因此允许输入56000字节；保留最近2个完整工具结果。测量包含消息结构、原生工具调用参数与工具Schema。这里是LangChain消息/Schema的本地JSON UTF-8大小，不是实际HTTP字节数或DeepSeek token数；预留也不是模型输出token硬约束。

输入超限才逐个压缩较早的ToolMessage。摘要保留证据ID、工具状态/错误、来源ID、数据版本、检索对象、分页信息、候选名称/ID与数量。靶点长列表和原始行替换为明确标记的计数，不把省略字段描述为不存在。压缩未减小体积时不替换。

原始系统提示、用户问题（含未完成要求/声明范围）、调用参数和最近工具结果原样保留。AI工具调用和ToolMessage仍成对，call ID不变。完整原始消息与证据不被修改，压缩只是下一次发送的视图；持久化运行仍保留原始工具观察及最终引用。

压缩后仍超限，返回 `partial / context_budget`，不再发请求。每次检查记入 `context_checks`，含压缩前后大小、上限、压缩的call ID和fits标记，方便在执行轨迹中核验。预算拒绝不增加model_calls。

示例配置（调用者通过代码指定）：

```python
from tcm.research_context import ContextPolicy
result = await run(question, sdk, backend,
    context_policy=ContextPolicy(max_bytes=64000, reserve_bytes=8000,
                                 keep_recent_tools=2))
```

## 验证

30项离线测试通过，无网络尝试。覆盖中文长结果、不可变原始证据、原始任务保留、工具消息配对、Schema计入、超限前拦截、短任务不改变，以及完整循环实际压缩后仍成功引用原始证据。报告 `audits/research_context_v1/summary.json`。

长循环测试的两个合成工具结果分别携带12000字符补充字段；模型调用前按需压缩旧观察，完整证据仍含这些字段。该测试使用脚本模型，只证明数据/协议处理正确，不能证明真实模型在压缩后完成率不下降。

## 明确限制及下一项

- 尚未实现模型可调用的 `read_run_result`/明细分页工具。完整证据留在运行记录中，不代表模型可以自动把被省略的字段取回来；需要细节但无法获得时应部分完成，不能凭摘要编造。
- 尚无DeepSeek精确tokenizer估算、累计token/费用硬预算或独立任务摘要。原始状态仍在内存/日志中，压缩解决输入规模，不解决长期内存增长。
- 工具调用参数、候选列表或最新结果本身很大时直接停止；不会强行截断用户要求来继续。
- 未执行新增真实模型调用，未重启预览或更新Docker。新增外部长任务效果比较需单独明确输入范围和预算。

下一步优先补按run/evidence ID获取有限明细的只读工具及来源绑定，避免只压缩却无法按需取回。之后再比较有/无压缩的真实合成任务完成率、输入token和延迟。

## 后续增量：按证据ID取回明细

已增加 `tcm/research_evidence_read.py`，工具名 `read_run_evidence`，注册在研究循环的原生工具Schema中。此前“无法自动取回”的限制现已对当前运行内的三个列表字段解除；跨运行读取、独立后台恢复仍未实现。

参数例：`{"evidence_id":"obs-1","field":"target_ids","offset":0,"limit":5}`。

- 仅访问本次执行状态中的原始 `search_disease_sources` / `disease_gene_profile` 观察，不接受run ID、路径或任意JSON字段。
- 字段限 `rows`、`target_ids`、`items`；每次最多20项，默认5项。data序列化最多6000 UTF-8字节（外层协议封装另计）；过大的单项返回明确错误，不截断原文。
- 返回 `source_evidence_id`、来源绑定、原观察总项数、offset和next_offset。分页只覆盖原工具响应中已有内容，不等于数据库全部原始行。例如原画像只含20条原始行，不能据此读到第21条，需业务分页工具另查。
- 读取失败、未知ID、字段缺失都作为工具观察回传，计入已有工具次数和重复调用限制；不会重新联网或读数据库。读取副本不能递归生成副本。
- 压缩摘要提示模型使用该工具。最新取回的明细按近期结果保留，仍受输入预算约束；无法容纳时仍停止，不偷偷扩大上下文。
- 最终需引用原始观察ID；只引用读取副本不能替代任务要求的原始查询证据。原始请求范围不因读取工具扩大。

39项定向测试通过，无网络尝试。验证实际循环触发压缩后取回P12345、完整证据未改动、最终仍引用原始观察、后端只执行原来的两次资料查询；同时覆盖分页、不存在/跨运行格式ID拒绝、超大条目、作用范围和工具预算。报告 `audits/research_evidence_read_v1/summary.json`。

这仍是脚本模型与模拟HTTP验收，没有新真实模型效果数据。既有两题真实验收使用旧工具定义，新工具Schema未做外部重测。本轮未更新Docker或重启预览；默认聊天本地驱动不会主动调用取回工具。

下一项可增加精确token估算/累计预算和压缩策略对照，或继续后台任务/SSE；真实模型长任务对照需单独明确调用范围，不能把本轮离线正确性等同于任务完成率提升。

## 后续增量：逐次/累计输入预算与对照

新增 `tcm/research_token_budget.py`。`InputBudget` 默认每次56000、累计160000估算单位；默认计数器 `TokenEstimator` 明确标记 `utf8_byte_proxy`，单位就是序列化UTF-8字节，不伪装成DeepSeek精确token。环境虽安装tiktoken，但没有默认本地词表缓存，本实现不调用可能下载词表的自动加载接口。

调用者可注入已加载的本地编码器 `TokenEstimator(encoding)`；计数包含消息和工具Schema，使用 `disallowed_special=()` 将特殊标记作为普通文本。即使使用真实BPE，它计数的也是本地序列化表示，不含已验证的DeepSeek chat template，因此仍是估算。当前没有实际本地BPE词表验收，测试仅核验编码器接口与预算算法。

每次决策先检查剩余累计预算，在必要时尝试压缩旧观察；压缩后仍超单次输入限制则 `input_call_budget`，超累计则 `input_cumulative_budget`。两者都在发出请求前停止。预算按预计尝试调用计入；即使请求失败，也不退还估算额度，避免自动继续消耗。原来的字节限制、调用次数和超时限制继续生效。

状态字段 `estimated_input_units` / `input_estimator` 与模型返回的 `input_tokens` / `output_tokens` 分开；每轮context_checks记录估算量及剩余额度。没有模型用量时不编造token数据。这是输入估算预算，不是输入+输出的实际账单硬上限，也不限制服务端计费误差。

```python
from tcm.research_token_budget import InputBudget, TokenEstimator
result = await run(question, sdk, backend,
    input_budget=InputBudget(per_call=56000, cumulative=160000),
    token_estimator=TokenEstimator())
```

离线对照命令：

```bash
/tmp/tcm-ci-mail-env/bin/python -m scripts.eval_research_context
```

六轮固定合成输入累计：原始279765字节，投影后163235字节，减少41.65%；六轮均满足该对照设置的38000字节输入上限，任务原文和工具消息配对保持。对照仅展示压缩投影，不应用默认累计160000额度执行六次模型调用；若实际运行超过累计额度，执行器会拒绝。无模型、无实际token计量，也不证明任务准确率不变。

31项定向测试通过，覆盖累计预算在第三次调用前拒绝、首轮超限阻止、估算与返回用量分离、长上下文取回和既有执行器/SDK协议。报告 `audits/research_input_budget_v1/summary.json`。未新增模型调用，未更新运行中的服务或Docker。

下一项建议回到J04后台任务/SSE，避免继续只扩充测试工具；真实长任务对照、DeepSeek token估算校准作为后续有预算的实验保留。
