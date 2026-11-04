# 新会话交接：中草药知识检索与资料分析 Agent

> 最新增量：可选工具反馈策略已完成源码和1835项离线检查；真实回归新协议待确认，旧live_v2不可继续复用。当前状态见[工具反馈验收](../audits/requirements_feedback_v1/README.md)。

> 2026-11-04状态更新：用户已确认恢复，本交接的暂停状态已解除。最新1804项全仓通过、7933专项通过，live_v2已完成全部可继续组合；HV2-02 V中断仍保留。真实业务收益及后续限制见[收尾验收](../audits/requirements_completion_v1/README.md)。评测结束后清理HTML历史注释，旧manifest不再匹配当前源码，勿照抄下文resume命令。以下保留原始交接快照。


更新时间：2026-11-03（Asia/Shanghai）。本文件用于替代超长聊天历史；先读本文，再按需读指定证据，不要重新遍历全部历史文档。

## 1. 当前指令与边界

用户希望完成剩余修复、评测、本地 Docker 更新及 Git 提交，随后为了节省额度，**明确暂停执行**，要求写本文并另开新会话。

- 本轮仅写交接，测试与模型评测已经停止。新会话先报告接手状态；用户明确要求继续后再执行。
- 未创建新的子 Agent。默认一个执行 Agent，确有独立审查需要再按用户指示委托。
- 不恢复、重置或清理工作区；不要删除未提交代码、历史成绩、SQLite派发回执及旧产物。
- 不自动调用外部模型，不自动重试超时/未知请求。下文区分两份已经获授权的实验；授权不是无限预算。
- 不推送远程 Git 或镜像。本阶段目标是本地提交及本地镜像验收。
- 尽量批量读取必要文件，避免频繁轮询、逐题修改、重复全仓测试与长篇重复汇报。

## 2. 环境与基线

- 仓库：`/home/zry/workspace/Multi-Agent-Medical-Assistant`
- Python：`.venv/bin/python`；不要使用缺依赖的系统/base Python。
- 当前 Git HEAD：`1532105`，标题 `test: freeze unseen questions and verify clean container recovery`。
- 写本文前 `git status --short` 有138行（含目录折叠，不等于138个实际文件）；大量修改来自此前多个阶段。**尚未提交任何本轮修改**。
- 可用数据发布版：`var/data_releases/r1_names_20261101`。旧 `r1_20261030` 缺少丹参/连翘中文候选，不能用旧版复现新目录能力。
- 最新来源参考计数：2076条ETCM来源记录、616个药典条目、8665个字段块。不是去重药材种数，也不是医学身份已审核。
- Docker Desktop 已启动，WSL docker 可用；此前“Docker不可用”属于历史状态。

## 3. 项目已实现什么

已有属性/药典/关系查询、跨来源多路答案、表格与父子追加、持久化会话和任务、幂等回执、所有权隔离、来源证据与请求诊断。

可选 `requirements_v3`：LLM提取需求 → 来源对象绑定 → 能力校验 → 机械计划/覆盖检查 → 共用查询或表格执行器。默认仍为 `local_then_one_model_v1`，未切默认策略。全量分页导出仍明确不支持。

不要把“多模块离线绿”说成“真实模型泛化达标”，也不要再引入新框架补名词。

## 4. 最近三阶段改造

### A. 评测执行器修复与独立题集

原评测存在自造观察值、冻结/预算/结果覆盖等缺口，已修复。

核心文件：
- `tcm/evaluation/requirements_benchmark.py`：实际字段、逐对象、关系组、来源和值、缺失、真实XLSX与父文件校验。
- `tcm/evaluation/ledger.py`：派发前扣预算，SQLite回执；未知请求停止后续调用，已有组合不可覆盖。
- `tcm/evaluation/api_runner.py`：隔离进程、真实ASGI/工具/数据/文件交付。合成模式只替换SDK；真实模式HTTP hooks限制模型、大小、次数并保存请求响应。
- `scripts/eval_requirements_generalization.py`：prepare/dry-run/execute/score，源码/依赖/数据/题集冻结；执行需要manifest SHA256。
- `scripts/prepare_requirements_references.py`：只读来源生成参考，不调用模型。

证据：`audits/requirements_eval_repair_v2/README.md`。
独立AI题集：`eval/requirements_heldout_v2/`，40场景43轮，原句及作者说明已固定哈希。第一位作者因读到指南中的调优记录被弃用；第二位仅基于纯能力说明出题。它是独立AI出题，不是真实用户研究。参考由实施Agent核对，不代表药材身份人工审核。

### B. 回答/表格Schema与纠错反馈（已完成全仓验收）

文件：`tcm/dataops/requirements_v2.py`、`requirements_understanding.py`。

- Delivery按form分为AnswerDelivery/TableDelivery，answer的row_policy/count只能null或省略；table必须row_policy。
- 兼容旧持久化null字段，保留top_n和append约束。
- 提示给出合法交付示例；输入包含来自能力注册表的标准字段清单。
- `correction_fields`返回稳定错误码、字段路径、具体改法，不把源文件行号/hash当作模型纠错说明。
- 两次预算不变；理解＋纠错耗尽预算后仍不能绕过语义复核执行。

证据：`audits/requirements_delivery_feedback_v1/README.md` / `acceptance.json`。
**1799项、207模块、0跳过、0联网尝试全部通过**；100项专项和6份已保存真实响应回放通过。该成绩不包括下一节新增修改。

### C. 来源选择与可接受参考（最新工作区修改）

- `tcm/dataops/query_coverage.py`：对未明确写药典/ETCM、且无否定约束的“性味/归经”，允许选择ETCM完整字段或药典对应字段；显式来源、无关要求与缺字段仍校验。
- `tcm/evaluation/requirements_benchmark.py`：Scenario新增 `alternative_expectations`。逐个完整方案评分，不允许拼凑来源凑分；对象、任务类别和交付形式不能改变。
- 新测试 `tests/test_source_choice_policy.py`：双来源、遗漏字段、明确来源、其他要求保留、替代参考完整性。
- **56项针对性测试通过**。
- 新全仓检查 `var/requirements_completion_v1/full_ci/` 被用户暂停，**未完成、不能写全绿**。此前1799项仅代表B阶段。

## 5. 两轮真实模型记录：严格分开

### 首轮 live_v1（已因未知派发终止）

目录：`var/requirements_heldout_v2/live_v1/`。
用户授权最多129次；实际11次，10次HTTP200，1次约30秒后响应未知；按协议停止，未重试。

前4场景×3策略已启动，其余108个组合未启动。原始评分R0/4、G3/4、V0/4，但必须附以下解释：
- G三题完成真实字段＋来源查询，补上规则缺口。
- V前三题六份响应均在answer带row_policy，纠错重复错误；第4题是传输未知，不是已证实理解失败。
- HV2-02原问句未指定ETCM/药典，但参考只接受ETCM。R返回有出处药典内容，不宜直接判断业务错误。

证据：`audits/requirements_heldout_v2_live_v1/README.md`、`findings.json`；原始回执、评分和题集不能改写。当前源码已变，旧manifest校验拒绝是正常保护。

### 修复后 live_v2（用户暂停，不是自然结束）

目录：`var/requirements_completion_v1/live_v2/`。
用户再次明确授权最多129次：4个已见场景回归（明确包括此前未知的第4题）＋36个此前未运行场景。
模型/范围：api.deepseek.com / deepseek-flash，thinking关闭；R0/G≤43/V≤86；每次1200输出token、64KiB、30秒、不自动重试；只发送问句、静态工具/标准字段、候选名称、父字段和纠错反馈，不发送原始资料或历史。HTTP错误/未知派发停止。

Manifest SHA256：
`f39416ab281b54fa944afe1067a08b36a9f782e5149fe68f60e64e47be887b57`

当前只读核验：
- 4次派发，全部HTTP200：HV2-01 G一次、V两次；HV2-02 G一次。
- 组合已完成：HV2-01 R/G/V，HV2-02 R/G。
- **HV2-02 V已经reserve为started，但没有该组合的provider派发记录**；用户暂停时协调器和子进程被终止。不要直接删journal或自动重跑。
- 尚无整套结果，不预判新方案改善。
- `authorization.json`保存本次授权，`ledger.sqlite3`是调用事实，不能只看日志末尾。

用户明确恢复后，若代码/数据/协议均未改，可以在同一授权范围内继续尚未启动组合，无须重复索要同一授权。现有 `--resume` 只跳过已开始组合，不会补跑HV2-02 V；它会作为中断缺口保留。若要补跑这个组合、修改源码/预算/协议或重测，需明确另存方案，不覆盖现有回执。不能因为“还剩125次”就重新跑整套。

## 6. Docker实际状态

均绑定127.0.0.1，模型关闭，不含密钥/PDF/数据库；数据使用主机只读挂载，因此不等于无数据机器一键启动。

| 端口 | 容器 | 状态 |
|---|---|---|
| 7933 | `tcm-requirements-clean-completion-v1` | 最新源码镜像，健康；clean验收已完成 |
| 7932 | `tcm-requirements-clean-names-v2` | 前一阶段镜像＋新名称目录，保留 |
| 7936 | `tcm-requirements-clean-v2` | 前一阶段镜像＋旧发布版，保留 |

最新镜像：`tcm-research-chat:requirements-completion-v1-20261103`。
最新clean验收：`var/requirements_completion_v1/clean/summary.json`，`status=passed, stage=complete`，包括启动、查询、表格、追加、容器重建恢复。它在暂停前已完成；本次写交接时仅查询状态。

**最新7933尚未运行专门的 `check_answer_delivery_container` 双来源交付检查**；7932/7936此前跑过，不可冒充7933结果。
Docker镜像未推送远程。新实例保留旧实例，不删除状态目录。

## 7. 下一会话最短执行顺序（用户恢复后）

1. 读本文、`git status`及上述ledger；确认没有重复运行的评测进程，不读取密钥正文。
2. 若不改代码，先补完C阶段全仓离线检查，使用新输出目录，保留中断记录。不要把1799项旧成绩用于最新源码。
3. 给7933补双来源答案交付验收（仅会重启该隔离测试容器）：

```bash
.venv/bin/python -m scripts.check_answer_delivery_container \
  --base http://127.0.0.1:7933 \
  --state var/requirements_completion_v1/clean/empty-state \
  --release var/data_releases/r1_names_20261101 \
  --container tcm-requirements-clean-completion-v1 \
  --out var/requirements_completion_v1/container_answer
```

4. 若用户明确恢复真实对照，先validate冻结文件；不改源码的条件下，继续未启动组合：

```bash
.venv/bin/python -m scripts.eval_requirements_generalization execute \
  --out var/requirements_completion_v1/live_v2 \
  --authorize-execute f39416ab281b54fa944afe1067a08b36a9f782e5149fe68f60e64e47be887b57 \
  --resume
```

此命令**现在不要自动运行**。它不是重跑中断组合；先检查ledger.stopped()及派发状态。代码hash变化时不要绕过校验，另建协议。

5. 分开报告4题已见回归和36题此前未运行结果；记录来源参考调整、未知/中断/未运行，不修改旧分数。不因测试不好看反复改题。
6. 更新 `docs/TCM_PROJECT_ROADMAP.md` 与新增验收记录；检查差异和敏感信息后本地Git提交。历史大量改动可能相互依赖，分批提交时保证可运行，勿丢弃他人修改或无检查地 `git add .`。
7. 默认不push。不要把 `var/`、私有来源参考、原始请求响应、数据库、PDF、密钥放入提交。

## 8. 尚未完成与不要宣称的内容

- 最新C阶段全仓验收、中断的真实对照、7933专门双来源验收、Git提交尚未完成。
- 新方案真实收益尚未证明；本轮没有改默认策略。
- 全量分页读取未实现；不用为“项目完成”临时扩展到所有疾病/方剂预测或临床推荐。
- 本轮目标是收尾并形成可复现证据，不继续扩框架/重建架构。

## 9. 给新会话的简短启动指令

> 请先读取 docs/NEXT_SESSION_HANDOFF_20261103.md，只核对当前状态并用简短清单汇报。不要重读全部聊天历史，不创建子Agent，不调用外部模型，不恢复中断评测，也不要提交或推送，等我确认继续。已有工作区修改和所有原始回执必须保留。
