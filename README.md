# 中草药资料问答系统

基于ETCM结构化资料与《中国药典》本地索引的问答应用。明确药材和查询字段后，读取对应来源，支持连续追问，并保留出处、缺失和歧义信息。

当前正式展示聚焦资料问答。固定模板表格列为候选功能；复杂研究工作台、向量检索实验和native Agent不作为当前产品交付承诺。详见[功能边界与保留清单](docs/FEATURE_STATUS.md)。这是本地开发与验证项目，不宣称已有商业用户、临床应用或生产规模运行。

## 当前支持

- 单药、多药的明确字段查询，例如性味、归经、药用部位、药典功效与用量。
- 按字段默认来源读取，明确指定来源优先；跨来源内容分别注明出处。
- 已给出药名后补充字段，或通过明确指代继续追问；不能确定对象时澄清。
- 成分、靶点来源记录的分页查询；当前页不等于全量结果。
- 会话保存、重复请求处理、失败提示和证据回查。

示例：

> 我想了解白术的信息
>
> 给出性味和剂量
>
> 查询黄芪的ETCM药用部位和药典功能与主治。

这些属于已有开发问法，不是独立泛化评测。名称映射和资料抽取不代表完成人工身份核验；缺项不以其他资料替代，不提供个体诊疗方案。

## 实际实现

Python、FastAPI、SQLite与现有LangGraph组件构成应用基础。明确模板可本地解析；可选模型理解生成类型化需求，宿主校验对象、字段和来源，再由固定工具执行。工具反馈可以在既定预算内纠正需求；原始资料与证据不会因展示整理而改写。

问句 → 会话对象范围 → 需求解析与校验 → 来源查询 → 有出处的结果 → 保存本轮状态。

历史上已开展BM25、BGE/Qdrant、RRF及多轮Agent对照。实验代码与结果保留，不能把它们写成当前聊天全部启用的技术栈。见[检索对照及限制](docs/RAG_RETRIEVAL_COMPARISON.md)、[架构与运行状态](docs/TCM_PROJECT_ROADMAP.md)。

## 本地运行

需要Python 3.11或以上，以及已构建的ETCM数据库、药典索引、PDF和对应资料发布目录。私有资料不随仓库分发；准备方式见[数据与检索](docs/README.md#数据与检索)。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-research-chat.txt
.venv/bin/python -m scripts.preview_local_tasks \
  --port 7896 --state var/qa_preview \
  --research-selection fixed \
  --rag-release var/data_releases/r1_names_20261101
```

访问 **http://127.0.0.1:7896/**。运行前确认端口空闲；发布目录应替换为实际准备好的版本。示例命令默认不启用模型，适合明确模板。已有7896试用实例使用W理解与查询后复核，其开关以运行状态为准。

开发者确需查看候选或实验入口时，可在启动进程前设置`SHOW_RESEARCH_EXTENSIONS=1`；默认隐藏。此开关只控制首页导航和能力介绍，不授权模型调用、不关闭后台API，也不删除旧任务或文件。

## 验证与证据

使用现有离线检查器：

```bash
.venv/bin/python -m pip install -r requirements-ci.txt
.venv/bin/python -m scripts.run_offline_ci --out var/qa_offline_check
```

离线检查使用模型替身与合成数据；测试通过数不等于真实问答准确率。已见问法回归、真实模型实验和用户使用验收分别记录，不叠加成统一分数。

- [字段追问修复](audits/field_followup_v1/README.md)与[来源规则统一](audits/field_sources_v1/README.md)
- [答案展示、公式显示与浏览器检查](audits/answer_compaction_v1/README.md)
- [请求恢复与幂等验收](audits/chat_request_recovery_v1/README.md)
- [本次功能范围收敛](audits/qa_product_scope_v1/README.md)

记录各自对应当时源码。历史CI、旧端口和旧部署不能代替当前工作区验收。

## 文档与历史

[使用说明](docs/NEW_USER_GUIDE.md) · [功能状态](docs/FEATURE_STATUS.md) · [文档导航](docs/README.md) · [架构与进度](docs/TCM_PROJECT_ROADMAP.md)

旧README的阶段说明、运行方法与成绩完整保留在[历史快照](docs/history/README_BEFORE_QA_SCOPE_20261106.md)。未通过或中断的实验不因本轮整理改判成功。

## 来源与许可证

本项目基于Souvik Majumder的[Multi-Agent-Medical-Assistant](https://github.com/souvikmajumder26/Multi-Agent-Medical-Assistant)改造，保留原作者署名与[Apache-2.0许可证](LICENSE)。旧版影像/语音/联网组件暂留代码、默认关闭，见[可选功能](docs/OPTIONAL_FEATURES.md)和[原医疗组件资料来源](assets/extra_details.md)。代码许可证不自动授权重新分发PDF、ETCM记录或模型权重。
