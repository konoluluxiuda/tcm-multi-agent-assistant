# 聊天请求诊断

当前本地交付：本功能已进入7876的`tcm-research-chat:c4de8bc`镜像，见[版本、启动及容器验收](releases/RESEARCH_LOCAL_C4DE8BC.md)。下方源码阶段记录保留其当时的验收边界；7875源码预览需按命令另行启动，不视为当前运行状态。

2026-10-20源码增量；对应AP-01失败可追溯与ENG-03工程排查，不改变模型、检索或会话策略。

## 使用

`POST /chat`的成功、部分结果、工具不可用、参数校验失败和内部错误均返回服务器生成的`X-Request-ID`。客户端传入的同名请求头不复用，不将编号当作会话凭证。`Server-Timing: chat;dur=...`表示请求进入诊断中间件至响应头产生的服务器耗时（毫秒），不包含浏览器网络时间，不是模型推理时间或完整下载耗时。

页面发生请求错误时显示中文提示和排查编号；工具错误卡及部分结果保留原内容并追加编号。发生断网且没有响应头时不编造编号。非JSON错误返回仍保留已收到的编号；服务端错误文字以`textContent`显示，禁止当HTML插入。恢复输入控件但不自动重发请求；409/410依然要求刷新，503不切换临时记忆或模型兜底。

在运行了该源码版本的服务上重现一次失败，复制页面编号后检查日志：

```bash
docker logs --since 10m tcm-research-candidate-chat-1 2>&1 | rg 'chat_request'
```

按编号查找对应JSON行，依次看HTTP状态、工具类别、处理阶段和错误类别。编号仅用于定位这一条请求，不能恢复用户身份或读取聊天。直接调试接口时可在浏览器网络面板查看响应头，或用`curl -i`查看`X-Request-ID`及`Server-Timing`。

默认Uvicorn info级别输出示意：

```json
{"event":"chat_request","request_id":"0123456789abcdef0123456789abcdef","tool":"research","phase":"execution","error_kind":"internal_error","model_calls":null,"http_status":500,"duration_ms":45.11,"outcome":"internal_error"}
```

字段口径：

| 字段 | 含义 |
|---|---|
| tool | 实际结果的固定工具类别；失败发生在结果返回前时仅表示已选择类别，未知保留unknown |
| phase | validation、routing、execution或response；是HTTP边界阶段，不冒充模型内部执行轨迹 |
| error_kind | none、internal_error、memory_unavailable、conversation_conflict、session_expired；预期工具失败另看HTTP状态及原工具结果 |
| model_calls | 工作流已返回的调用次数；未知为null，不能据此推断为零；不额外调用模型 |
| outcome | success、partial、unavailable、rejected或internal_error；不是答案准确率 |

## 数据与运行边界

新增结构化日志只记录上述固定分类、随机请求编号、计数与耗时，不读取/记录请求正文、查询参数、Cookie、Authorization、会话标识、问题原文、个人笔记、来源片段或异常字符串/堆栈。原有HTTP访问日志和旧医疗分支日志不是这个新增记录的组成部分；本功能不承诺替代整个应用的日志脱敏审计。

未预期异常统一返回安全错误，不再把`str(exception)`作为`/chat`的500详情。路由阶段也覆盖；纯ASGI中间件避免原HTTP中间件在返回安全响应后再次抛出异常。本接口当前返回普通JSON，不提供流式响应；响应已经发送后无法替换成新错误响应。

日志沿用应用标准输出及Uvicorn日志等级，warning级别不会输出info诊断。没有新增数据库、监控服务、身份关联、跨服务追踪或日志检索接口；保留时间由部署的日志驱动/运维策略管理。健康轮询及其他接口不生成chat_request记录。

## 验证与当前交付

新增8项离线测试，覆盖成功与隐私字段排除、部分结果及模型失败调用数、存储503/冲突409/过期410、执行与路由异常、参数校验/功能关闭、并发编号隔离、健康轮询不计入。

隔离7877浏览器验收6项：真实500与服务器编号一致；409文本不能注入HTML；503保留工具卡；非JSON错误；断网不编造编号；无JS错误、无自动重发且输入恢复。仅使用合成故障与禁用资料工具，模型调用0。首次路由异常测试失败记录保留在本机`var/request_observability_v1/targeted.json`，修复后记录及全量结果位于同目录。

完整回归71模块611项通过，跳过0、网络尝试0；见[验收摘要](../audits/request_observability_v1/summary.json)。

本增量尚未构建进Docker；7876仍运行本地干净提交`0d65be4`的`local-20261020`镜像，先前603项离线/20项容器验收属于该版本。此处浏览器验收是源码隔离服务，不将它写成容器验收。本增量纳入本轮本地Git提交，没有push或Hub发布，没有新增真实资料外发。
