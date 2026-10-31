# DataOps本地Langfuse观测

日期：2026-10-30。用户已允许任务相关资料外发，以及简便的本地自托管部署。该授权替代此前仅合成资料/逐次确认的限制；实验仍要有明确预算并保存实际派发记录。

## 部署与使用

采用独立Compose项目`tcm-langfuse`：Langfuse Web/Worker 4.72.0、PostgreSQL、ClickHouse、Redis及MinIO。六个镜像均固定实际拉取的SHA256摘要；SDK 5.0.4及本轮新增依赖单独锁定，没有替换业务LangGraph/OpenAI版本。流程依据[官方Compose方案](https://langfuse.com/self-hosting/deployment/docker-compose)和[Python观测API](https://langfuse.com/docs/observability/sdk/instrumentation)。这是单机开发部署，不包含高可用或自动备份。

```bash
.venv/bin/python -m pip install -r requirements-observability.txt
.venv/bin/python -m scripts.setup_langfuse_local up
.venv/bin/python -m scripts.setup_langfuse_local status
```

打开`http://localhost:3037`，使用`var/langfuse_local/access.json`里的本地账号登录，选择TCM DataOps，进入Observability → Tracing。该文件及`local.env`、`app.env`权限为600且被Git忽略；不要提交或粘贴密码/密钥。脚本支持WSL通过`docker.exe --context desktop-linux`控制Docker Desktop，不要求重新安装Linux Docker引擎。首次启动可能需要等待迁移、自动创建账号及项目。

开启动态替身与本地追踪：

```bash
.venv/bin/python -m scripts.preview_local_tasks \
  --port 7912 --state var/dataops_question_demo \
  --dataops-synthetic --with-langfuse
```

聊天中输入“整理白术和黄芪的功效、注意事项，保留出处并生成表格”，完成后输入“增加贮藏，保留旧字段”。在Langfuse查看两次`dataops.execution`，通过workspace_id和parent_id关联v1/v2。固定表单交付使用`dataops.fixed`；每次等待人工输入/续跑为一个执行段，不把人工等待时间算进该段执行耗时。

主预览7896也可在原命令后加`--with-langfuse`；它仍是固定工作台，不能仅因开启追踪就执行真实动态模型。`/api/runtime`中的`dataops_tracing_enabled`表示配置已开启，不表示采集服务一定健康。

## 采集范围与故障处理

| 路径 | 实现与边界 |
|---|---|
| 固定F | 目标、状态、工作区和文件摘要，明确provider_requests=0；尚未为每次来源读取建独立span |
| 动态图 | 每执行段、decision/tools/wait节点、工具事件、版本与来源修订；保留派发回执重放语义 |
| 原生A | 仅实际进入模型适配器才产生generation，记录有界提示、静态工具定义、有效动作/格式诊断、token用量 |
| 一次规划P | 一个generation记录提示、计划和用量；后续宿主动作不伪装模型调用 |
| 模型对照脚本 | case_id、strategy、experiment_id、实际transport请求增量；development_contract_pass是开发契约检查，不是独立业务准确率 |
| 旧聊天/检索/MCP | 本轮未统一埋点，不宣称全应用观测覆盖 |

启动默认`ENABLE_LANGFUSE=0`；只有`--with-langfuse`才加载`app.env`白名单。独立实验进程可加载该文件中的追踪变量，再显式运行既有有界实验命令；单纯配置追踪不会启动模型实验。`TCM_TRACE_CONTENT=1`采集有界任务内容；改为0只保留元数据/用量。每个嵌套结构最多8层、字典80项、列表60项、字符串4000字、内容预算48000字符；这是观测截断，不能作为完整证据副本或精确线上请求重放依据。真实模型请求协议、完整证据与产物仍在本地记录中。

应用显式埋点移除密钥、认证头、cookie、隐藏推理及异常正文；不向SDK传完整会话/数据库/PDF。SDK自身会添加公开项目标识和运行环境信息。新部署关闭Langfuse遥测及in-app Agent，不配置额外模型服务；只有Web 3037和媒体3038绑定localhost，其余数据库端口不映射主机。数据持久化在Docker命名卷，容器日志有轮转。

图的旧`model_calls`字段兼容保留，含义仍是driver决策步数。合成替身只有SPAN、provider_requests=0；真实调用的generation元数据`provider_request_attempts=1`表示进入模型调用，最终是否实际发到提供商以transport派发回执为准。缺失用量/未配置价格不硬填0成本。追踪导出失败不重跑模型、工具或文件生成；SQLite回执/checkpoint和本地产物继续作为业务事实。

## 验证与关闭

```bash
# 只读取本地追踪，不创建任务/调用模型；使用新的输出文件保留历史
.venv/bin/python -m scripts.check_langfuse_local \
  --expect-synthetic --minimum 2 --out var/langfuse_trace_readback.json

# 停容器但保留命名卷与本地凭据
.venv/bin/python -m scripts.setup_langfuse_local down
```

关闭应用追踪：停止预览，再使用原state命令启动且去掉`--with-langfuse`。不需要回滚业务数据库。若回退修改前的图代码，已运行中的checkpoint会因实现版本保护拒绝跨版本续跑；新建任务，保留旧结果。不要删除卷、原始数据或改写旧失败记录。

接入验收见[audit](../audits/langfuse_local_v1/README.md)。随后已完成[贮藏边界修复与原块参考](../audits/storage_boundary_v1/README.md)，并验证新索引版本进入任务追踪。真实模型A兼容验收和F/P/A价值对照仍待完成；Langfuse的安装不能代替这些证据。

后续[V3真实模型对照](../audits/dataops_live_v3/README.md)已回读9条评测轨迹、33个generation与9个开发契约分数，A通过1/3。最新[V4复测](../audits/dataops_live_v4/README.md)另有9条评测轨迹、33个generation及9个分数，均与本地HTTP回执/成绩对应；A为2/3，B04失败轨迹标WARNING、分数false。原V3轨迹和分数不回写；该调用链观测已实际验收，不代表动态A稳定性或业务收益通过。

[G V1目标理解](../audits/dataops_goal_v1/README.md)新增`dataops.goal_proposal` generation和`dataops.goal_evaluation`根轨迹。已回读24条评测根轨迹、12个generation、24个`development_goal_contract_pass`分数；R/G各8/12，8个失败false且WARNING。该分数按事前目标/固定交付契约评定，不把schema通过当理解正确。尚未接入HTTP提案路由，现有预览不因此调用模型。
