# Tavily 配置后的复测

日期：2026-10-09。使用同一发布镜像和知识库副本，未修改业务代码。

Tavily 工具独立调用成功，返回 2 条带 URL 的结果。密钥内容未输出。

| 项目 | HTTP | 耗时 | Agent |
|---|---:|---:|---|
| health_after_env_reload | 200 | 0.02s | — |
| medical_rag_with_tavily | 200 | 87.87s | RAG_AGENT, WEB_SEARCH_PROCESSOR_AGENT |
| request_sources_with_tavily | 200 | 60.62s | RAG_AGENT, WEB_SEARCH_PROCESSOR_AGENT |

两次问答均返回 RAG_AGENT, WEB_SEARCH_PROCESSOR_AGENT，说明联网回退已执行，先前缺失密钥导致的 500 在这两条用例中消除。

限制：来源追问回答声称无法查看上一回答完整原文，随后列出新的搜索来源；没有实现逐条对应上一回答的证据追溯。HTTP 200 不能作为来源正确性的证明。本次未逐一核对外部网页内容或医学陈述。

第一题包含首次模型加载；耗时不等同于纯 API 延迟。仍需修复引用定位、上下文传递、会话隔离和组件生命周期。

旧测试容器保留为 medical-assistant-local-test-before-tavily。7860 开发容器的更新和健康状态以本轮最终确认结果为准。

7860 更新确认：已重新创建 medical-assistant-current，旧容器保留为 medical-assistant-current-before-tavily。新容器中 TAVILY_API_KEY 存在，GET /health 返回 200/healthy；当时 Docker 定时健康状态仍为 starting。未在开发容器重复消耗模型用量执行问答。
