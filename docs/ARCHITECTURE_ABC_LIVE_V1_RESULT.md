# A/B/C同环境真实模型对照：首轮结果与协议缺口

2026-10-28。用户明确授权后，各候选执行一次，共9次实际HTTP请求，无重试或追加任务。本轮为1/3完整完成，未把未完成任务统计为成功。真实药典、ETCM、PDF和用户历史均未外发。

## 实际结果

固定问题、环境和验收器见[执行前续审](ARCHITECTURE_ABC_V1_REVIEW.md)。提供方为api.deepseek.com的deepseek-flash；LangGraph1.2.18、Core1.7.0、OpenAI适配器1.7.3、Pydantic2.14.1。

| 指标 | A单循环 | B Deep Agents | C主管 |
| --- | ---: | ---: | ---: |
| 最终状态 | complete | partial / invalid_action | partial / invalid_action |
| HTTP请求 | 7 | 1 | 1 |
| 底层查询 | 6 | 0 | 0 |
| 共同靶点 | 23/23 | 未查询 | 未查询 |
| 通过本地核验的双方见证行 | 48 | 0 | 0 |
| 时间（秒） | 8.059 | 1.466 | 1.049 |
| 提供方输入token | 42679 | 793 | 813 |
| 提供方输出token | 573 | 258 | 98 |

9次响应均提供usage；费用保持null。B/C在首轮停止，因此其时间和token不能与完整完成的A作为成本优劣比较。没有据此认定A框架整体胜出。

## 失败来自什么

公共提示要求每轮一个动作，宿主也只接受一个tool_call，但SDK请求没有显式设置parallel_tool_calls=false。

- B首个响应包含4个read_source，分别读取Alpha/Beta的text/structured。
- C首个响应包含2个delegate，分别针对Alpha/Beta。
- 工具名、对象和参数本身符合业务范围；宿主因为同一响应里有多个动作，拒绝整轮，实际查询尚未派发。

这说明“只在提示里约束单动作”不足。也暴露了替身测试的局限：固定Driver始终只返回一个动作，因此27项离线检查通过仍没有发现真实模型可能批量调用工具。

本轮公平结论是：**当前协议与B/C此次实际模型响应不兼容**。不能扩大解释为B/C不会拆任务、查资料能力差，或Deep Agents本身不支持批量工具调用。

## 本地诊断与修正准备

将已记录的B/C首轮动作在合成目录中逐条串行重放，均通过当前业务账本：B完成四次资料读取，C完成两次委派（子流程使用固定SDK替身）。这只验证首轮工具参数合法，不是完整任务重跑，更没有新增外部请求。

V2对照准备采用SDK显式`parallel_tool_calls=False`，并在HTTP传输层要求该字段为false，继续保持原单动作验收门槛。新增“缺少该字段或传true必须在发送前拦截”的测试。真实SDK＋MockTransport及B/A/C相关回归共28项通过。

该修正只完成本地验证，未验证DeepSeek在本次配置中一定遵循此字段；即使提供方仍返回多个动作，宿主也会继续拒绝。后续若需要比较批量/并行工具的收益，应单独定义批量准入、去重、依赖和累计预算契约，不能在一轮严格串行对照中临时放宽规则并称为同条件实验。

## 选型影响

保留A作为当前接入基线。B/C仍是候选，首轮结果不足以淘汰它们。先修正协议请求，再考虑新的配对测试；不能拿失败的首轮调用数说B/C更省资源。

路线图和生产入口未修改。原16题开发集仍未执行，本题不代表泛化成功率。

本轮授权范围为各候选一次、不重试，已按此结束。36次只是最大上限；实际使用9次，剩余上限不自动授权修复后的第二轮。

## 证据

- [原始结果、实际tool_calls及usage](../audits/architecture_abc_live_v1/result.json)
- [执行源码与原始结果哈希](../audits/architecture_abc_live_v1/source_manifest.json)
- [B/C首轮本地重放](../audits/architecture_abc_live_v1/local_replay.json)
- [重放脚本](../scripts/replay_abc_batch_diagnostic.py)

原执行脚本保存在`audits/architecture_abc_live_v1/executed_source`，不会用当前V2脚本覆盖V1记录。
