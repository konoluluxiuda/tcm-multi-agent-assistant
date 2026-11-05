# 中草药知识检索与资料分析 Agent

**当前收尾范围（2026-11-04）：** 只完善单药字段、多药同字段、跨来源、分页关系查询和固定模板表格五类预设问题。暂停通用列顺序、更多理解策略及扩大模型评测。见[范围、13条走查与完成标准](docs/PRESET_QUESTION_SCOPE.md)；下方历史功能描述不扩大本轮开发范围。


**真实反馈检查（2026-11-05）：** 已完成15次真实调用；首次四个检查点3/4符合预期，修复后失败单案通过。反馈可能增加误澄清，尚未证明优于本地规则，因此7934保持模型关闭。见[证据与限制](audits/conversation_scope_v1/README.md)。

**离线收尾（2026-11-05）：** CI测试依赖已补齐。干净Python 3.11环境完成1862项全仓覆盖；两个旧记忆断言模块更新后14项复测通过，无剩余失败。首轮失败保留，未宣称远程CI已通过，见[验证汇总](audits/conversation_scope_v1/ci_verification.json)。

**连续对话修复（2026-11-05）：** 7934已补充未识别请求记忆、对象指代和两药性味查询；用户四句连续提问及原13条清单复验通过，140项受影响测试通过。W理解与复核已接入会话上下文，部署仍关闭模型。见[记录与边界](audits/conversation_scope_v1/README.md)。

**预设范围交付（2026-11-05）：** 五类预设问题的10条业务例子和3条边界已在隔离源码入口通过，外部模型调用0次；修复多药/跨来源分流、分页关系和发布版来源比对，受影响123项回归通过。默认策略未切换；7934已更新并通过同一13项部署验收，原会话保留；实现、测试与公开证据整理为本地提交，不推送。见[走查与修复](audits/preset_questions_v1/README.md)。

**上一阶段收尾：** live_v2共118次请求均HTTP200，119组合有终态、1组合保留中断；此前未运行30道业务题R/G/V为2/8/6题通过，6边界题全部通过，未证明V优于G。7934隔离镜像已验收。见[历史结果与限制](audits/requirements_completion_v1/README.md)。

以下按日期保留各轮历史记录，旧分数不改写，旧“待接入”描述以最新进展为准。

**最新修复（2026-11-01）：** 已修反例门禁失败却返回0，以及功效查询被属性查询替代、混合请求漏掉功效字段的问题。新门禁17题按结果/拒绝码/阶段核对，关闭守卫的3个失败保留为负对照；此前“全仓CI通过”的错误记录已更正。完整验证与部署状态见[本轮验收](audits/understanding_gate_fix_v1/README.md)。能力矩阵和统一契约执行入口仍待接入。

**当前改造（2026-11-01）：** 已加入 `unified_v2`：普通问句统一模型理解，机械校验与资料任务语义复核分开，首轮加一次纠正或独立复核最多2次请求；不一致先澄清，未知结果不重试。资料执行仍为 fixed。离线反例和真实来源保存提案回放已验证，未新增真实模型成绩。见[实现、测试、运行与回滚](audits/understanding_v2/README.md)。下文模型成绩为各轮历史记录。

**最新验证（2026-11-01）：** 三道旧业务与两道边界真实回归全部通过；另冻六个业务新表述，纯本地0/6、一次理解＋fixed为5/6，包含可下载表格和增量v2。实际12次模型理解、69,123 token，原首测保留。剩余筛选题是宿主把“词表未识别”当成提案冲突，已在首测后改为明确约束校验；保存参数＋真实资料交付通过，20模块167项检查通过，未新增模型重测。不是独立用户准确率或多轮Agent收益证明，见[首测、原因、修复与限制](audits/query_generalization_v2/README.md)。

当前试用：[7921统一理解入口（最多2次请求）](http://127.0.0.1:7921/)；[7920纯本地对照](http://127.0.0.1:7920/)。两者已重启至同一源码，仍采用fixed执行；Docker未更新。

**提案校验修复（2026-11-01）：** 普通模型查询按目录候选与字面对象验证，补齐出处子句、齐全筛选、停止理由与第三人诊疗拒绝；本地诊断保留深层参数并串行化同进程事务。60模块529项检查、6个真实来源回归及双实例10次HTTP检查通过，新增外部模型调用0；旧0/8与4/8首测不改。7920/7921已更新，见[修复、验收与剩余限制](audits/proposal_validation_repairs_v1/README.md)。

**新问法首测（2026-11-01）：** 8个开发者新编业务场景，本地0/8、一次理解＋fixed为4/8；两药表格及增量用量通过实际文件校验。已定位名称边界、出处子句、齐全筛选和停止类Schema的宿主误拒，原始失败保留；本轮仅改评测器，未改业务或Prompt。见[首测、模型收益与下一大步](audits/query_generalization_v1/README.md)。这不是独立用户准确率或多轮Agent收益证明。

**本地查询补修（2026-11-01）：** “想知道连翘的药用部位”和“查看白术的药用部位、科名、性味、归经”已在7920/7921直接查询通过，性味同时保留药性与药味；普通查询补齐结构化失败原因及诊断事件。57模块502项受影响回归、双实例16次HTTP检查通过，新增模型调用0。见[原因、验收与范围](audits/local_query_repairs_v1/README.md)。

> **当前工作台默认固定执行，按需单次模型理解。** 支持中文/拼音名单、字段核对、缺失与差异标记、带出处XLSX和增量v2；原生Tool Calling、持久人工等待及多轮观察循环作为显式实验保留。见[当前运行说明](docs/RESEARCH_SELECTION_AGENT.md)。文献搜索已退役，历史台账仍可读取。

**最新大步（2026-11-01）：** 中文名候选520→561，补入丹参、连翘等41条有标题依据的关联；聊天与工作台共用新发布版本，支持单药多属性及具体失败原因。真实模型首批业务2/4，两个宿主误拒已用保存返回修复；追加定向改写1/2，另一路由误判已以零模型调用修复，原分数保留。当前7920本地、7921一次模型兜底，旧Docker镜像未更新。见[本轮验收与运行](audits/name_resolution_v1/README.md)。

请求诊断已补齐：同一编号关联模型参数、拒绝原因、队列和后台交付，支持会话内回查及本地CLI；诊断阶段未修改业务规则。见[诊断说明](docs/REQUEST_DIAGNOSTICS.md)、[具体原因与验收](audits/diagnostic_chain_v1/README.md)。此前本地7914镜像为`diagnostic-chain-v1`。

**2026-11-01第一版业务修复：** 多药成分/靶点完整动作、共同靶点专用路由及“哪些字段都有记录”筛选已实现，44模块345项回归与真实资料交付通过。独立试用`http://127.0.0.1:7920/`，本轮外部模型关闭，旧镜像未更新；丹参/连翘中文名关联仍缺失，真实模型未重测，旧低分保留。见[修复结果与限制](audits/repair_attempt_v1/README.md)。

面向中草药研究资料整理的大模型应用项目，整合ETCM结构化记录与药典文本，提供有出处的查询、比较、来源靶点分析和研究报告。

当前采用 **Python、FastAPI、LangGraph 1.x、SQLite、可选PostgreSQL、Qdrant和DeepSeek兼容接口**。默认本地预览关闭模型和联网工具。当前聊天是多步骤意图解析＋受控本地执行；没有默认开启向模型回传真实资料的自主科研循环。

**此前新问句首测：** 8道作者业务题R本地解析1/8、G一次模型理解＋fixed执行7/8；另4道控制零调用拦截。预选多轮A端到端0/2（一题Goal被拒未执行，另一题错误参数被拒），13次HTTP200、26,135 token，见[原记录](audits/research_acceptance_v1/README.md)。更早的[组合验收](audits/research_combined_v1/README.md)G/A各2/2与原R4 A0/2均保留，不外推为独立准确率。求职演示与证据见[三分钟演示](docs/PROJECT_DEMO.md)、[面试材料](docs/INTERVIEW_EVIDENCE.md)。

**随后统一入口与Harness已完成：** 聊天/工作台先本地完整解析，7912已启用未识别输入的一次模型兜底，查询不二次分类；7896/7914保留本地模式。新建/续改Schema和合格集合/数量约束已接入，零合格允许空表。28模块166项受影响离线检查通过，真实模型三项合成烟测通过（5请求、20,526 token），不改写上述真实资料旧分数。该轮本地镜像`tcm-research-chat:unified-harness-v1`，未上传远程；范围与代价见[验收](audits/unified_harness_v1/README.md)。

**Schema精简验收：** 忙碌429后同key可继续原工作区且不重复理解，API字段层级已澄清，服务源码与启动入口分别追踪版本。32模块206项受影响离线检查通过；四题合成开发对照中激进精简3/4未采用，保守版4/4，总token较同题完整基线少29.86%。该轮本地镜像为`tcm-research-chat:understanding-compact-v2`。原失败保留，不视为独立准确率，见[完整记录](audits/understanding_compact_v1/README.md)。

**此前用户原话回放：** 11场景12轮通过真实聊天接口验收。首轮业务本地8/10、兜底10/10；边界分别1/2、0/2，原失败保留。已修复无对象整理、个人治疗指令及无表格关键词的明确齐全筛选，三条原句双实例回归6/6；本轮9模块70项离线检查通过。该轮本地镜像`tcm-research-chat:user-chat-replay-v1`已更新，未远程上传。问法已参与开发，不是未见独立样本，见[回放、模型作用与失败处置](audits/user_chat_replay_v1/README.md)。

**外部上下文新题与干净复现：** 新鲜模型上下文生成12场景14轮，未按这些题调优，首测原样保留。10个业务场景本地1/10、单次理解兜底2/10，两个边界各2/2；两项续改因首轮合同不通过未执行，不能把未运行当通过。AI模拟不是真人独立验收。新镜像`external-clean-v1`在空状态下完成查询、交付、容器重建恢复和v3；21项受影响离线检查通过。当前主要缺口是中文名覆盖、跨工具交付和自由表格/筛选语义，不能声称开放任务已稳定闭环。见[首测与复现](audits/external_clean_v1/README.md)、[运行命令](docs/CLEAN_REPRODUCTION.md)。

**主线与阶段记录（保留各轮当时口径）：** [版本化数据与benchmark → 检索对照 → 带引用RAG → Agent整合部署](docs/DATA_RAG_BENCHMARK_PLAN.md)，状态见[统一大纲](docs/TCM_PROJECT_ROADMAP.md)。R1研究基线已交付：核验2076份原JSON，新建616条目/8665片段同hash词法索引，生成8665个未审核候选并冻结30题原始参考；34项离线及真实发布回读通过，见[R1验收](audits/data_rag_r1/README.md)和[构建/回查命令](docs/DATA_RELEASE_BENCHMARK.md)。R2已完成新8665条/512维向量、Qdrant与受限跨条目CLI对照：开发20/20、保留首测9/10（四方法相同），一项字段别名状态缺陷保留；58项离线检查通过。向量未显示增益，继续默认精确/BM25，见[R2结果与命令](docs/RAG_RETRIEVAL_COMPARISON.md)。**R3已完成受限摘录RAG：状态缺陷修复，4次真实请求、4/4受限生成＋4/4零调用控制、99项离线通过。** 模型整体收益未证，默认仍看原文；[引用资料页](http://127.0.0.1:7912/rag)仅点击整理才调用模型，见[使用与边界](docs/RAG_CITED_ANSWER.md)。**R4最初工程验收时，真实A未通过。** [候选核对任务](http://127.0.0.1:7912/dataops/research)支持限定候选、字段缺项、增量和宿主验证交付；新真实两题F按契约2/2，A0/2，P两次未交付（一次基础设施故障），不能证明模型收益。14模块118项与7912/7914浏览器通过；本地Docker7914运行最终镜像，未发布远程。执行上下文Harness V2已做离线修复（15模块127项，原8动作的4个重复动作被拦截），该轮镜像为 `tcm-research-chat:r4-question-v1`；随后已接通有界问句→Goal→任务→表格/v2，22模块174项与双实例浏览器通过。新8题理解规则4/8、模型方案6/8（含3题宿主零调用控制），实际5次请求/7,754 token，仍有多余澄清和擅加字段失败；事后守卫离线回放不改原成绩，模型理解默认关闭，见[问句验收](audits/research_question_v1/README.md)，见[R4使用与限制](docs/RESEARCH_SELECTION_AGENT.md)。

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

后续[V3真实模型兼容对照](audits/dataops_live_v3/README.md)F/P各3/3、A为1/3；反馈改进通过77项离线检查。最新[V4有界复测](audits/dataops_live_v4/README.md)实际33次HTTP200，F/P各3/3、A为2/3，多候选题仍因两次工具校验拒绝耗尽预算，未完成结束动作。Langfuse轨迹与评分已核对，原始失败保留。这组三道已知合成题未显示模型优于固定流程；常规整理继续F，随后转向单次LLM目标提案验证，不追加预算重跑兼容题。

[G目标理解对照](audits/dataops_goal_v1/README.md)随后已实现并运行：12道合成开发题，规则与单次模型各8/12；模型自然表达3/4、规范控制1/4，表明它能补一些改写，也会引入错误列。实际12次请求、无重试，46项离线检查及Langfuse回读通过。实验适配器尚未接入网页；G2独立调优暂缓，出处/来源与默认字段语义随R4真实任务处理，不阻塞新的数据/检索/RAG主线，见[运行和边界](docs/DATAOPS_GOAL_UNDERSTANDING.md)。

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

代码更新后需重启对应端口的旧进程，继续使用原`--state`目录。可访问`/api/runtime`检查`restart_required`，只看磁盘文件更新不代表运行服务已经更新。页面样式、图标和脚本现从本地加载，不依赖公网CDN。7896主入口及7912模拟模式的实际核验、直接多字段和唯一候选修复见[新用户走查记录](docs/NEW_USER_QA_20261030.md)。

预览和研究Compose现默认使用`var/mineru_retrieval_v3`：修复贮藏正文混入后续注释的问题，原文和旧索引保留，见[边界修复与复现](audits/storage_boundary_v1/README.md)。仅有旧数据时先按该说明生成新索引；`--pharmacopoeia-index var/mineru_retrieval_v2`用于显式复现旧版本，不能与新索引任务增量混用。

可选[本地Langfuse追踪](docs/LANGFUSE_OBSERVABILITY.md)：安装`requirements-observability.txt`，运行`.venv/bin/python -m scripts.setup_langfuse_local up`，再为预览命令增加`--with-langfuse`。控制台在`http://localhost:3037`；自动生成的本地登录信息位于被Git忽略的`var/langfuse_local/access.json`。仅接入DataOps，普通查询尚未统一追踪；不开此参数仍无追踪外发。用户已允许任务相关资料外发，当前默认关闭模型的行为保持由运行开关决定。

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
