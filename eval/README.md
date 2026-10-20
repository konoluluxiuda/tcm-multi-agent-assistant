# ETCM 药材固定验收集

当前大模型应用评测入口见[业务验收约定](../docs/APPLICATION_EVALUATION_PROTOCOL.md)：现有开发/回归输入的用途和指纹已留存，运行 `python -m scripts.check_eval_baseline` 可检查文件是否变化。该检查不执行任务，也不证明独立性；旧文件中的test标签不能直接当作新工作流的独立成绩。模型与本地dense实际运行记录见[实验记录](../docs/LLM_APPLICATION_EXPERIMENTS.md)。下文保留各历史套件的专用运行说明。

日期：2026-10-12，对应 M1-06。

[tcm_golden_v1.json](tcm_golden_v1.json) 包含 30 个固定用例、66 份涉及的来源记录及其字段预期，绑定 `local-2026-10-10` 的 2,076 份药材文件快照。

## 预期结果如何获得

1. 编码代理直接读取并核对黄芩、甘草、当归、人参、白芍、稀疏记录及两份 HuaShi 的基础字段。
2. 独立的标准库 oracle 从原始 JSON 遍历源字段、计算名称匹配和筛选集合；不导入生产 loader/repository，不读取查询结果作为答案，不调用 LLM 生成真值。
3. 将字段值、缺失状态、原值、真实数组位置和文件 SHA-256 固定保存。评测时只读取，不自动生成或更新答案。
4. 数据库存储的全量路径/文件哈希指纹必须匹配；可选 `--source` 还会检查原始目录的文件指纹及已冻结字段的 JSON Pointer。

**复核状态：** `source_reconciled_pending_human_signoff`。这是已与源文件对照的工程验收候选集，尚无人类审核人签核。代理核对不记为人工审定，也不是科学内容或临床效果核验。M1-06 尚有人工签核环节。

## 运行

仓库层只需要 Python 标准库，使用已导入的本地数据库：

```bash
python -m scripts.eval_tcm_golden
```

增加原始目录核对：

```bash
python -m scripts.eval_tcm_golden \
  --source /home/zry/workspace/HDCTI/dataset/ETCM2.0
```

检验已运行的 Docker API（仍需本地数据库做快照前置检查）：

```bash
python -m scripts.eval_tcm_golden \
  --base-url http://127.0.0.1:7863 \
  --out audits/tcm_golden/report_http.json
```

默认仓库报告为 `audits/tcm_golden/report_repository.json`；HTTP 模式不会访问聊天模型或外部知识服务。`/ready` 用服务配置版本，评测会核对其返回版本。

退出码：0 全部通过，1 有失败项，2 套件/数据前置条件不满足。快照指纹不匹配时停止评测，标记 blocked 和 not_run，不把失败结果计为通过。

用例覆盖：详情、名称归一化、HTML 名称、稀疏字段、缺失 PMID、同名候选、来源 ID、中文未映射、无记录/版本、筛选 AND、分页首/中/末/越界、非法参数、比较、数据库就绪。每条返回记录均与冻结字段和出处比较。

仓库层校验异常类型及提示片段；HTTP 层校验状态码与错误结构。两层不要求异常文案相同。固定示例中“Cold + Bitter”是**子串**筛选，包含带修饰词的原值，不等同于标准术语精确筛选。

## 人工签核与版本更新

审核人应核对 [复核说明与用例清单](../docs/TCM_GOLDEN_REVIEW.md)，确认查询语义、名称候选、缺失字段及标准答案无误。完成后填写 `review.human_reviewer` 和审核日期，并通过 Git 留下审定记录；未完成前不把 `review.status` 改为人工已审。

数据或查询语义变更时另建版本，说明变化和迁移，不自动用现有程序输出覆盖标准答案。生成器只产生待审核候选，且拒绝覆盖现有文件：

```bash
python -m scripts.build_tcm_golden \
  --source /path/to/new/ETCM2.0 \
  --version new-local-version \
  --out eval/tcm_golden_candidate_v2.json
```

生成器含当前快照的代表性断言，新数据变化需要审核并明确修改，不能为消除失败任意更新它们。不同基线通过率不能直接比较。

[本轮结果与限制](../audits/tcm_golden/report.md)。

## 任务候选草案（2026-10-15）

[tcm_task_candidates_v1.json](tcm_task_candidates_v1.json) 是另一个独立AI设计的60条任务草案，dev20/test40，全部designed、observed=0；与上方冻结30题分开管理。仅完成格式与来源锚点校验，尚未人工审定、运行任务或计算准确率。

运行 `python -m scripts.validate_tcm_task_candidates --source /home/zry/workspace/HDCTI/dataset/ETCM2.0` 检查候选来源；不传source只查格式与声明分组。此校验器不执行任务，报告中的passed指格式与来源校验通过。见 [候选说明](../docs/TCM_TASK_CANDIDATES.md)。

后续新增 `python -m scripts.eval_tcm_tasks`，默认通过ASGI测试客户端执行真实FastAPI接口检查；本地需已导入SQLite，`--source`可增加原始来源校验。它分别记录自动检查、partial、人工未执行和未支持状态，不生成业务准确率。当前55条API检查已运行，其余5条未执行；见 [逐题说明](../docs/TCM_TASK_EXECUTION.md)。

## 限定字段定位召回（2026-10-20）

[scoped_recall_v1.json](scoped_recall_v1.json)是新增24题来源辅助开发诊断：18条检索题、6条边界题。直接从固定源视图穷举指定药材/字段的全部非空非附录候选，计算限定口径Recall@5；重复字段与空正文不从分母中删除。它不是独立语义或临床黄金集。使用`python -m scripts.eval_scoped_recall prepare`准备参考，再用`python -m scripts.eval_scoped_recall run`比较三种现有方法；两个阶段均拒绝覆盖已有输出。详见[口径、首轮失败与复现](../docs/SCOPED_RECALL_EVALUATION.md)。
