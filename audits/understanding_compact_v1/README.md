# 输入Harness修复与Schema精简（2026-10-31）

本轮同时修复外部走查提出的三个问题，并将一次理解的默认输入改为保守精简版`compact_v2`。保留Python、FastAPI、LangGraph、SQLite及原查询/交付链，没有新增框架或默认开启多轮模型。

## 三项问题的处理

1. **API层级文档**：聊天的资料任务响应中，`model_calls`在顶层，`understanding_requests_this_submission`在`assistant`内；两者是本次新增理解请求。`assistant.llm_calls`及`counts_scope`是原任务/理解累计值。修正了[上轮验收](../unified_harness_v1/README.md)的错误表述，用户指南增加JSON示例，没有增加第二份同名顶层字段。
2. **429后的同key重试**：根因是先保留工作区再入队，`JobBusy`被当成通用失败，聊天又把失败工作区当成重放。现在已知队列忙保存为`queue_busy`，目标和实际理解usage在入队前落盘；返回429与`Retry-After: 2`。同Cookie、key和问句重试时，仅重新尝试原工作区入队，不重新理解。重复忙碌仍为429，释放后只有同一个工作区和队列任务。来源变化或同key换问句返回409；未知入队错误不自动重试。旧`queue_not_enqueued`没有可靠忙碌标记，不自动迁移为可重试。
3. **跨部署源码摘要**：旧摘要包含仅本地拥有的`preview_local_tasks.py`及未打包的18份旧影像/RAG/文献模块，因此不能直接证明共享业务文件不同；首次部署核对189份镜像内源码与本地逐字一致，但旧比较范围仍不同。新`source_revision_scope=research-application-v3`只比较共享服务源码；启动脚本由`entrypoint_revision`及`disk_entrypoint_revision`单列。旧可选模块另用`optional_modules_revision`追踪；`restart_required`检查服务、入口及可选模块三部分漂移，保留旧字段。部署后另检查三个端口共同摘要及镜像逐文件清单，不用`restart_required=false`替代跨部署一致性验证。数据、环境配置仍独立。

API字段示例及重试说明见[新用户指南](../../docs/NEW_USER_GUIDE.md)。新增回归覆盖聊天/工作台的真实队列占用、重复忙碌、释放后同key继续、原目标/调用数保留、来源改变及后到的入队回执不能覆盖完成状态。

## 精简的选择过程

理解阶段原来同时发送`QueryIntent`中的完整参数Schema，以及提示词里重复的一份分析/业务工具参数。保守精简仅删除重复的参数正文和JSON Schema的`title`注释，保留工具说明、全部查询action、参数约束及原有语义规则。聊天仍提供`propose_research_goal`与`submit_query_intent`，资料任务页仍只提供前者；宿主的字段、对象、版本及交付校验不放宽。不按关键词隐藏某一组查询工具，不自动二次理解或格式重试。

先比较`full_v1`与激进`compact_v1`：后者还会在明确表格/续改时删掉查询工具和相关上下文。本轮4题里，激进版将“主要用途”多余澄清，因此**未入选**。随后单独冻结`compact_v2`四题回归，恢复两工具及相关上下文，只保留去重与title删除。两个批次的原始结果分别保留，不覆盖失败。

| 方案 | 本轮四题通过 | 服务商记录总token | 选择 |
|---|---:|---:|---|
| 完整版full_v1 | 4/4 | 30,122 | 原基线，保留 |
| 激进compact_v1 | 3/4 | 13,446 | 不采用，保留多余澄清失败 |
| 保守compact_v2 | 4/4 | 21,127 | 本轮默认；较相同四题基线少29.86% |

四题为功能介绍、两数据库介绍、两对象功效/贮藏表格、父表追加贮藏。后两项实际运行fixed交付，逐格回读并核验父对象、旧单元格及文件不变。此实验为比较输入表示而直接调用理解器；生产链仍先本地解析，规范问句不因实验而强制调用模型。

这是**作者编写的合成开发测试**，不是独立用户集。第二批复用首批的完整基线回执，不重复调用完整方案；不是随机重复实验或稳定准确率估计。总token下降不等于账单同比下降（缓存计价不同），也不证明多轮Agent比fixed更有用。既有真实资料题未重测，历史分数不变。

## 验证及复现

32模块、206项受影响离线检查通过，0跳过/联网尝试；最终Schema修改后重测相关7模块49项，版本摘要细化后再测相关3模块，最后补验忙碌后未知入队结果及相关7模块；结果按模块覆盖汇总，不将重测次数相加为总数。包括SDK模拟HTTP的八组合/未知结果停止、一次消费、逐格交付检查。见[离线结果](offline.json)。

首批冻结最多8请求，实际8次；后续独立冻结最多4请求，实际4次；共12次HTTP200、64,695 token，无自动重试。只发送合成问句、静态定义、合成名称候选与父表结构，不发送真实ETCM、药典、PDF、用户历史或密钥。Langfuse导出关闭，不声称本轮评分已写入追踪系统。

[首批协议](protocol-first.json) / [首批结果](model-first.json) / [后续协议](protocol-selected.json) / [后续结果](model-selected.json) / [对照摘要](comparison.json)。完整的逐次请求、响应、表格、源文件hash和执行标记在被Git忽略的`var/understanding_compact_v1/`，原协议已消费，不能重新执行。

新实验目录使用以下入口；只准备不调用模型，执行消耗该目录且失败也不复用：

```bash
.venv/bin/python -m scripts.eval_understanding_compact --out var/compact_future
.venv/bin/python -m scripts.eval_understanding_compact --execute --out var/compact_future
```

这会比较当前`full_v1`/`compact_v2`，最多8次请求。`--compact-only`仅准备保守版、最多4次，不代表已有授权预算可以无限复用。

## 部署与后续

真实模型实测后的最终改动涉及`tcm/runtime_status.py`版本摘要及`service.py`/`workspace.py`的入队失败保护；理解与Goal编译函数保持相同，未重新调用模型。忙碌重试若随后出现未知入队结果会停止；晚到的忙碌/失败回执不能覆盖已绑定的任务。该差异及对应构建清单另存，见[源码对照](source-comparison.json)。

本轮本地镜像为`tcm-research-chat:understanding-compact-v2`，无远程上传。7896/7912沿用原状态及启动参数；7914沿用原只读资料挂载、状态目录和端口。7912启用一次理解兜底，7896/7914保持本地解析。最终三个端口的共享研究摘要一致，190份镜像源码逐文件匹配；94份原有XLSX的hash不变，Cookie连续有效。7912的真实本地资料命名核对、增量、筛选、下载、重放和刷新恢复均通过浏览器验收，模型请求为0；三端口页面无脚本异常或公网资源请求。详细运行检查及回滚容器见[部署摘要](runtime-summary.json)。

下一项是独立问句的实际任务验收：冻结未参与开发的问法，比较本地解析与一次理解＋fixed的目标/交付、调用量和失败处置。尚无新独立用户问题，不能把本轮自编题改称独立评测；也不以新增框架、无限补规则或重跑旧题刷分代替业务收益证据。
