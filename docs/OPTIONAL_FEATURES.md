# M0-04：原医疗应用可选功能开关

日期：2026-10-12。用于 `app.py` / `agents.agent_decision`，独立药材查询服务无需这些开关。

## 默认文本模式

| 环境变量 | 默认 | 启用条件 |
|---|---|---|
| ENABLE_IMAGE_ANALYSIS | false | 显式 true；首次图像请求需要原 CV 依赖、权重和可用视觉端点 |
| ENABLE_SPEECH | false | 显式 true + ELEVEN_LABS_API_KEY；使用时需要语音 SDK / 音频处理依赖 |
| ENABLE_WEB_SEARCH | false | 显式 true + TAVILY_API_KEY |

可使用 true/false、1/0、yes/no、on/off（大小写无关）。非法布尔值启动时报清晰配置错误。仅配置密钥不会自动启用相应功能。

关闭全部可选功能：

```dotenv
ENABLE_IMAGE_ANALYSIS=false
ENABLE_SPEECH=false
ENABLE_WEB_SEARCH=false
```

仍需有效的 `CHAT_*`、`EMBEDDING_*` 配置用于原医疗文本图及 RAG；这些不是本任务禁用的可选配置。独立 `tcm.api` 不依赖它们。

图像、语音和联网配置对象在关闭状态下为 None。CV 分析器、语音 SDK 与联网处理器按需导入/初始化；RAG 按请求创建，Docling 只在文档导入时加载。文本服务启动不需要可选密钥或 CV 权重；真正的 RAG 查询仍会使用检索依赖和重排模型，此轮没有把它们移除。

## API 和页面

`GET /features` 返回开关、开关/凭据是否满足以及不可用原因，不暴露密钥。`available=true` 表示开关/凭据条件满足，不等于已经验证外部服务、模型权重或视觉模型兼容性。

- 图像关闭：`POST /upload` 返回 HTTP 403、status=feature_disabled，上传入口隐藏且禁用。
- 语音关闭：`POST /transcribe`、`POST /generate-speech` 返回 HTTP 403，录音和播放入口不显示，不启动语音清理线程。
- 语音显式开启但缺密钥：返回 HTTP 503、status=feature_unavailable、reason=missing_credentials。
- 联网关闭或未配置：路由器不能进入联网工具；RAG 资料不足时返回明确不可用提示，不调用 Tavily 或将失败包装为“没有研究”。

禁用上传/语音请求在解析请求体之前被检查，所以未提供上传文件的请求也得到功能禁用响应。没有移除旧端点契约，明确开启时可按原用途调用。

配置在启动时读取，调整后需重启进程。若容器通过 `--env-file` 注入配置，修改宿主机文件不能更新已存在容器的环境，需要用原创建配置重新创建容器。当前 `medical-assistant-current` 挂载源码，重启会加载新代码；全量镜像还没有重建发布。

## 验证

```bash
python -m unittest discover -s tests -p test_optional_features.py -v
python -m unittest discover -s tests -p test_agent_sessions.py -v
```

集成测试要求原医疗依赖；只有轻量/标准库环境会跳过集成部分，不应把跳过算成通过。测试使用真实 LangGraph 和 FastAPI，聊天/工具响应使用确定性替身，不调用付费 API。导入阻断器确保关闭状态不导入 CV、Docling、ElevenLabs、pydub 或联网模块；开启分支用注入的 SDK/工具替身验证按需加载与缓存。

实际容器测试命令：

```bash
docker exec -e ENABLE_IMAGE_ANALYSIS=0 -e ENABLE_SPEECH=0 -e ENABLE_WEB_SEARCH=0 \
  medical-assistant-current python -m unittest discover -s tests -p test_optional_features.py -v
```

浏览器功能验证：`python -m scripts.verify_optional_features_ui`（需 Playwright/Chromium，主应用在 7860）；文本回复及外部展示资产为测试替身。

[验收报告](../audits/optional_features/report.md)。

## 当前边界

三个开关默认值由旧版始终启用改为关闭；需要原图像、语音或联网功能时应显式开启。此轮修复图分支所需的 next 状态及嵌套返回，完整 State 统一、图编译复用和生命周期整理仍属 M0-02/03。

后续 M0-05 已移除 data/uploads 整目录挂载并提供受控资料/匿名会话结果接口，见 [访问策略](SOURCE_ACCESS.md)。尚未重建原全量医疗镜像，依赖仍安装在其中；本任务验证关闭后的启动与路由行为，不宣称整个镜像已经精简。
