<div align="center">

本地启动：`python -m scripts.preview_local_tasks --port 7877`，访问 **http://127.0.0.1:7877/**。明确的双药共同靶点查询直接显示结果、双方原始行和出处，不再要求清单或计划确认；当前目录只有一份候选来源时自动使用并展示来源，多来源时才选择。独立研究任务页仍保留。无需模型或Tavily密钥；见[启动说明](docs/PROJECT_DEMO.md#启动与复现)和[本次简化](docs/LOCAL_TASK_REQUEST.md#明确双药查询直接执行2026-10-22)。

回答默认显示简短结论、结果表格或药典正文；原始记录、来源哈希和技术说明按需展开，缺失/失败状态仍直接提示。见[展示调整](docs/LOCAL_TASK_REQUEST.md#回答展示精简2026-10-22)。

数据库介绍已支持：“分别介绍两个数据”“介绍ETCM”“ETCM和药典有什么区别”，无需药材名。7877源码聊天还支持“白术的靶点画像”“与白术靶点最相似的药材，前5个”，见[相似度使用说明](docs/TARGET_SIMILARITY.md)。疾病JSON审计和来源靶点索引的本地构建/查询见[疾病索引说明](docs/DISEASE_GENE_CATALOG.md)；疾病聊天与候选药材来源排序现已接入，启动加 `--diseases var/disease_catalog_v1/build/diseases.sqlite3`，见[示例与限制](docs/DISEASE_CANDIDATE_ANALYSIS.md)；不是新靶点预测。当前阶段与后续工作见[实施方案](docs/RESEARCH_ANALYTICS_PLAN.md)。

可直接问“我需要查看白术的相关属性”。已接入[一次模型意图兜底](docs/INTENT_ROUTING.md)：用户已授权当前7877仅外发未识别的当前问句，最多一次模型调用，提取对象/方向/字段后本地查询，不发送资料或历史。实际验证“资料卡”“调出档案”均可返回白术属性；原句走规则、零模型调用。启用命令：`python -m scripts.preview_local_tasks --port 7877 --with-intent-model`；不加选项则关闭模型。

最新逐题验收（2026-10-22）：先模拟16个聊天及任务流程，修复刷新丢失核对按钮、UniProt交集未进入清单两处阻塞，再执行794项完整离线回归，全部通过。16场景中11项完成本地范围，5项缺失/未支持要求或边界未完整完成；来源和请求仍须用户核对，不代表自动理解全部语义。另18项实际进程检查通过，外部模型调用0；见[逐题结果](docs/PROJECT_DEMO.md#逐题模拟与随后全量回归2026-10-22)。

聊天入口修复（2026-10-22）：聊天输入“我在整理当归与川芎，想核对共同靶点，并保留双方原始记录的位置”，现提供“核对请求清单”按钮，点击携带完整原句进入本地任务。原任务页验收未覆盖聊天入口，实际403失败已保留。790项离线及15项实际浏览器检查通过，外部模型调用0；见[修复范围](docs/LOCAL_TASK_REQUEST.md#聊天入口衔接修复2026-10-22)。

当前演示与业务检查（2026-10-22）：[五分钟流程、实际架构和项目讲解](docs/PROJECT_DEMO.md)。固定`f2f8114`运行6条开发设计任务，结果为3完成、2部分完成、1等待补充；5份提交清单中4份需模拟人工修订，16条选定原始行核对通过。来源核查闭环可演示，自动完整语义理解和独立用户验收仍未完成；本轮不改应用规则或增加模型调用。

当前主线已调整：[核心任务控制对照与结论](docs/CORE_WORKFLOW_COMPARISON.md)。文献扩展冻结；4个已知合成场景中，确定性、一次策略规划和有限循环均满足预期，模型调用分别0/4/15次，未观察到额外完成收益。现有明确任务继续默认确定性工具，后续优先真实用户任务与演示，不继续堆功能。

当前问句入口：[真实ETCM问句与清单核对](docs/LOCAL_TASK_REQUEST.md)。7877任务页可输入“白术和黄芪有哪些共同靶点？”，核对/修订清单后确认两味药的计划和来源，再核对选定靶点原始记录。未执行的其他要求保留为未完成；输入与资料均不外发，不具备完整自动语义提取能力。87模块782项离线及20项真实浏览器检查通过，含实际重启恢复；Docker未更新。

当前范围控制：[请求覆盖契约](docs/REQUEST_COVERAGE.md)。新合成计划及ETCM问句任务保留用户声明的全部对象和交付，未覆盖部分须明确确认才允许执行子范围；子任务完成不会将整份清单标成完成。当前核验显式清单，尚不保证自由问句的完整需求提取。

当前规划验证：[追加12题的冻结验收](docs/TASK_PLANNER_SYNTHETIC.md#追加验收冻结代码后的12题2026-10-21)。模型计划符合10/12、规则4/12，但只有4项完整执行完成；三对象与额外交付仍有遗漏。保留默认入口，下一项优先补请求覆盖契约，不以合法的子任务替代原请求。此前8题结果保留，未开放网页任意问题的模型外发。

当前计划增量：[有界计划与持久澄清（合成验证）](docs/TASK_PLAN_CLARIFICATION.md)及[ETCM本地限定计划](docs/LOCAL_TASK_REQUEST.md)。计划、清单和来源/靶点选择可持久恢复；真实ETCM只执行两味药的来源关系，不支持自由问句模型自主规划。

当前工程增量：[ETCM任务工具注册与返回契约](docs/TASK_TOOL_REGISTRY.md)。三个只读操作使用固定入口、严格参数与类型化结果，明确区分空结果、资料故障和快照变化；真实ETCM仍由确定性控制器执行，尚未接模型规划。

当前源码增量：[本地任务完成范围](docs/TASK_COMPLETION_SCOPE.md)。新建ETCM查询不再因缺少文献一律标为未完成；来源不确定仍保留缺口，研究证据核验始终单列为未完成。旧任务及历史报告保留原口径。

当前开发预览：**http://127.0.0.1:7877/research-tasks**。新增[来源文献ID与对象核验契约](docs/TASK_LITERATURE.md)，705项离线及16项本地页面检查通过；本轮不联网读取论文、不调用模型。Docker Desktop的WSL集成当前不可用，7876未监听，新镜像未构建/部署；以下7876记录为上次成功交付。7877使用独立会话与任务库。

新用户请先阅读[使用说明与模拟测试问题](docs/NEW_USER_GUIDE.md)。

上次Docker入口：**http://127.0.0.1:7876/**。最后成功装入[真实ETCM任务及此前修复](docs/RESEARCH_TASK_ETCM.md)工作树镜像，保留原会话卷；已提交基线为c4de8bc，见[交付记录](docs/releases/RESEARCH_LOCAL_C4DE8BC.md)。未上传远程。

2026-10-21进一步接入[真实ETCM本地来源关系任务](docs/RESEARCH_TASK_ETCM.md)：确认两份来源、分页核对共同UniProt和双方原JSON，研究文献证据明确待补，不调用外部模型。695项离线及25项实际页面检查通过，仅本地Docker更新。

2026-10-21新增[研究任务页面](docs/RESEARCH_TASK_WEB.md)：http://127.0.0.1:7876/research-tasks ，支持来源/靶点确认、SQLite持久化、重启恢复、动作记录及证据报告。初版页面仅合成资料；当前新增上述ETCM本地模式，两种模式均无模型调用。[CLI原生tool calling验证](docs/BOUNDED_RESEARCH_TASK.md)单独保留，不混算业务效果。

本地候选更新（2026-10-20）：新功能镜像已在 http://127.0.0.1:7876/ 隔离验收，原7873及远程标签未切换；[部署修复、603项回归与20项容器检查](docs/releases/RESEARCH_LOCAL_20261020.md)。

源码增量（2026-10-20）：新增[研究收藏](docs/RESEARCH_BOOKMARKS.md)，支持药典及已选来源关联查询的手动保存、个人笔记和重启回看；同一浏览器访问，清空聊天保留，尚未进入已发布Docker镜像。
 
![logo](https://github.com/konoluluxiuda/tcm-multi-agent-assistant/blob/main/assets/logo_rounded.png)

<h1 align="center"><strong>⚕️ Multi-Agent-Medical-Assistant :<h6 align="center">AI-powered multi-agentic system for medical diagnosis and assistance</h6></strong></h1>

<!-- ![PyTorch - Version](https://img.shields.io/badge/PYTORCH-2.0+-red?style=for-the-badge&logo=pytorch) -->
![Python - Version](https://img.shields.io/badge/PYTHON-3.11+-blue?style=for-the-badge&logo=python&logoColor=white)
![LangGraph - Version](https://img.shields.io/badge/LangGraph-0.3+-teal?style=for-the-badge&logo=langgraph)
![LangChain - Version](https://img.shields.io/badge/LangChain-0.3+-teal?style=for-the-badge&logo=langchain)
![Qdrant Client - Version](https://img.shields.io/badge/Qdrant-1.13+-red?style=for-the-badge&logo=qdrant)
![Pydantic - Version](https://img.shields.io/badge/Pydantic-2.10+-red?style=for-the-badge&logo=pydantic)
![FastAPI - Version](https://img.shields.io/badge/FastAPI-0.115+-teal?style=for-the-badge&logo=fastapi)
![Docling - Version](https://img.shields.io/badge/Docling-3.1+-orange?style=for-the-badge&logo=docling)
[![Generic badge](https://img.shields.io/badge/License-Apache-<COLOR>.svg?style=for-the-badge)](https://github.com/konoluluxiuda/tcm-multi-agent-assistant/blob/main/LICENSE) 
[![GitHub Issues](https://img.shields.io/github/issues/konoluluxiuda/tcm-multi-agent-assistant.svg?style=for-the-badge)](https://github.com/konoluluxiuda/tcm-multi-agent-assistant/issues)
![Contributions welcome](https://img.shields.io/badge/contributions-welcome-orange.svg?style=for-the-badge)

</div>

## 中草药研究版本

本地实用性首轮12题中8题完成当前资料范围任务、2题澄清、2题关系改述/追问未支持。已补有限改述和SQLite持久关系焦点，同题回归10题完成、2题澄清；原失败保留，不作为独立或医学准确率。选择成分来源后可问“这些成分对应哪些靶点”，刷新/重启可恢复来源与页码，原文不入记忆。详见[逐题结果与修复边界](docs/LLM_APPLICATION_EXPERIMENTS.md#关系改述与持久焦点修复2026-10-20)。

源码中的LLM增量已完成5题真实开发测试及原两道失败题的受限重测，提供模型规划、原文支持绑定和药典摘录回答；该增量尚未发布到下述镜像。先看[5分钟演示与项目讲解](docs/PROJECT_DEMO.md)及[实验结果与限制](docs/LLM_APPLICATION_EXPERIMENTS.md)。已有本地记录时可生成不联网、不调用模型的交互回放：

```bash
python -m scripts.replay_evidence_demo --out var/evidence_assistant_v1/demo/replay-new.html
```

当前源码可用 `python -m scripts.preview_evidence --port 7875 --with-local-research --with-local-herbs` 启动独立预览，打开 http://127.0.0.1:7875/ 。`/research 白术的功效`直接查询本地药典，`BaiZhu的药用部位`查询ETCM，均不调用模型；需已有本地数据库、药典索引及PDF。ETCM默认固定`local-2026-10-10`版本，`/ready/herbs`可检查就绪。AI路径固定使用合成资料，未匹配的问题自动进入模型规划；“AI整理回答”可强制选择该模式（统计除外）。省略对应选项可关闭真实工具。需要安装研究聊天依赖并配置聊天模型，模型路径会发起实际调用。预览使用独立会话库，不更新旧Docker；详见上方演示说明。

不勾选AI时，还可输入“比较白术和黄芪的功效与用量”，按字段并列两味药的原文及出处。最多两味、两字段；缺失或重复字段保留提示，不推断疗效优劣或替代关系。

ETCM中文对照及来源关联已在当前源码交付：2076条双语记录、584583条来源关联，520条药材有未核验的中文药名候选。启动命令加`--etcm-enrichment var/etcm_enrichment_v1/data/enrichment.sqlite3`后，输入`/herb 白术`选择来源，可分页查看成分、靶点、方剂等记录；机器译文默认折叠，原值和出处保留。全程本地处理，未更新Docker；详见[数据位置、复现与限制](docs/ETCM_LOCALIZATION_AND_LINKS.md)。

同一预览现可直接问“白术有哪些成分”“白术和黄芪有哪些共同靶点”：先选择中文候选对应的ETCM来源，再分页展示关联行或相同UniProt标识及双方出处，不调用模型。共同靶点不代表已验证机制或药效，见[查询范围与使用方法](docs/ETCM_LOCALIZATION_AND_LINKS.md#自然语言关联查询)。

启动再加`--etcm-source /home/zry/workspace/HDCTI/dataset/ETCM2.0`，可从成分列表打开本地文件详情：核对内部名称、分子式及分子量后显示来源CAS/PubChem、SHA和JSON位置。缺少直接成分—靶点关系时明确提示，不把药材靶点归给每个成分；详见[成分详情与数据缺口](docs/ETCM_LOCALIZATION_AND_LINKS.md#成分文件标识与直接靶点记录)。

当前研究镜像：`8n1eld/tcm-research-assistant:d0d3bc3`，约408 MB，支持数据概览、药材与药典、现代文献三个页签，以及受限自然语言查询、原页证据、组合清单和SQLite持久会话。本机入口为 http://127.0.0.1:7873/ 。镜像已发布并完成远程CI、摘要和拉取复验；运行仍需本地只读资料挂载，详见[新版发布与部署](docs/releases/RESEARCH_D0D3BC3.md)、[工作区说明](docs/RESEARCH_WORKSPACES.md)和[建设大纲](docs/TCM_PROJECT_ROADMAP.md)。页签共用一个任务，药典与文献对象对应关系尚待核验。

源码7875预览另支持“CYP24A1有哪些成分”“P04278有哪些成分”，只读查询核对后的211个靶点、34924条来源成分行，保留分页和出处。启用需增加`--etcm-targets var/etcm_target_links_v1/targets.sqlite3`，完整命令与范围见[靶点→成分查询](docs/ETCM_LOCALIZATION_AND_LINKS.md#靶点成分聊天查询2026-10-20)。未覆盖不表示没有关系，未证明实验结合；此功能未进入上述Docker镜像。

当前预览还支持“白术的成分关联哪些靶点”：选择来源后，按成分名称、分子式、分子量三字段连接双方来源行，显示候选路径及覆盖缺口，未核准化学身份或作用机制。使用包含组合索引的`--etcm-targets var/etcm_target_links_v1/targets-paths.sqlite3`即可兼容上述查询，完整启动命令见[候选路径说明](docs/ETCM_LOCALIZATION_AND_LINKS.md#药材成分靶点候选路径2026-10-20)。

本机后续增量：7873已装入 `tcm-research-chat:bd40e73`，提供[对象原文线索与可选用户标注](docs/RESEARCH_OBJECT_REVIEW.md)。新增量尚未push或发布Hub，不自动核验实体一致。

下文保留原医疗项目的介绍和完整部署说明；精简研究版本请使用上面的专用部署文档。

----

> [!IMPORTANT]  
> 📋 Version Updates from v2.0 to v2.1 and further:
> 1. **Document Processing Upgrade**: Unstructured.io has been replaced with Docling for document parsing and extraction of text, tables, and images to be embedded.
> 2. **Enhanced RAG References**: Links to source documents and reference images present in reranked retrieved chunks stored in local storage are added to the bottom of the RAG responses.
>
> To use Unstructured.io based solution, refer release - [v2.0](https://github.com/souvikmajumder26/Multi-Agent-Medical-Assistant/tree/v2.0).
 
## 📚 Table of Contents
- [Overview](#overview)
- [Demo](#demo)
- [Technical Flow Chart](#technical-flowchart)
- [Key Features](#key-features)
- [Tech Stack](#technology-stack)
- [Installation and Setup](#installation-setup)
  - [Using Docker](#docker-setup)
  - [Manual Installation](#manual-setup)
- [Usage](#usage)
- [Evaluation](#evaluation)
- [Contributions](#contributions)
- [License](#license)
- [Citing](#citing)
- [Contact](#contact)

----

## 📌 Overview <a name="overview"></a>

The **Multi-Agent Medical Assistant** is an **AI-powered chatbot** designed to assist with **medical diagnosis, research, and patient interactions**.  

🚀 **Powered by Multi-Agent Intelligence**, this system integrates:  
- **🤖 Large Language Models (LLMs)**  
- **🖼️ Computer Vision Models** for medical imaging analysis  
- **📚 Retrieval-Augmented Generation (RAG)** leveraging vector databases  
- **🌐 Real-time Web Search** for up-to-date medical insights  
- **👨‍⚕️ Human-in-the-Loop Validation** to verify AI-based medical image diagnoses  

### **What You’ll Learn from This Project** 📖  
🔹 **👨‍💻 Multi-Agent Orchestration** with structured graph workflows  
🔹 **🔍 Advanced RAG Techniques** – hybrid retrieval, semantic chunking, and vector search  
🔹 **⚡ Confidence-Based Routing** & **Agent-to-Agent Handoff**  
🔹 **🔒 Scalable, Production-Ready AI with Modularized Code & Robust Exception Handling**  

📂 **For learners**: Check out [`agents/README.md`](agents/README.md) for a **detailed breakdown** of the agentic workflow! 🎯  

<!-- The **Multi-Agent Medical Assistant** is an advanced AI-powered chatbot system designed to assist in medical diagnosis, research, and patient interactions.

Using a **Multi-Agentic framework**, this assistant integrates **Large Language Models, Computer Vision Models, Retrieval Augmented Generation leveraging Vector Database**, and **Web Search** to provide **accurate**, **reliable**, and **up-to-date** medical insights.

This project serves as a **comprehensive resource** for learning and implementing **GenAI with multi-agent orchestration**. It demonstrates **advanced Retrieval-Augmented Generation (RAG)**, integrating **vector search with hybrid retrieval techniques**, **efficient chunking strategies respecting document semantic boundaries**, **confidence-based routing**, **agent-to-agent handoff**, **real-time web search capabilities**, **human-in-the-loop validation** and more. The system incorporates **specialized AI agents** for medical reasoning, diagnosis, and research retrieval, all working seamlessly through a structured graph workflow. Developers will gain insights into **modular agentic AI solution design, and robust exception handling** — ensuring **scalability and production readiness**.

For learners: Refer `agents/README.md` for detailed explanation of the agentic workflow. -->

---

## 💫 Demo <a name="demo"></a>


https://github.com/user-attachments/assets/d27d4a2e-1c7d-45e2-bbc5-b3d95ccd5b35


If you like what you see and would want to support the project's developer, you can <a href="https://www.buymeacoffee.com/souvikmajumder" target="_blank"><img src="https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png" alt="Buy Me A Coffee" style="height: 41px !important;width: 174px !important;box-shadow: 0px 3px 2px 0px rgba(190, 190, 190, 0.5) !important;-webkit-box-shadow: 0px 3px 2px 0px rgba(190, 190, 190, 0.5) !important;" ></a> ! :)

📂 **For an even more detailed demo video**: Check out [`Multi-Agent-Medical-Assistant-v1.9`](assets/Multi-Agent-Medical-Assistant-v1.9_Compressed.mp4). 📽️

---

## 🛡️ Technical Flow Chart  <a name="technical-flowchart"></a>

![Technical Flow Chart](assets/final_medical_assistant_flowchart_light_rounded.png)

---

<!-- ## 🌟 Key Features  <a name="key-features"></a>
✅ **Multi-Agent System** – Separate agents handle different tasks (diagnosis, retrieval, reasoning, etc.).  
✅ **RAG-based Retrieval** – Uses Qdrant for vector search & hybrid retrieval techniques.  
✅ **Medical Image Analysis** – Supports **brain tumor segmentation, chest X-ray disease detection, and skin lesion classification**.  
✅ **Web Search Agent** – Fetches the latest medical research when required.  
✅ **Confidence Score Check** – Ensures high accuracy with log probability-based verification.  
✅ **Speech-to-Text & Text-to-Speech** – Uses **Eleven Labs API** for voice interactions.  
✅ **Human-in-the-Loop Verification** – Medical professionals validate the AI’s results before final output.  
✅ **Intuitive UI** – Built for seamless user experience.  

---

## 🛠️ Tech Stack  <a name="tech-stack"></a>
🔹 **Backend**: FastAPI 🚀  
🔹 **Multi-Agent Orchestration**: LangGraph + LangChain 🤖  
🔹 **Vector Database**: Qdrant (for retrieval-augmented generation) 🔍  
🔹 **Medical Image Analysis**: Computer vision models (Brain Tumor - Semantic Segmentation, Chest X-ray - Object Detection, Skin Lesion - Classification) 🏥  
🔹 **Speech Processing**: Eleven Labs API 🎙️  
🔹 **UI**: HTML, CSS, JS 🌐  
🔹 **Deployment**: Docker 🛠️   -->

## ✨ Key Features  <a name="key-features"></a>

- 🤖 **Multi-Agent Architecture** : Specialized agents working in harmony to handle diagnosis, information retrieval, reasoning, and more

- 🔍 **Advanced Agentic RAG Retrieval System** :

  - Docling based parsing to extract text, tables, and images from PDFs.
  - Embedding markdown formatted text, tables and LLM based image summaries.
  - LLM based semantic chunking with structural boundary awareness.
  - LLM based query expansion with related medical domain terms.
  - Qdrant hybrid search combining BM25 sparse keyword search along with dense embedding vector search.
  - HuggingFace Cross-Encoder based reranking of retrieved document chunks for accurate LLM reponses.
  - Input-output guardrails to ensure safe and relevant responses.
  - Links to source documents and images present in reference document chunks provided with reponse.
  - Confidence-based agent-to-agent handoff between RAG and Web Search to prevent hallucinations.

- 🏥 **Medical Imaging Analysis**  
  - Brain Tumor Detection (TBD)
  - Chest X-ray Disease Classification
  - Skin Lesion Segmentation

- 🌐 **Real-time Research Integration** : Web search agent that retrieves the latest medical research papers and findings

- 📊 **Confidence-Based Verification** : Log probability analysis ensures high accuracy in medical recommendations

- 🎙️ **Voice Interaction Capabilities** : Seamless speech-to-text and text-to-speech powered by Eleven Labs API

- 👩‍⚕️ **Expert Oversight System** : Human-in-the-loop verification by medical professionals before finalizing outputs

- ⚔️ **Input & Output Guardrails** : Ensures safe, unbiased, and reliable medical responses while filtering out harmful or misleading content

- 💻 **Intuitive User Interface** : Designed for healthcare professionals with minimal technical expertise

> [!NOTE]  
> Upcoming features:
> 1. Brain Tumor Medical Computer Vision model integration.
> 2. Open to suggestions and contributions.

---

## 🛠️ Technology Stack  <a name="technology-stack"></a>

| Component | Technologies |
|-----------|-------------|
| 🔹 **Backend Framework** | FastAPI |
| 🔹 **Agent Orchestration** | LangGraph |
| 🔹 **Document Parsing** | Docling |
| 🔹 **Knowledge Storage** | Qdrant Vector Database |
| 🔹 **Medical Imaging** | Computer Vision Models |
| | • Brain Tumor: Object Detection (PyTorch) |
| | • Chest X-ray: Image Classification (PyTorch) |
| | • Skin Lesion: Semantic Segmentation (PyTorch) |
| 🔹 **Guardrails** | LangChain |
| 🔹 **Speech Processing** | Eleven Labs API |
| 🔹 **Frontend** | HTML, CSS, JavaScript |
| 🔹 **Deployment** | Docker, GitHub Actions CI/CD |

---

## 🚀 Installation & Setup  <a name="installation-setup"></a>

## 📌 Option 1: Using Docker  <a name="docker-setup"></a>

### Prerequisites:

- [Docker](https://docs.docker.com/get-docker/) installed on your system
- API keys for the required services

### 1️⃣ Clone the Repository
```bash
git clone https://github.com/konoluluxiuda/tcm-multi-agent-assistant.git
cd tcm-multi-agent-assistant
```

### 2️⃣ Create Environment File
- Create a `.env` file in the root directory and add the following API keys:

> [!NOTE]  
> Models are constructed by the helpers `create_chat_model()` and `create_embedding_model()` in [`config.py`](config.py).
> Chat and embedding use any **OpenAI-compatible** endpoint — this repo is developed against **DeepSeek** (chat) and **Aliyun Bailian / DashScope** (embeddings). Point `CHAT_BASE_URL` / `EMBEDDING_BASE_URL` at any compatible provider.
> Image analysis agents use a separate `VISION_*` endpoint; if unset they fall back to `CHAT_*` and will fail on image inputs.

> [!WARNING]  
> Ensure the API keys in the `.env` file are correct and have the necessary permissions.
> No trailing whitespaces after variable names.
> `EMBEDDING_DIM` must equal the embedding model's output dimension **and** match the dimension the vector DB was built with. After changing the embedding model, delete `data/qdrant_db` and re-ingest.

```bash
# Chat Model Configuration (OpenAI-compatible - DeepSeek used in development)
CHAT_BASE_URL=https://api.deepseek.com
CHAT_API_KEY=
CHAT_MODEL=deepseek-chat

# Embedding Model Configuration (OpenAI-compatible - Aliyun Bailian used in development)
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_API_KEY=
EMBEDDING_MODEL=text-embedding-v4
EMBEDDING_DIM=1024

# (OPTIONAL) Multimodal model for image agents & PDF image summarization
VISION_BASE_URL=
VISION_API_KEY=
VISION_MODEL=

# Speech API Key (Free credits available with new Eleven Labs Account)
ELEVEN_LABS_API_KEY=

# Web Search API Key (Free credits available with new Tavily Account)
TAVILY_API_KEY=

# Hugging Face Token - using reranker model "ms-marco-TinyBERT-L-6"
HUGGINGFACE_TOKEN=

# (OPTIONAL) If using Qdrant server version, local does not require API key
QDRANT_URL=
QDRANT_API_KEY=
```

### 3️⃣ Build the Docker Image
```bash
docker build -t medical-assistant .
```

### 4️⃣ Ingest Data into the Vector DB
The vector database is **not** shipped in the repo — it is generated by ingestion. Mount the `data` directory so the built DB persists on the host, and ingest before starting the app:

- To ingest a directory of documents:
```bash
docker run --rm --env-file .env -v "$PWD/data:/app/data" medical-assistant python ingest_rag_data.py --dir data/raw
```

- To ingest a single document:
```bash
docker run --rm --env-file .env -v "$PWD/data:/app/data" medical-assistant python ingest_rag_data.py --file data/raw/brain_tumors_ucni.pdf
```

### 5️⃣ Run the Docker Container
```bash
docker run -d --name medical-assistant-app -p 8000:8000 --env-file .env -v "$PWD/data:/app/data" medical-assistant
```
The application will be available at: [http://localhost:8000](http://localhost:8000)

> [!NOTE]
> - Startup takes ~10-15s (the CV models are loaded); the health check may briefly report unhealthy. Wait for `Uvicorn running` in `docker logs medical-assistant-app`.
> - If host port 8000 is already in use, map another one, e.g. `-p 18000:8000`, then browse http://localhost:18000.

### Managing the Container:

#### Stop the Container
```bash
docker stop medical-assistant-app
```

#### Start the Container
```bash
docker start medical-assistant-app
```

#### View Logs
```bash
docker logs medical-assistant-app
```

#### Remove the Container
```bash
docker rm medical-assistant-app
```

### Troubleshooting:

#### Container Health Check
The container includes a health check that monitors the application status. You can check the health status with:
```bash
docker inspect --format='{{.State.Health.Status}}' medical-assistant-app
```

#### Container Not Starting
If the container fails to start, check the logs for errors:
```bash
docker logs medical-assistant-app
```


## 📌 Option 2: Without Using Docker  <a name="manual-setup"></a>

### 1️⃣ Clone the Repository  
```bash  
git clone https://github.com/konoluluxiuda/tcm-multi-agent-assistant.git  
cd tcm-multi-agent-assistant  
```

### 2️⃣ Create & Activate Virtual Environment  
- If using conda:
```bash
conda create --name <environment-name> python=3.11
conda activate <environment-name>
```
- If using python venv:
```bash
python -m venv <environment-name>
source <environment-name>/bin/activate  # For Mac/Linux
<environment-name>\Scripts\activate     # For Windows  
```

### 3️⃣ Install Dependencies  

> [!IMPORTANT]  
> ffmpeg is required for speech service to work.

- If using conda:
```bash
conda install -c conda-forge ffmpeg
```
```bash
pip install -r requirements.txt  
```
- If using python venv:
```bash
winget install ffmpeg
```
```bash
pip install -r requirements.txt  
```

### 4️⃣ Set Up API Keys  
- Create a `.env` file and add the required API keys as shown in `Option 1`.

### 5️⃣ Run the Application  
- Run the following command in the activate environment.

```bash
python app.py
```
The application will be available at: [http://localhost:8000](http://localhost:8000)

### 6️⃣ Ingest additional data into the Vector DB
Run any one of the following commands as required.
- To ingest one document at a time:
```bash
python ingest_rag_data.py --file ./data/raw/brain_tumors_ucni.pdf
```
- To ingest multiple documents from a directory:
```bash
python ingest_rag_data.py --dir ./data/raw
```

---

## 🧠 Usage  <a name="usage"></a>

> [!NOTE]
> 1. The first run can be jittery and may get errors - be patient and check the console for ongoing downloads and installations.
> 2. On the first run, many models will be downloaded - yolo for tesseract ocr, computer vision agent models, cross-encoder reranker model, etc.
> 3. Once they are completed, retry. Everything should work seamlessly since all of it is thoroughly tested.
> 4. **Model weights are not tracked in git.** The skin-lesion checkpoint auto-downloads. Other CV weights (e.g. `covid_chest_xray_model.pth`) must be placed manually under the corresponding `agents/image_analysis_agent/*/models/` directory, otherwise those image agents are unavailable.

- Upload medical images for **AI-based diagnosis**. Task specific Computer Vision model powered agents - upload images from 'sample_images' folder to try out.
- Ask medical queries to leverage **retrieval-augmented generation (RAG)** if information in memory or **web-search** to retrieve latest information.  
- Use **voice-based** interaction (speech-to-text and text-to-speech).  
- Review AI-generated insights with **human-in-the-loop verification**.  

---

## TCM herb query with Docker

The independent herb service provides source-grounded lookup, filtering and comparison. Import the local herb snapshot first, then build and start it on port 7863:

```bash
python -m tcm import --source /path/to/ETCM2.0 --version local-2026-10-10
docker compose -f compose.tcm.yaml up -d --build
```

Open http://127.0.0.1:7863. The database is mounted read-only; `/ready` checks the selected dataset. The lightweight service needs no chat, embedding or Tavily credentials. See [usage and dataset version configuration](docs/ETCM2_MVP_USAGE.md).

## Optional medical features

Image analysis, speech and web search now default to disabled. Enable them explicitly with `ENABLE_IMAGE_ANALYSIS=true`, `ENABLE_SPEECH=true` or `ENABLE_WEB_SEARCH=true`; speech/web also require their provider credentials. Disabled SDKs and CV weights are not loaded at text startup, and unavailable tools return explicit responses. See [configuration and startup limits](docs/OPTIONAL_FEATURES.md).

## Session memory and local concurrency

The API passes its `session_id` cookie into the agent graph. Different sessions
have separate history; turns in the same session are serialized. Output review
and web-answer generation receive that session's recent context.

Memory is **in-process and temporary**, with the active conversation trimmed to
`max_conversation_history` (20 messages by default). Restarting the application
clears memory. This is not an account-based or durable memory system, and the
in-memory checkpointer does not provide a session expiry/retention policy.

The local Qdrant backend is supported in a **single application process**. RAG
requests share one instance and queue while it is in use; this prevents competing
clients from locking the same folder. Do not run another worker, ingestion job,
or evaluation process against that directory concurrently. For multiple workers,
use a server-backed vector database and a shared persistent checkpointer.

Local source links use `/sources/{id}` with an explicit file allowlist and SHA-256 checks. Legacy `/data/...` links redirect only for registered files. Entire data/uploads folders are no longer public; generated image results use expiring session-scoped URLs. See [source publishing and access boundaries](docs/SOURCE_ACCESS.md).

## 🧪 Evaluation  <a name="evaluation"></a>

A minimal, self-contained evaluation harness lives in [`eval_rag.py`](eval_rag.py). It auto-generates QA pairs from the ingested corpus and reports label-free metrics plus a badcase list (it does **not** modify anything under `agents/`).

```bash
# 1. Verify the vector DB (dimension + a live retrieval probe)
docker run --rm --env-file .env -v "$PWD:/app" -w /app medical-assistant:dev python eval_rag.py --verify

# 2. Generate QA pairs and run the evaluation (~ n_per_doc per document)
docker run --rm --env-file .env -v "$PWD:/app" -w /app medical-assistant:dev python eval_rag.py --all --n-per-doc 10

# 3. Re-run against the cached dataset, with optional LLM-as-judge
docker run --rm --env-file .env -v "$PWD:/app" -w /app medical-assistant:dev python eval_rag.py --eval --judge
```

Reports are written to `data/eval/` (`report.json`, `summary.md`, `badcases.json`). Metrics: retrieval hit-rate, RAG stay-rate / routing distribution, retrieval-confidence distribution, answer keyword recall, and optional judge correctness. Badcases are flagged by retrieval misses, handoff away from RAG, insufficient-info responses, low answer overlap, and judge failures.

---

## Offline regression

Run `python -m scripts.run_offline_ci` after installing `requirements-ci.txt` in an isolated environment. Tests use temporary data and deterministic substitutes; skipped tests and network attempts fail the run. Reports are saved in `var/ci/`. The GitHub workflow runs on main pushes and pull requests. Private ETCM snapshot acceptance and live provider evaluation remain separate, explicitly unrun scopes. See [instructions](docs/OFFLINE_CI.md) and [local results](audits/offline_ci/report.md).

## 🤝 Contributions  <a name="contributions"></a>
Contributions are welcome! Please check the [issues](https://github.com/konoluluxiuda/tcm-multi-agent-assistant/issues) tab for feature requests and improvements.  

---

## ⚖️ License  <a name="license"></a>
This project is licensed under the **Apache-2.0 License**. See the [LICENSE](LICENSE) file for details.  

> This project is a derivative work based on [Multi-Agent-Medical-Assistant](https://github.com/souvikmajumder26/Multi-Agent-Medical-Assistant) by Souvik Majumder (Apache-2.0). It has been adapted to OpenAI-compatible chat/embedding providers and is being extended toward Traditional Chinese Medicine (TCM) / herbal medicine content.

---

## 📝 Citing <a name="citing"></a>
```
@misc{Souvik2025,
  Author = {Souvik Majumder},
  Title = {Multi Agent Medical Assistant},
  Year = {2025},
  Publisher = {GitHub},
  Journal = {GitHub repository},
  Howpublished = {\url{https://github.com/souvikmajumder26/Multi-Agent-Medical-Assistant}}
}
```

---

## 📬 Contact  <a name="contact"></a>
For any questions or collaboration inquiries, reach out to **Souvik Majumder** on:  

🔗 **LinkedIn**: [https://www.linkedin.com/in/souvikmajumder26](https://www.linkedin.com/in/souvikmajumder26)

🔗 **GitHub**: [https://github.com/souvikmajumder26](https://github.com/souvikmajumder26)

<p align="right">
 <a href="#top"><b>🔝 Return </b></a>
</p>

---
