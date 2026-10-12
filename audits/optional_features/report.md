# M0-04：可选能力配置化验收

日期：2026-10-12。原医疗应用启停功能的代码与实际运行验收；不调用付费 API。

## 已完成

- ENABLE_IMAGE_ANALYSIS / ENABLE_SPEECH / ENABLE_WEB_SEARCH 默认 false，非法布尔值明确报配置错误。
- 关闭状态不创建相应配置对象，不导入 CV / 语音 / 联网模块，不加载 CV 权重；RAG 模块按请求加载，Docling 按文档导入加载。
- CV 分析器和语音客户端第一次使用时初始化并缓存；联网导入与路由受可用性检查限制。
- 上传、转写、语音生成在功能关闭时返回 403；语音开启但缺密钥返回 503。输入检查先于上传体解析。
- 路由与 RAG 低置信度回退不能进入禁用/未配置联网工具，返回明确 unavailable 消息。
- 页面隐藏禁用的上传、录音、播放功能；关闭语音不启动清理线程。
- 图状态显式声明 next / disabled_feature；修复路由和验证节点嵌套返回，供新禁用分支正确工作。

## 测试与证据

| 验证 | 结果 |
|---|---|
| 开关单测及真实图/API 集成 | 13/13 通过，无跳过 |
| 会话隔离/记忆裁剪回归 | 3/3 通过 |
| runtime 回归 | 5/5 通过 |
| 比较评测契约回归 | 6/6 通过 |
| 实际主服务 HTTP 检查 | 6/6 通过 |
| 实际主页面浏览器功能检查 | 5/5 通过 |

图/API 测试在现有医疗容器的 Python 3.11 环境中运行。MetaPath 导入阻断器拒绝 torch、torchvision、cv2、docling、ElevenLabs、pydub、Tavily 及图像/联网模块，文本图和 /chat 仍工作。可选凭据为空且 CV 配置不存在。开启分支使用注入 SDK/工具，验证首次使用和缓存，不冒充真实 CV 推理或供应商调用。

会话 A/B 隔离、未提供会话 ID 隔离和活跃 checkpoint 历史裁剪继续通过。测试模型、guardrail 和联网工具均为确定性替身，不调用付费接口。

主服务在 7860 重启后，/features 显示全部关闭；/health 和首页返回 200；/upload、/transcribe、/generate-speech 返回 403。新启动日志不包含 CV 模型加载。浏览器确认上传控件隐藏/禁用，无录音按钮；模拟文本回复正常显示且无播放入口、无 JavaScript 错误。外部展示资产和聊天响应使用测试替身。

结果：[HTTP 检查](http_checks_2026-10-12.json)、[浏览器检查](ui_checks_2026-10-12.json)。代码：[开关](../../features.py)、[回归](../../tests/test_optional_features.py)、[浏览器脚本](../../scripts/verify_optional_features_ui.py)。

## 运行状态与边界

当前 `medical-assistant-current` 挂载源码、运行本轮文本配置；7863 独立药材服务未改动。原全量医疗镜像未重建/发布，仍包含已安装的可选依赖；禁用行为不等于镜像已精简。

配置变更要求重启。容器已有环境变量由 env-file 注入时需重新创建容器使变更生效。原 MemorySaver 会在进程重启时清空，持久会话属于 M3。

真实启用供应商调用/CV 推理未在本轮复测。文本 RAG 的检索/重排依赖仍可在 RAG 使用时初始化；不是所有文本查询都永久不导入 torch。State 全面统一与图复用仍属 M0-02/03。data/uploads 公开静态挂载尚在，下一项 M0-05 负责受控来源访问。

用法：[OPTIONAL_FEATURES.md](../../docs/OPTIONAL_FEATURES.md)。
