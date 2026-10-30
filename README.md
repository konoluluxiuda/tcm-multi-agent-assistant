# TCM DataOps Agent

> **D1已接入：批量资料核对与带来源表格交付。** 支持目录筛选、中文/拼音名称名单、差异与缺失标记、工作区回看和XLSX下载，见[D1运行与验收](docs/DATAOPS_D1_DELIVERY.md)。保留0模型调用的固定流程F；D2已加入原生Tool Calling适配器、持久人工等待和增量v2，网页当前使用合成模型替身，见[D2使用与边界](docs/DATAOPS_D2_DELIVERY.md)。文献搜索已退役，历史台账仍可读取。

面向中草药研究资料整理的大模型应用项目，整合ETCM结构化记录与药典文本，提供有出处的查询、比较、来源靶点分析和研究报告。

当前采用 **Python、FastAPI、LangGraph 1.x、SQLite、可选PostgreSQL、Qdrant和DeepSeek兼容接口**。默认本地预览关闭模型和联网工具。当前聊天是多步骤意图解析＋受控本地执行；没有默认开启向模型回传真实资料的自主科研循环。

## 当前能做什么

- 批量核对最多50条药材来源，读取药典候选字段，保留差异/缺失/歧义并生成四张工作表；任务与来源可回看。
- 可选DataOps观察循环、候选人工消歧、跨进程恢复和增量v2差异表；外部模型默认关闭。
- DataOps新增有界问句入口，可直接提出整理要求或增加字段；未识别要求不入队，见[入口验收](audits/dataops_question_v1/README.md)。
- 查询药材属性、成分、来源声明的靶点及可追溯关联。
- 查询药典字段与原页出处，比较单药/两药资料并导出报告。
- 计算药材靶点相似度、按明确疾病来源的靶点集合排列药材候选。
- 一句话联合查询药典字段、ETCM属性与两药共同靶点，内部读取计入统一预算；大结果完整保存为私有证据，页面摘要、按需分页及下载，来源不唯一时才选择。
- 保存会话、研究项目和运行结果；新聊天支持后台任务、SSE、显式取消及受控恢复，独立报告保留可选PostgreSQL checkpoint。
- 提供可选本地语义记忆、混合检索/重排及两个资料工具的MCP stdio接入。

来源记录数不等于去重药材种数；数据库关联和集合相似度不代表作用机制、疗效或治疗推荐。药典提取与ETCM/药典对象对应尚未全部人工核验。

**当前边界：** 已改进问句契约和资料路由；同一批24题开发回归中，资料任务从首轮8/18变为固定评分器17/18，另1题为“科名/科”标签判定差异，原始记录已另行核对。原首轮保留，不能当独立准确率；见[本轮改进与验收](docs/QUESTION_CONTRACT_MILESTONE.md)和[业务闭环核验](docs/BUSINESS_CLOSURE_REVIEW.md)。

DataOps现已增加[30题契约评测与50条规模报告](docs/DATAOPS_D3_EVALUATION.md)及[3–5分钟演示](docs/DATAOPS_DEMO.md)。三题真实模型V2实际8次请求均HTTP200：固定F与一次规划P各通过3/3，动态A为0/3，工具动作中断原因尚未确认。原始失败保留，不宣称Agent优于固定流程。

## 本地运行

需要已构建的本地ETCM数据库、药典索引及源文件。没有这些资料时，请先阅读[数据入口](docs/README.md#数据与检索)，不要把空目录当作成功部署。

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-research-chat.txt
.venv/bin/python -m scripts.preview_local_tasks \
  --port 7896 --state var/candidate_a_chat_preview \
  --diseases var/disease_catalog_v1/build/diseases.sqlite3
```

打开 **http://127.0.0.1:7896/dataops** 使用新资料工作台；**http://127.0.0.1:7896/** 保留聊天。工作台中可输入“白术”“黄芪”各一行，勾选字段生成表格。既有多步骤研究仍先排队显示进度，见[后台执行说明](docs/RESEARCH_PLAN_LIFECYCLE.md)。默认不发送问题或资料给模型；启动脚本检查本地资料和openpyxl依赖。项目已有`.venv`可继续使用，缺依赖时先执行上述安装命令。

若要体验D2，在启动参数中增加`--dataops-synthetic`；仍不调用外部模型。页面新增“直接说明要整理什么”：例如“整理白术和黄芪的功效、注意，保留出处并生成表格”；打开结果后输入“增加贮藏，保留旧字段”，点击“修改当前结果”。聊天首页也可直接输入这类“整理并生成表格”的要求，结果在聊天内展示并可下载；再问“增加贮藏，保留旧字段”可续改，见[聊天接入验收](audits/dataops_chat_v1/README.md)。它使用有界本地语法，不是通用LLM理解；原手动表单继续可用。支持后台进度、必要选择和增量修改。合成候选演示及完整说明见[D2交付](docs/DATAOPS_D2_DELIVERY.md)。

聊天可输入：

> 当归的靶点画像，川芎的靶点画像
>
> 当归的靶点画像，查找与川芎靶点最相似的药材，前5个
>
> 比较当归与川芎的药典功效和用量，核对全部共同靶点，保留双方原始记录的位置

普通使用见[使用说明与示例](docs/NEW_USER_GUIDE.md)，多步骤及跨来源查询见[报告工具统一](docs/RESEARCH_REPORT_TOOLS.md)，完整报告及故障演示见[演示指南](docs/PROJECT_DEMO.md)。复杂问法的单次模型意图提案由既有开关显式启用，仍只发送当前问句与静态定义；真实资料的多轮外发不包含在此范围内。

## 验证与部署

```bash
.venv/bin/python -m pip install -r requirements-ci.txt
.venv/bin/python -m scripts.run_offline_ci --out var/ci
```

离线回归使用合成资料和模型替身，跳过、断言失败或联网尝试都不能视为通过。离线测试数量不代表真实模型业务准确率。D1完整回归147模块1243项通过；其后范围修复复测16项通过，见[验收摘要](audits/dataops_d1_v1/README.md)。旧回归记录保留，不叠加成绩。业务首轮结果见[闭环审计](audits/business_closure_v1/README.md)，后台执行证据见[生命周期审计](audits/research_plan_lifecycle_v1/README.md)，仓库整理后的检查见[清理记录](docs/PROJECT_CLEANUP_20261029.md)。

研究版容器入口为`Dockerfile.research`与`compose.research.yaml`；资料使用只读挂载，运行状态独立保存。框架升级后的新镜像尚未构建发布，旧Docker镜像不包含最新本地改动。见[部署说明](docs/RESEARCH_DOCKER.md)与[升级/回滚记录](docs/LANGGRAPH_V1_UPGRADE.md)。

## 项目结构

| 位置 | 用途 |
|---|---|
| `app.py`、`templates/` | FastAPI与聊天/研究页面 |
| `tcm/` | 资料查询、工具循环、证据校验、队列、报告及记忆 |
| `agents/` | 主LangGraph、会话存储及保留的可选医疗组件 |
| `scripts/`、`tests/`、`eval/` | 数据构建、验证、故障演练与评测 |
| `experiments/architecture_candidates/` | 隔离A/B/C比较与待接入的证据外置实验；不参与默认生产路径 |
| `docs/`、`audits/` | 使用/设计说明及原始验收证据 |
| `data/`、`var/` | 本地源资料、派生索引和运行状态；不作为公开资料分发 |

[文档导航](docs/README.md) · [统一大纲与真实进度](docs/TCM_PROJECT_ROADMAP.md) · [架构选择](docs/ARCHITECTURE_DECISION_REVIEW.md) · [面试证据](docs/INTERVIEW_EVIDENCE.md)

## 来源与许可证

本项目基于Souvik Majumder的[Multi-Agent-Medical-Assistant](https://github.com/souvikmajumder26/Multi-Agent-Medical-Assistant)改造，保留原作者署名与[Apache-2.0许可证](LICENSE)。旧版影像/语音/联网功能默认关闭，使用与模型来源见[可选功能](docs/OPTIONAL_FEATURES.md)和[原医疗组件资料来源](assets/extra_details.md)。本项目代码许可证不自动授权重新分发用户提供的PDF、ETCM记录或模型权重。
