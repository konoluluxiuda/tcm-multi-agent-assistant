# J01：真实 SDK 的离线协议验收

2026-10-24。本增量使用项目已安装的 `langchain_openai.ChatOpenAI` 与 OpenAI SDK，将请求交给 `httpx.MockTransport`，验证实际序列化/反序列化。不同于先前直接返回 `AIMessage` 的假模型，但HTTP响应仍是合成脚本，不代表真实模型决策效果。

运行：

```bash
/tmp/tcm-ci-mail-env/bin/python -m scripts.eval_research_sdk
```

无需密钥、数据目录或外网；CLI关闭可选LangSmith追踪，不加载 `.env`。未提供真实服务调用开关。结果标记 `real_sdk_synthetic_http`、`external_model_calls: 0`、`provider_quality_evaluated: false`。

调用链：LangGraph → ChatOpenAI原生工具绑定 → OpenAI请求序列化 → MockTransport → SDK解析工具调用 → 本地合成工具 → ToolMessage携带观察ID和结果回传 → 下一次SDK调用。

本例第一次检索为空，第二次换词检索，第三次读取返回的来源ID，第四次引用观察ID结束。实际发送4个模拟HTTP请求，执行3次工具；完成状态仅覆盖要求的工具观察，不宣称完成任意科研目标。输入400/output80 token来自固定模拟响应，不能当作成本或性能数据。

新增：

- `tcm/research_run_sdk.py`：显式传入模型、端点、密钥及由调用者管理的HTTP客户端。默认10秒、输出650 token，SDK重试关闭；模型和工具调用总数仍由已有执行器限制。
- `tcm/research_run_protocol.py`：独立的合成HTTP响应，按观察结果切换工具。
- `scripts/eval_research_sdk.py`：离线可复现命令。
- `tests/test_research_run_sdk.py`：真实SDK协议、观察回传、调用预算、503不重试、错误JSON不执行工具、输出上限验证。

26项定向测试通过（5个SDK、12个执行器、9个范围/本地适配），网络尝试0。命令行验收退出0。汇总见 `audits/research_sdk_v1/summary.json`。

## 实际发现与限制

当前安装的SDK将 `max_tokens` 转成HTTP中的 `max_completion_tokens`；测试验证实际字段，未假定第三方服务已经兼容。接DeepSeek前需验证服务端参数支持，必要时在适配器统一转换。输出上限不等于整个运行的输入+输出token硬预算。

本增量没有改聊天默认驱动、没有连接真实数据库、没有增加远程模型调用。聊天页仍是之前明确标注的本地固定流程。现有问句的一次意图识别授权不覆盖工具结果多轮外发。

下一步是固定合成任务集的真实模型验收：具体端点、输入范围和请求次数确定后获得授权，先验证工具协议兼容及任务完成率，再比较固定流程和观察驱动流程。不要把当前合成响应的成功率写成DeepSeek准确率。持久化、SSE和多worker控制仍属于J04。

回滚只需停用上述独立命令/适配器；无数据迁移，无服务配置变更。

后续：[两题真实模型验收方案](RESEARCH_LIVE_SYNTHETIC.md)已完成脚本与离线预检，本次已授权执行8次请求，结果0/2；协议可用但存在合成契约问题，详见该方案的运行记录。

V2真实重测已完成：7次请求、两题通过；V1失败记录继续保留。详见[完整验收与限制](RESEARCH_LIVE_SYNTHETIC.md)，不将两道合成题推断为通用准确率。
