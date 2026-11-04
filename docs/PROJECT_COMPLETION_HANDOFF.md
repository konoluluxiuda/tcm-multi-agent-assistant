# 项目剩余工作：交接给实施 Agent 的操作文档

日期：2026-11-03。项目：**中草药知识检索与资料分析 Agent**。

本文依据当前工作区源码和验收回执编写。目标是完成可复现评测与部署闭环，取得可以用于求职介绍的证据。本文只是交接说明，**没有执行下述新增实现、模型调用、镜像构建或上传**。

## 0. 可直接发送给实施 Agent 的任务

> 请阅读 `docs/PROJECT_COMPLETION_HANDOFF.md`，按 M0→M1→M2→M3→M4 推进。先核对当前工作区和验收记录，保留已有未提交修改。优先完成评测执行器、评分器、离线故障注入及冻结协议，不新增业务框架，不重写已经通过的查询交付链。
>
> 本轮可直接完成本地代码、测试与文档。真实模型执行应核对本次具体协议是否已有授权；已有资料外发许可继续有效，但此前有限次数实验不能被当成本轮无限调用授权。若缺授权，先把完整协议、问题与预算做成可审阅产物，并继续独立的离线和部署准备。
>
> 评测必须经过实际 API 与交付流程，保留首测失败；不以模型输出合法 JSON、HTTP 200 或有 XLSX 文件就判成功。不得通过针对冻结题目补规则、放宽评分器、删除失败题来提高首测分数。默认策略与现有服务先保持；独立环境验收通过后再提出切换结论。
>
> 每个大阶段提供改动文件、复现命令、原始证据路径、通过与失败数量及剩余风险。需要外部操作时检查本次授权范围，不擅自推送 Git、Docker Hub 或替换现有实例。

## 1. 当前基线与不能误读的状态

已完成：可选 `requirements_v3` 的需求契约、对象绑定、能力和计划覆盖检查、表格/查询适配；聊天与工作台共用查询协调器；多路答案、部分失败、刷新恢复、请求重放、单轮对话提交及备份兼容。

最新证据：

- `audits/answer_delivery_v2/README.md`：本轮交付范围与回滚边界。
- `audits/answer_delivery_v2/offline_ci_summary.json`：203 个模块、1752 项通过，0 跳过、0 联网尝试。
- `audits/answer_delivery_v2/browser_result.json`：9 项浏览器检查通过，合成资料与模拟模型。
- `audits/requirements_v3/acceptance.json`：版本 v6，真实模型评测未完成。
- `docs/TCM_PROJECT_ROADMAP.md`：唯一大纲；开头最新状态优先于下方历史记录。

这些结果不能解释为真实模型对新问题的成功率。默认策略未切换，运行中实例和 Docker 未因本轮修复而自动更新。当前有大量此前与本轮未提交文件，不能用 `git reset --hard`、`git clean` 或整体覆盖来“整理”。

保留 Python、LangGraph、FastAPI、SQLite、现有 Qdrant/RAG 与已配置模型路线。本阶段不添加新多 Agent 框架，不重做向量数据库，不新增 PubMed，不把方药推荐、药效预测作为验收业务。

## 2. 代码地图

| 职责 | 现有文件 | 实施时应保留的约束 |
| --- | --- | --- |
| 入口、运行配置 | `app.py`、`scripts/preview_local_tasks.py` | 聊天与工作台都要验证；配置不等于实际命中策略 |
| 理解策略与服务 | `tcm/dataops/service.py`、`request_policy.py`、`requirements_understanding.py` | V 最多两次理解，纠错与复核共享预算；未知派发不自动重试 |
| 类型化需求 | `tcm/dataops/requirements_v2.py` | 严格 schema；澄清不携带可执行半个需求 |
| 宿主规划 | `capability_matrix.py`、`requirements_planner.py`、`requirements_pipeline.py`（均在 `tcm/dataops/`） | 绑定真实对象；规划函数不重新读原问句；按实际参数重推覆盖 |
| 统一查询交付 | `tcm/dataops/answer_delivery.py`、`api.py`、`chat.py` | 不绕开 owner、来源版本、journal、单轮记忆提交 |
| 表格交付 | `tcm/dataops/research_graph.py`、`research_contracts.py`、`artifacts.py` | fixed 执行；内容、来源、缺失、旧产物均需核对 |
| 展示 | `assets/web/answer_delivery.js`、`templates/index.html`、`tcm/dataops/research_workbench.html` | 所有查询块可读；部分失败与澄清可继续操作 |
| 诊断 | `tcm/dataops/diagnostics.py`、`scripts/inspect_request_diagnostics.py`、`tcm/tracing.py` | 本地诊断是基础，Langfuse 是附加视图，不是正确性裁判 |
| 持久化 | `tcm/request_journal.py`、`agents/conversation_store.py`、`agents/memory_backup.py` | 外置结果和会话一起备份；未知执行不得重复派发 |
| 可借鉴评测代码 | `scripts/check_unified_harness_live.py`、`scripts/eval_research_combined.py` | 参考协议冻结和 Recorder；旧脚本含 native 测试，不能直接充当新实验 |
| 基础部署验收 | `scripts/check_clean_reproduction.py`、`scripts/run_research_selection_container.py` | 当前关闭模型，验证已有语料上的新容器与空状态，不是从 PDF 重建数据 |

相邻回归：`tests/test_requirements_*.py`、`test_answer_delivery.py`、`test_memory_backup.py`、`test_clean_reproduction.py`、`test_unified_harness_live_protocol.py`。

## 3. M0：接手与固定基线

先执行只读检查：

```bash
cd /home/zry/workspace/Multi-Agent-Medical-Assistant
git status --short
git diff --stat
.venv/bin/python --version
.venv/bin/python -m pip check
.venv/bin/python -m scripts.preview_local_tasks --help
.venv/bin/python -m scripts.dataops_source_manifest --hash-only
```

1. 阅读本目录上级适用的 `AGENTS.md`（若存在）及上述验收文件。
2. 核对 `audits/answer_delivery_v2/verification.json` 的相关文件 hash。若已有新改动，应记录差异，不能假定旧成绩覆盖新代码。
3. 记录 commit、dirty 状态及源码清单；仅保存 `git diff` 不包含未跟踪文件，不能作为完整基线。将待测代码存入受控本地快照或提交，保存文件 hash。数据、密钥与用户状态不混入源码提交。
4. 只读检查数据 release 的 `release.json`、`sources.private.json` 及实际挂载文件存在性。不要打印 `.env` 或密钥；私有绝对路径留在本机记录。
5. 为本轮准备独立 `var/requirements_eval_v1/`，每次执行用新 run 目录，拒绝覆盖已有回执。公共审计摘要最终放 `audits/requirements_eval_v1/`。

代码未变且旧摘要可验证时不用先完整重跑一遍；实施后跑相关测试，收尾再跑全仓。若基线已变或有失败，先记录并判断是否阻塞。

**M0 验收：** 基线可定位，数据来源可用，已区分现有改动和本轮改动，尚无新增模型请求。

## 4. M1：先实现可信的评测工具

### 4.1 建议新增文件（目前尚未实现）

文件名可以调整，但职责必须保留：

| 建议文件 | 职责 |
| --- | --- |
| `tcm/evaluation/requirements_benchmark.py` | 题目、参考答案、运行结果 schema；类别、计分口径与校验 |
| `scripts/eval_requirements_generalization.py` | `prepare`、`dry-run`、`execute`、`score` 四个互斥阶段 |
| `tests/test_requirements_benchmark.py` | 评分器正例与突变反例 |
| `tests/test_requirements_eval_protocol.py` | 冻结、预算、回执、未知派发及续跑门禁 |
| `eval/requirements_generalization_v1/README.md` | 数据来源、排除已调优题、标注规则与冻结过程 |
| `audits/requirements_eval_v1/README.md` | 最终成绩、失败分类、成本与策略选择结论 |

先用合成小样本开发执行器，不能拿最终未见题逐条开发评分器。

### 4.2 两个测量范围，避免归因错误

- **主评测：产品端到端。** 经过真实 `/chat` 或工作台 API，保留真实本地优先路由。每题记录配置策略和实际策略；快捷查询绕开模型时不能记为 V 的理解收益。
- **补充诊断：理解契约层。** 如需隔离比较提取质量，可直接运行理解阶段，但单独计分、单独预算，不能冒充端到端完成率。除非首轮结果需要，不作为本轮必做项。

对照设置：

| 组 | 配置 | 外部理解请求上限 | 执行工具 |
| --- | --- | --- | --- |
| R | `local_then_one_model_v1`，关闭 goal model；工作台显式 `use_model=false` | 0 | 同一 fixed 执行器 |
| G | `local_then_one_model_v1`，开启 goal model | 每提交最多 1 次 | 同上 |
| V | `requirements_v3`，开启 goal model | 每提交最多 2 次 | 同上 |

不能用 `requirements_v3 + use_model=false` 代替 R：该配置本身可能返回“关闭模型”的澄清，与原本的本地基线不是一回事。禁用 RAG 生成、native loop、旧 intent model 及联网工具，避免叠加其他模型请求。

独立子进程或独立服务配置每个策略，避免同进程模块导入缓存污染环境。每题新会话与新 key；父子题在同策略内共享会话和父对象，三个策略绝不共享 journal、记忆或工作区。

### 4.3 API 执行流程

1. 访问 `/conversation` 或 `/dataops/research` 初始化同一个客户端的 cookie。
2. 聊天：`POST /chat`，请求体使用 `query` 与 `request_key`。
3. 工作台：`POST /api/dataops/research-questions`，请求体使用 `question`、`request_key`、`use_model`；父子参数按当前 `SubmitQuestion` schema 传递。
4. 工作台返回 query 提案时，再 POST `/api/dataops/answer`，body 只传原 `request_key`，禁止自行填 routes。
5. 返回 workspace 时，按响应中的 ID 轮询 `/api/dataops/agent-runs/{id}` 至终态；GET 轮询有超时与总时限，不能当作重新提交。
6. 下载 `/api/dataops/workspaces/{id}/artifact` 并核对实际单元格；查询结果逐个读取 `answer_steps`。保留 HTTP 状态、X-Request-ID 和原始响应。
7. 明确区分 clarification、unsupported、partial、unavailable、queued 和 complete。HTTP 200 不是自动成功；合理 partial 是否满足原始任务由冻结参考答案决定。
8. 重放与恢复检查放在明确子测试中，模型/工具新增调用必须为 0；不能把重放误作第二次模型试验。

开发阶段参考 `tests/test_requirements_v3_http.py` 和 `tests/test_answer_delivery.py` 的实际 body/fixture。不要直接复制某轮已过期的字段假设。

### 4.4 参考与评分器

每个场景至少保存：

```json
{
  "id": "H001",
  "split": "heldout",
  "category": "cross_source",
  "entrypoint": "chat",
  "turns": [{"id": "t1", "question": "由独立出题者填写"}],
  "expected": {
    "task_class": "supported",
    "objects": [],
    "requirements": [],
    "delivery": "answer",
    "allowed_terminal_states": ["success"],
    "source_assertions": [],
    "missing_assertions": [],
    "forbidden_claims": []
  }
}
```

这是待实现的示意格式，不是已存在的可执行题目。生产题需要具体对象、逐对象字段、关系配对、证据位置及来源版本，不允许空参考通过校验。表格题还要标注 keep_all/filter_complete/top_n、数量、追加的父字段和原文件 hash。

评分要求：

- 对象与字段按需求集合核对，不把完整输出逐字相等作为唯一标准。
- 关系题按整组对象与来源标识核对；当前仅分页支持的结果不能冒充全部，全部读取题应按冻结能力边界判拒绝。
- 证据必须指向同一来源版本中的实际记录、字段或页码/chunk，不能只判断“有引用字段”。
- 表格重新打开验证字段、每个对象对应的内容、缺失标记、来源列和工作簿 hash；增量题同时验证旧文件不变。
- 成功文本但漏路、错对象、证据不一致、错误地保留筛选行，一律不能算完整完成。
- 同时输出“支持任务完成率”“边界请求处理正确率”“澄清率”“未运行比例”，不把拒绝混进业务成功数。
- 父任务失败时子任务标为未运行并注明依赖失败，完整场景仍记未完成，不能缩小分母。
- 没有 usage 的超时请求记录为未知，不能按 0 token/0 成本处理；SDK dispatch、收到响应、已知 token 分列。

### 4.5 必须通过的离线故障注入

| 注入 | 必须观察到的结果 |
| --- | --- |
| 删除一个对象/字段/答案块 | 完整任务失败，并定位遗漏阶段 |
| 甲乙/丙丁换为甲丙/乙丁 | 关系组错误，不被集合去重抹平 |
| 交换工具或 namespace | 计划校验拒绝 |
| 引用存在但页码/来源 hash 错 | 证据正确性失败 |
| HTTP 200 但只有空表 | 根据真实任务及原因判定，不能无条件通过 |
| 第二路异常、第一路成功 | 保留成功块；终态 partial；列出未完成项 |
| 所有路不可用 | unavailable/503；不生成假成功 |
| 两次理解都已消耗 | 第三次派发在发送前被拦截 |
| 超时发生在已发请求之后 | 原始回执保留；该请求停止，不自动重发 |
| 429 后同 key 重试 | 按已有 journal 语义处理，不重复理解；是否恢复须断言实际状态 |
| 旧 key 换问句或父对象 | 冲突/拒绝，不能返回错任务的成功 |
| 外来 cookie、客户端 routes | 403/404 或 schema 拒绝，无数据泄露 |
| 改冻结题/数据/提示词/代码 | execute 前拒绝，不启动模型 |
| 恢复或重放 | 结果不变、新模型与工具调用为 0 |

**M1 验收：** dry-run 全链完成；故意错误均被评分器识别；执行器不会启动未授权/超预算请求；相关回归通过。测试项数以实际新结果为准，不硬填一个“目标数量”。

## 5. M2：冻结未见问句、参考答案与协议

### 5.1 题集建议

40 个场景建议覆盖：单药字段 6、多药字段 6、跨来源 6、关系查询 6、数据介绍 4、表格与追加 6、澄清/不支持/边界 6。每个追加场景可以多轮，因此 **40 场景不等于 40 次提交**。

由未参与规则调优的出题者提供自然表达，先看用户能力说明，不看失败词表。避免复用本轮已暴露的 Alpha/Beta、白术/黄芪固定问句作为未见题。独立 AI 出题应如实标注“独立生成问句”，不能声称是真实用户样本。

标注者应核对真实来源和能力矩阵。数据库值生成的参考只能证明实现与数据库一致，不能证明 OCR 内容或药材身份在现实中正确；保留待审核与冲突状态。所有题目须在运行前确定类别，不能运行后把失败题改为“不支持”。发现参考本身错误要另记更正及对分母的影响。

### 5.2 冻结内容

在新的 run 目录保存：

```text
protocol.json                 模型/策略/数据范围/预算/停止条件
questions.private.jsonl       完整场景与原始问句
gold.private.jsonl            逐对象参考与证据锚点
manifest.json                 题目、参考、评分器、工具、提示词、代码、依赖 hash
preflight.json                数据存在、配置与环境核验
dispatches.private.jsonl      请求前预约与返回后的追加回执
results.private.jsonl         逐题逐策略原始结果索引
diagnostics/                  请求诊断链
artifacts/                    XLSX/可下载结果等
report.json                   分层计分与效率指标
README.md                     结论、失败与限制
```

私有输入、会话 cookie、原始资料和诊断原文保留本机；公开审计只提交必要脱敏摘要与 hash。冻结清单必须覆盖 dirty/untracked 代码、候选名称目录、数据 release 和评分器，不能只有 commit ID。

### 5.3 模型调用协议

核对实际模型配置，不假定历史的 `deepseek-flash` 永远可用。不在日志打印 API key。协议明确 endpoint、model、thinking 配置、temperature、输出 token 上限、请求字节上限、超时、SDK retries=0、是否向 Langfuse 导出及内容范围。

当前理解 payload 可包括当前问句、候选名称目录、父字段/属性、纠错反馈、静态 schema/提示词；**不能宣称仅发送问句**。原则上此实验不需要发送整份 PDF、ETCM 数据库、完整历史或 native 工具交互。若实现改变输入范围，协议同步更新。

预算按实际提交数 N 计算：R=0，G≤N，V≤2N，合计≤3N；仅当每场景一轮时才是最多120次。独立复核、纠错、附加诊断或补充实验都必须计入，不能悄悄加次数。

在发送前持久化预算占用；发送后追加结果。未知派发或 HTTP 错误停止相应执行并保留回执，续跑仅运行未开始组合，另存清单，不覆盖、不自动重试失败组合。按照具体协议与现有授权判断能否 execute；不能仅因用户允许资料外发就假定任意新增次数已获批。

**M2 验收：** 题集、参考、协议与 hash 冻结；dry-run 评分器通过；能展示具体发送内容类别与预算。此时尚无真实成绩也可以完整交付 M2。

## 6. M3：真实对照、定位失败与选择策略

执行前核对冻结 hash；每题各策略使用同版本数据与固定执行器。按预先记录的顺序交错或随机化策略，避免服务时段差异；不能失败后临时换顺序重跑。CLI 必须显式选择 execute，不因导入模块、访问页面或准备协议而调用模型。

报告至少包含：

| 维度 | 必须记录 |
| --- | --- |
| 请求入口 | 场景/轮次/策略/request key/X-Request-ID/实际路由 |
| 理解 | 模型实际调用、纠错/复核次数、契约与失败码 |
| 绑定与计划 | 对象、字段、关系组、能力拒绝、覆盖情况 |
| 执行与交付 | 工具次数、成功/部分/失败、证据、文件、恢复 |
| 效率 | 本次与累计请求、已知 token、未知用量、端到端延迟、P50/P95 |
| 成本 | 有可信价格再换算；否则报告 token 与请求，不猜金额 |

失败优先找“最早出现错误的阶段”，再记下游影响：routing → requirements_extract → object_binding → schema/capability → plan_compile/plan_coverage → execution → delivery → presentation。区别来源缺失、名称歧义与算法错误，不一律归因“提示词不好”。

本地诊断示例（替换实际路径和编号）：

```bash
.venv/bin/python -m scripts.inspect_request_diagnostics \
  --root var/requirements_eval_v1/V-state/dataops \
  --request-id ACTUAL_32_HEX_REQUEST_ID \
  --out var/requirements_eval_v1/diagnostics/one-request.private.json
```

Langfuse 可按 trace ID 回溯；导出关闭或服务不可用时，本地诊断仍应足够定位。导出本身不能触发额外模型请求。

采用以下结论规则，并在冻结前登记，避免事后选择有利口径：

1. 越权、静默漏需求却成功、超预算或未知请求自动重发为阻塞项。
2. 对支持任务报告 R/G/V 的成对得失清单和完成率；对边界请求另报正确处理率。
3. V 没有净收益或以大量额外澄清换取低错率时，不默认切 V；可保留为可选策略，并解释成本权衡。
4. 40 场景只能支持小样本判断，不能写成广泛泛化或显著提升。若要设百分比上线门槛，必须运行前明确，不按首测结果倒推。
5. 修复进入独立开发回归集，产生 v2；原首测不动。再次使用同批题只能称重测，不能称新的未见问句评测。

**M3 验收：** 所有组合均有 completed/failed/not_run 等明确记录；失败可追溯，计数可对账；有保留或切换策略的证据结论。没有提升也可完成实验，不要求编造好成绩。

## 7. M4：干净环境、Docker 与恢复验收

### 7.1 环境范围

保留当前正式实例；使用新端口、新状态目录、新镜像 tag。先检查端口未占用和同名容器不存在，不自动杀进程。示例端口 7932/7936 仅作建议，执行前检查。

本地纯离线试用命令（现有 CLI，可直接使用；要求已有 release）：

```bash
.venv/bin/python -m scripts.preview_local_tasks \
  --port 7932 \
  --state var/requirements_eval_v1/local-preview \
  --research-selection fixed \
  --understanding-policy local_then_one_model_v1 \
  --rag-release var/data_releases/r1_20261030
```

这里只验证本地路径。G/V 真实理解需要 `--research-goal-model`，V 另选 `requirements_v3`；只能在明确协议范围内开启。不要同时加 `--with-intent-model`、`--rag-model` 或 `--research-selection native`。

### 7.2 构建独立本地镜像

源码、依赖和最终测试一致后执行，未提交代码应如实记录 dirty 状态：

```bash
task_source_revision=$(git rev-parse HEAD)
task_source_tree=$(.venv/bin/python -m scripts.dataops_source_manifest --hash-only)
docker build -f Dockerfile.research \
  --build-arg SOURCE_REVISION="$task_source_revision" \
  --build-arg SOURCE_TREE_SHA256="$task_source_tree" \
  -t tcm-research-chat:requirements-v3-acceptance-v1 .
```

镜像只包含代码与依赖，资料只读挂载、状态独立挂载。确认 `.dockerignore` 与 Dockerfile allowlist，没有把 `.env`、语料、cookie、私有审计或模型权重打入镜像。不要为了构建方便降低现有容器权限边界。

现有基础验收脚本：

```bash
.venv/bin/python -m scripts.check_clean_reproduction \
  --image tcm-research-chat:requirements-v3-acceptance-v1 \
  --release var/data_releases/r1_20261030 \
  --out var/requirements_clean_v1 \
  --port 7936 \
  --name tcm-requirements-clean-v1
```

输出目录必须不存在。该脚本会启动专用容器，完成固定查询与表格，停止并重命名专用旧容器，使用相同状态创建新容器验证恢复；不会自动删除所有容器或状态。其范围是**新依赖镜像＋空状态＋已注册只读资料**，不是从零提取 PDF，也未验证运行中任务崩溃恢复。

### 7.3 还需扩展的验收

`scripts/run_research_selection_container.py` 当前不配置真实理解模型，因此上述通过仍不等于 V 在容器中真实可用。扩展部署脚本和 `tests/test_clean_reproduction.py`，或新增明确独立脚本，至少覆盖：

- 运行元数据报告真实策略和请求上限；`restart_required=false`。
- 用同一种 `tcm.runtime_status.fingerprint` 算法比较宿主和镜像应用源码；镜像 label 的 source-tree hash 是另一个算法，不能混比。
- requirements_v3 的合成提案执行或已授权真实路径能生成跨来源多路答案；合成模型只在测试环境注入，不增加公共调试入口。
- 同 cookie 刷新/重启后所有答案块可读，重复执行新工具调用为 0。
- 保存表格、追加字段、旧文件 hash 不变；来源变化拒绝执行旧提案，历史 GET 仍可读旧快照。
- 新 owner 无法读取旧结果，任意 routes 注入被拒。
- 浏览器展示所有块、原页、部分失败和澄清后继续操作，无 JavaScript 错误。

### 7.4 备份、回滚与上传

备份会话库及整个 `DATAOPS_ROOT`，包括答案 journal、提案 journal、工作区、XLSX、诊断及任务状态。对活跃 SQLite 用数据库一致性备份或停服后复制，不能仅复制运行中的主库而忽略 WAL。

默认策略切换前记录原环境、镜像 ID、源码指纹、数据版本和恢复点。异常时切回旧镜像/旧配置，保留新状态副本；注意旧版备份校验器可能不认识新增表。不得删除整个 `var/` 或覆盖旧数据索引作为回滚。

Git 提交与远程 push 分开；Docker build 与 Docker Hub push 分开。本说明不自动授权远程发布。若后续授权，先审阅具体文件和镜像，Docker 用户名为 `8n1eld`，仓库名应实际核对，不能凭用户名猜仓库。不要执行无筛选的 `git add .`。

**M4 验收：** 新镜像有固定标识，实例与代码一致，基础查询/交付/恢复通过；清楚记录合成、真实、离线各自范围；回滚说明可操作。

## 8. 可选项：不要挡住主线

### 全量分页

当前 `completeness=all` 显式拒绝是已知边界，不是本轮必须取消的限制。若另行实现：先选一个关系工具，增加来源版本绑定、稳定游标、去重和完整性回执，流式外置保存；预算不足/来源变更/超时必须 partial，不能声称全部。适配 planner、执行器、UI、导出和评测后才能移除拒绝。不要把页大小设得很大充当全量读取。

### 首轮本地语义依据

只有 M3 证据说明复核过多或误澄清明显时再做。需证明本地依据能核对逐对象需求、关系配对和交付形式，不能只验证 schema 合法就跳过语义复核。独立开关、负例回归、调用计数与默认回滚均需保留。

## 9. 每阶段收尾格式与完成定义

实施 Agent 每个大阶段汇报：

```text
完成阶段：M?
修改文件及原因：
验证命令与原始证据：
通过 / 失败 / 未运行：
真实模型请求与已知 token / 未知用量：
默认策略 / 服务 / Docker / Git 是否变动：
剩余阻塞与下一阶段：
```

相关测试通过后，最终执行一次全仓 CI：

```bash
.venv/bin/python -m scripts.run_offline_ci --out var/requirements_eval_final_ci_v1
```

新问题、新修复才触发重复测试；不要靠反复跑全仓代替业务验证。更新 `docs/TCM_PROJECT_ROADMAP.md`、`docs/README.md`、相关使用文档和新的审计回执，保留历史成绩及旧协议。

求职版完成标准：可复现启动；典型任务可交付且有出处；失败可诊断、恢复和重放；未见问句对照有真实结论；简历的每个技术点能对应源码、测试或演示。全量分页、复杂语义记忆和新增框架不作为完成前提。
