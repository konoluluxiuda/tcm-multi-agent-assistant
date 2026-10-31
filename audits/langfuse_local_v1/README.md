# 本地Langfuse接入验收

日期：2026-10-30。用户授权已更新：允许任务相关真实资料外发以及简单的本地自托管。本轮实际只将追踪写入本机Langfuse，没有调用外部模型或上传远程追踪平台，没有发布业务镜像或Git提交。

- Docker Desktop启动成功，独立`tcm-langfuse`六容器运行；四个存储服务健康，Web健康API与项目认证200；本地账号可实际登录并看到任务。
- Langfuse 4.72.0、SDK 5.0.4；镜像固定RepoDigest，新增Python依赖单独锁定，`pip check`通过。凭据在忽略的`var/langfuse_local`，不复制到本审计目录。
- 最终13模块91项受影响离线回归通过，0跳过、0联网尝试。覆盖固定交付、图/HITL/恢复、原生SDK与协议、规划、API、版本状态，以及新增追踪失败隔离、裁剪/脱敏、凭据幂等初始化；不是全仓库最新完整回归。
- 7912最终实际浏览器：白术/黄芪v1整理、下载hash、刷新恢复、追加贮藏v2、复用8个旧字段、v1文件不变、未支持要求不排队通过。0外部模型请求，0JS错误、0外部资源请求。
- 最终v1/v2追踪回读：13与11个观察，父子关联完整、workspace及revision可关联，有界输入输出存在；generation均0。它们是合成驱动执行真实本地资料，不是实际LLM测试。
- 7896最终实际浏览器：帮助问法、单药多字段、唯一成分候选、药典可读文本与固定工作台生成/下载/刷新通过，0JS/HTTP错误、0外部资源请求；`dataops.fixed`追踪回读通过，provider_requests=0。
- 7896/7912均保持原state和既有开关，增加`--with-langfuse`；源码版本一致、健康/工作台/ETCM/药典就绪端点200；原队列没有活动任务。临时7913关闭。

机器可读摘要：[acceptance.json](acceptance.json)。详细本地回执在`var/langfuse_verification_v1/final_tests`、`final_chat_browser.json`、`fixed_browser.json`、`readback.json`及`fixed_readback.json`；其中图片/真实任务内容不随Git上传。先前临时实例与中间测试回执保留，不与最终测试数相加。

原生generation和规划的映射由脚本化SDK响应离线测试验证，未新增外部模型请求；对照脚本已加case/strategy/experiment及开发契约分数，本轮只生成preview、未运行新的真实模型对照。Langfuse不是任务执行数据库：导出失败不触发重跑，未知派发继续遵守原回执规则。

未解决项：黄芪贮藏原始字段污染及独立页块参考、真实动态A兼容性/价值证据、普通聊天与检索全链路追踪、业务新镜像验收。历史V2 A=0/3保留，不能用本轮浏览器成功改写成绩。
