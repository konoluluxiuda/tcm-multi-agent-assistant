# 未见问句评测修复与隔离部署验收（2026-11-03）

> 后续：本次真实调用已获授权并执行，11次派发后因未知请求停止。此前“等待授权/未调用”的表述为准备阶段状态；最新事实见[首轮结果](../requirements_heldout_v2_live_v1/README.md)。

## 结论

评测工具已从“自造观察值的 dry-run”改为真实 ASGI、数据库、工具与 XLSX 执行；外部模型仍未调用。独立 AI 的 40 场景 / 43 轮原句已固定并完成来源一致性参考，真实对照协议已冻结，等待本次预算确认。新本地镜像已构建，最新名称目录的独立实例在 http://127.0.0.1:7932/，旧发布版对照实例在 7936；均关闭模型，不替换已有实例，不推送远程。

本记录接续 [12 项审查缺口](../requirements_eval_review_v1/README.md)，不覆盖旧报告。

## 实际改造

- `tcm/evaluation/requirements_benchmark.py`：逐对象字段、关系组、来源和值同时核对；缺失必须明确；真实 XLSX 重开逐单元格比较；追加验证父文件不变。排队、空观察和部分失败不能算成功。
- `tcm/evaluation/ledger.py`：SQLite 持久派发回执，在传输前扣预算；未知请求不自动重派，重复执行不覆盖历史，未执行组合仍计入分母。
- `tcm/evaluation/api_runner.py`：独立进程运行真实 ASGI；合成模式只替换 SDK，数据查询/交付不替换；真实模式 HTTP hook 校验模型、请求大小、输出限额并保存实际返回。观察只从响应与产物提取，不读参考答案造结果。
- `scripts/eval_requirements_generalization.py`：输入、源码（含未提交文件）、运行依赖、数据全量哈希冻结，前后校验；每组合独立状态与不可覆盖结果；真实执行要求传入冻结 manifest 的 SHA256。
- `scripts/prepare_requirements_references.py`：按本地来源生成字段、缺失、关系及共同靶点参考；不会调用模型，不能代替药材身份人工审核。
- `scripts/check_answer_delivery_container.py`：在专属容器中检查双来源交付、重放、隔离、过期来源拒绝和重启恢复。通过本地 owner 绑定提案注入测试执行器，**不是自然语言理解验收**。

## 结果与边界

| 检查 | 实际结果 |
|---|---|
| 全仓离线 CI | 1792 项，206 模块，无跳过、无联网尝试；随后新增出题原句防改测试，相关模块 41 项通过 |
| 合成 ASGI 对照 | 5 场景 × 3 策略；V 5/5，R 3/5，G 3/5；8 次模拟 SDK 派发，0 次真实请求 |
| R/G 未通过原因 | 两个 workbench 问答要求返回 answer，旧策略交付 complete 表格；保留失败，不放宽判据 |
| 干净状态部署 | 两个数据版本分别完成启动、查询、表格、追加、容器重建、会话和旧产物恢复 |
| 最新目录双来源答案 | 10 项检查通过；2 路实际数据、重放 0 新工具、权限隔离、重启回读 |
| 独立原句 | 40 场景，43 轮，7 类；未作为业务代码调优输入，尚无应用首测或真实模型成绩 |

第一位独立出题者因指南内嵌调优记录而退出，未使用其题目。第二位只接收纯能力说明，写入完整题目后遇到 Agent 额度限制；主 Agent 核验 JSON、场景/轮次数与重复性并固定哈希。属于独立 **AI** 出题，不是真人用户研究。题目见 `eval/requirements_heldout_v2/`；评分参考由实施 Agent 在运行前核对，身份仍未人工批准。

交接基线 `r1_20261030` 的名称目录无法绑定丹参/连翘，涉及 6 场景。已有 `r1_names_20261101` 发布版包含带出处的候选补齐，切换后参考问题清单为空。没有删题、改题、修改生产规则或覆盖旧发布版。数量依据为 2076 条来源记录、616 个药典条目、8665 个字段块，不是去重药材总数。

评分仍有明确范围：关系核对首屏见证及总量，不声明全量导出；边界以 HTTP/终态与禁用断言为主，不是临床语义人工评审；表格核对值与文件，不评审视觉排版。合成驱动成功不证明真实 LLM 泛化；不得把离线测试数量当作用户任务成功率。

## 复现与证据

- 全仓：`var/requirements_eval_repair_v2/full_ci/summary.json`
- 最终相关测试：`var/requirements_eval_repair_v2/final_focused.log`
- 合成对照：`var/requirements_eval_repair_v2/smoke_final/reports/0001.json`（执行时冻结版本；后续增加出题出处校验，不覆盖原运行）
- 最新目录部署：`var/requirements_eval_repair_v2/clean_names/summary.json`
- 最新目录答案：`var/requirements_eval_repair_v2/container_answer_names/summary.json`
- 旧目录部署：`var/requirements_eval_repair_v2/clean/summary.json`
- 旧目录答案：`var/requirements_eval_repair_v2/container_answer_v2/summary.json`
- 正式协议：`var/requirements_heldout_v2/live_v1/protocol.json`、`manifest.json`、`preflight.json`

本地镜像 `tcm-research-chat:requirements-eval-v2-20261103`，镜像 ID `sha256:e428b85cb7812ef6fce162a347fff7020a252b953d5239e2e749b22da664b9ec`。容器使用已有只读数据挂载，未将 PDF/数据库/密钥打包，因此这是干净运行状态的复现，不是任意空白机器无数据一键启动。

前一次答案验收脚本使用了 ReleaseSources 扩展版本标签，实际 answer API 比较的是基础 Sources，导致 409。修正测试提案的版本绑定后通过；原失败目录保留，生产服务未为测试降低校验。

下一步：本次明确授权后，按冻结协议执行 R/G/V。最多 R0/G43/V86，共129次请求；遇到 HTTP 错误或未知结果全局停止，不用剩余预算自动重测。完成后先分析阶段失败与真实收益，再决定是否调整默认策略。工作区未提交、未推送；既有用户改动保留。
