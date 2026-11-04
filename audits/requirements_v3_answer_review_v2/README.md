# 统一查询交付第二次独立复核

日期：2026-11-02。审阅对象：`audits/requirements_v3/acceptance.json`（answer-delivery-v5）及当前工作区。

## 结论

**旧的 HTTP 多路内容丢失已修复；本阶段尚不能按“完成标准全部满足”验收。** 剩余问题集中在页面消费、最终交付保存、执行重放和失败状态。它们属于上一份交接文档第 4 节的已有范围，不要求扩展业务或更换架构。

本轮未修改业务代码、默认策略或既有 acceptance；未重启服务、未调用真实模型、未提交/推送。新增内容仅为本目录审查材料。

## 1. 复核证据

- 独立重跑 7 个 requirements 测试模块：**102 项通过，0 跳过、0 联网尝试**，包含新增的 21 项 HTTP 测试。
- 6 项额外缺陷探针：**全部成功复现错误行为**，不计入产品验收通过数。
- 核对已有 `var/requirements_v3_ci17/summary.json`：1733 项、passed、0 跳过、0 联网尝试；本轮未重复执行全仓 CI。
- 具体结果和源码哈希见 `verification.json`；6 份 JSON 是合成数据复现回执。
- 页面行为为源码控制流审查，本轮没有运行浏览器或 JavaScript 引擎，不将它写成浏览器实测通过/失败。

已确认完成：逐路结构化结果和可读文本汇总；成功+返回失败对象时的 partial；合成 ETCM 和药典索引查询；同类多路结果保留；工作台 query 调用入口；超时模拟器正确抛异常；来源版本变更拒绝。前一轮两个缺陷已关闭，不需推翻这些实现。

## 2. P1：HTTP 含两路结果，聊天页面仍只展示第一路；工作台澄清分支被删除

### 聊天页面

`templates/index.html:1939` 在顶层存在 `tcm/research/assistant` 等字段时不显示 `data.response`。随后第 1987–2014 行仅选择一个旧字段渲染，从未读取 `answer_steps`。

`app.py` 合并后仍保留第一路的顶层 `tcm` 等字段。因此真实合成查询 `Alpha和Beta的药用部位` 的 HTTP 响应虽然包含 Alpha/Leaf、Beta/Root，页面会走 `data.tcm → renderHerbResult`，只拿到 Alpha。跨来源场景同理。

### 工作台澄清

`tcm/dataops/research_workbench.html:50` 新增 query 分支后，没有保留最终的澄清 `else`。没有 workspace 且不是 query 的正常澄清响应会直接结束 try：不展示问题、不恢复 `busy=false`、不启用提交按钮。用户无法修改问题后继续提交。

工作台的 query 分支仅显示 `exec.response`；没有呈现 `answer_steps` 的完整出处或 `unfinished` 列表。提示“详见下方未完成项”却未添加对应展示，且未清理上一份表格结果，容易把旧产物当成新查询结果。

**修复与验收：** 优先按 `answer_steps` 渲染全部块，再兼容旧单路；工作台穷尽 query/workspace/澄清分支，并在 finally 恢复可交互状态。浏览器测试需实际断言两路可见、证据可查看、澄清后可再次提交以及旧产物不冒充新结果，不能只断言 JSON 包含字符串。

## 3. P1：只少写了额外轮次，没有保存完整交付

`agents/agent_decision.py:955` 在第一路执行后立即提交；额外路由 `persist_turn=False`，且最终汇总没有再次提交完整交付。`test_extra_route_does_not_persist_a_turn` 只证明消息数量没增长。

本轮真实合成 ETCM 数据库 + 真实聊天执行探针确认：

- HTTP 同时返回 Alpha/Leaf 和 Beta/Root；
- 已保存的末条 AI 消息仍是第一路“药材资料如下。”；
- 已保存的药材上下文严格等于第一路 Alpha 上下文，和第二路 Beta 上下文不同；
- `/conversation` 恢复逻辑只使用已保存上下文，没有完整 `answer_steps` 的交付引用。

证据：`saved_first_only.json`。这不要求把全部原文塞进记忆；可以保存紧凑摘要、完整交付的持久化引用及明确的追问作用域。当前没有这样的最终提交。

**修复与验收：** 将“逐步执行”和“提交一个完整轮次”分开。全部路由使用同一请求/会话上下文，完成汇总后一次性保存可恢复的交付引用和结果摘要；明确多对象追问需要选择对象的情况。断言刷新后两路均可恢复，且旧轮次不被半成品覆盖。

## 4. P1：状态归并和异常处理尚未覆盖完整失败契约

位置：`tcm/dataops/answer_delivery.py:6`、`:40`，`app.py:718`，`tcm/dataops/api.py:300`。

| 复现 | 实际行为 | 预期 |
| --- | --- | --- |
| 药典返回 `partial_evidence`、缺少注意字段 | 汇总为 success，unfinished=[] | partial，并保留缺失字段 |
| 第一条数据库不可用、第二条成功 | JSON 为 partial，HTTP 仍为 503 | 部分成功的 HTTP 策略一致，不能由失败发生顺序决定 |
| 工作台所有路由不可用 | JSON unavailable，HTTP 200 | 与声明的 unavailable/503 契约一致 |
| 第一条成功、第二条抛运行时异常 | 聊天整体 500，只剩通用错误，第一条成功消失 | 保存成功结果，将该步记为失败，不自动重试 |

对应证据：`partial_promoted.json`、`first_failed.json`、`workbench_failed.json`、`second_exception.json`。

`partial_evidence` 表示明确不完整，不能加入完整成功集合。澄清、无结果、部分证据、不可用也应按工具契约分类，而不是用一个跨工具二分白名单粗分。调用异常需保留内部可诊断信息，并返回稳定错误码；权限/版本冲突等前置错误仍保持对应契约，不应无差别吞掉。

**修复与验收：** 定义统一的步骤结果到总体状态/HTTP 映射；在单步边界收集执行失败；两入口只应用最终汇总状态。对成功/部分/失败组合做正反顺序测试，增加实际抛异常测试。

## 5. P1/P2：工作台只是复用提案，并没有交付重放和完整执行身份

`tcm/dataops/api.py:292` 从 journal 读取的是理解提案，执行后的答案不保存到交付回执。第 300 行调用：

```python
execute_routes(process_query, proposal.get('question') or '', None, routes)
```

新 query 提案不含 `question`（见 `tcm/dataops/service.py:214`），且此处没有传入原会话或 request_key。

合成 ASGI 探针实际观察：同一个 key 提交 `/answer` 两次，两条路由执行共 **4 次**；四次执行的 question 都为空、session_id 都为 None、request_key 都未提供。证据：`workbench_replay.json`。

这不是跨用户读取漏洞：取提案的 owner 校验仍存在。问题在于执行阶段重新生成临时会话，没有跟随原用户执行身份，也没有持久化答案供重放/恢复。当前都是本地只读查询，不等于重复收费，但不满足前一份交接要求的“已有结果可重放”。

**修复与验收：** 保存原问句及绑定版本；建立 owner + request_key + 提案/来源版本的交付回执；执行前预留、完成后保存结果引用，已完成请求直接重放。统一传递原会话或等价的稳定执行身份，不接受客户端提供任意 routes。同 key 不增加工具执行或会话轮次，异主不可执行/读取，未知执行状态不盲重跑。

## 6. P2：共享执行与诊断的验收口径偏大

- 聊天仅调用 `merge_answer_datas`，仍在 `app.py` 中自己逐路执行；工作台才调用 `execute_routes`。当前共享的是汇总函数，不是完整执行器，因此状态和保存策略继续分叉。
- `app.py:645` 的 `query_execution` 记录只有第一路；额外路由循环和新共享模块没有逐步诊断事件。不能将返回体有 `answer_steps` 等同于诊断链有各步记录。
- 合成药典测试验证了索引读取与来源哈希匹配；`_pharma_fixture` 写出的最小字节不含真实页面/TEST_EFFICACY 原文，未证明 PDF 页面可打开或原文与页码内容一致。这不否定检索测试，但需缩小“PDF 证据验证”的表述。

## 7. 下一次收尾范围与停止条件

仍只完成原来的“统一查询交付”，不新增工具/框架，不扩大全量读取能力。建议按以下顺序整批完成：

1. 统一结果契约与状态分类，补异常边界和最终 HTTP 映射。
2. 两入口调用同一个协调器：绑定身份/来源 → 执行回执预留 → 逐步执行并记录诊断 → 汇总 → 保存完整交付/提交一轮 → 重放。
3. 前端实际消费该结果契约，恢复澄清与按钮状态，支持刷新恢复。
4. 新增正式回归：双路浏览器展示、澄清后再次提交、刷新恢复、重放零新增执行、权限、状态组合/异常、每个 step 的诊断关联。
5. 更新 acceptance：逐项链接测试证据，把未覆盖项保留在 not_implemented；不再用“消息数不增”代替“完整轮次已提交”。

上述回归通过后即可进入冻结问句真实模型评测。无需先做全量分页或扩大本地语义规则。若只做理解层实验，应单列范围，不把它作为完整业务任务成功率。

## 8. 重现本轮探针

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
import subprocess, sys
from scripts.run_offline_ci import test_environment
out = Path('var/requirements_v3_answer_review_v2_replay')
out.mkdir(parents=True, exist_ok=True)
subprocess.run([
    sys.executable, '-m', 'scripts.run_offline_ci',
    '--worker', 'audits/requirements_v3_answer_review_v2/probe.py',
    '--worker-output', str(out / 'probe.json'),
], env=test_environment(), check=True)
PY
```

探针断言的是当前缺陷。修复后应相应失败，正式产品回归则反向断言正确交付。重新执行会更新本目录的合成 JSON 回执，不接触真实资料或在线服务。
