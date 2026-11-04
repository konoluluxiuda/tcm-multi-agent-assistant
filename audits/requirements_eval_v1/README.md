> 2026-11-03复核：本文为历史交接，原工具存在真实执行、冻结和评分缺口，已在[修复验收](../requirements_eval_repair_v2/README.md)记录。旧草稿实际43场景46轮；不沿用本文的独立性或预算结论。

# requirements_eval_v1 审计

日期：2026-11-03。状态：**M0/M1 完成；M2 草稿完成（43 题，待独立复核与冻结）；M3 真实对照与 M4 镜像部署被外部条件阻塞。**

## M0 基线

- commit `1532105f…`，122 个未提交文件，源码清单 `b7d9b5ff…`，release `r1_20261030`。
- `var/requirements_eval_v1/baseline.json` 记录 dirty/commit/清单/release；未启动模型请求。

## M1 评测工具（完成）

| 文件 | 内容 |
| --- | --- |
| `tcm/evaluation/requirements_benchmark.py` | 场景/参考/结果 schema + 评分器（终态、对象、逐对象字段、证据、禁止断言、表格 hash/旧文件、边界） |
| `scripts/eval_requirements_generalization.py` | `prepare`/`dry-run`/`execute`/`score` 四阶段；冻结 hash 门禁、预算 R=0/G≤N/V≤2N/总≤3N、拒绝覆盖、`execute` 必须 `--authorize-execute` |
| `tests/test_requirements_benchmark.py` | 13 项：正例 + 6 类突变 + schema 校验 |
| `tests/test_requirements_eval_protocol.py` | 8 项：冻结不覆盖、预算、dry-run、源变化拒绝、未授权 execute 拒绝、计分聚合、空表不通过、全失败→unavailable、第二路失败保留第一块 |

## M2 草稿（完成，未冻结）

- `eval/requirements_generalization_v1/questions.private.draft.jsonl`：**43 题，7 类**（单药 6、多药 6、表格 9（含 3 追加）、数据介绍 4、边界 6、跨来源 6、关系 6）。
- 数据依据：真实 `var/tcm/herbs.sqlite3`、`name_candidates` 中文名映射、`mineru_retrieval_v3` 药典证据页、enrichment 成分关系。
- 协议与预算：`prepare` 冻结 `protocol/manifest/preflight`；预算 **R=0、G=43、V=86、合计 129**。
- `dry-run`：**43/43 通过**（评分器对参考自检）。
- 局限：草稿由实施 Agent 生成，**不满足独立出题要求**；中文名身份未人工核验；追加题旧文件 hash 需在执行时捕获。

## M4 部署准备（只读审阅）

- `.dockerignore` 已排除 `.env`、`.venv`、`var/`、`data/`、`*.sqlite3`。
- `Dockerfile.research` 只 allowlist 代码（app.py/agents/tcm/templates/assets），非 root 用户，固定 base digest 与依赖版本，带 source-revision/source-tree label。
- 未 build、未 push、未替换实例。

## 阻塞条件（连续三轮）

1. **M2 冻结**：需要未参与规则调优的独立出题者提供自然表达 + 人工核对来源与身份；我作为实施 Agent 不能自证独立。
2. **M3 真实对照**：需要本次模型调用授权（模型/请求次数/内容范围）；此前有限次数实验不构成本轮无限授权。
3. **M4 镜像**：需要 Docker 构建与独立端口/状态目录的运行授权。

## 口径

- 合成 dry-run 与草稿题不能解释为真实模型对新问题的成功率。
- 默认策略、运行实例与 Docker 均未变动。
