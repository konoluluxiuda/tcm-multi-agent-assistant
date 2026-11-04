# requirements_v3：answer 交付阶段独立复核

日期：2026-11-02。范围：复核本轮 answer 接线、工作台行为和生命周期验收口径；不修改业务代码、不变更默认策略、不重启服务、不调用外部模型。

## 结论

继续完成报告中第 1、2 项，并作为同一阶段收尾，再进入冻结未见问句评测。新契约和规划器的既有修复仍成立；当前跨来源 answer **尚未完整交付**，不只是缺少数据库夹具测试。

- 本轮独立重跑 7 个 requirements 模块，共 **93 项通过，0 跳过、0 联网尝试**。
- 另有 **2 个缺陷复现通过**：其通过表示成功重现缺陷，不是产品验收通过。
- 核对已有 `var/requirements_v3_ci12/summary.json`：1724 项、passed、0 跳过、0 联网尝试；本轮没有重新执行全仓 CI。
- 独立结果见 `verification.json`；合成 HTTP 原始结果见 `candidates.json`、`research_unavailable.json`。

## 1. P1：额外路由执行后，结果与失败状态未进入最终交付

位置：`app.py:649` 附近的 `extra_routes` 循环、后续结果装配及 `plain_answer(result)`；`tcm/answer_text.py:18`。

当前循环只拼接各次 `messages[-1].content`，后面的 `tcm/research/assistant` 等结构仍只取第一次 `response_data`。最后 `plain_answer()` 遇到第一路 `tcm`，会重新生成文本并覆盖拼接文本。

真实 ASGI、真实 requirements 理解/绑定/编译/交接链，替换模型和工具响应后的复现：

| 场景 | 执行次数 | 最终实际结果 | 应有结果 |
| --- | --- | --- | --- |
| ETCM 药用部位成功 + 药典功效成功 | 2 | 只有药用部位；功效文本与 PDF 第 7 页证据均丢失 | 两块结果与各自出处均可见 |
| ETCM 成功 + 药典 `research_unavailable` | 2 | HTTP 200、`status=success`，没有第二路失败信息 | 保留成功块，并明确未完成的需求及原因 |

这里替换了 `process_query` 返回值，并强制进入理解兜底，验证的是 HTTP 汇总边界；**不是实际数据库检索成功的证明，也不是自然语言泛化评测**。工具输出即使正确也会丢失，已足以定位本次接线错误。

另经静态检查，`process_query` 每调用一次都会加载并保存一次对话（`agents/agent_decision.py:_process_query`）。直接逐路调用还可能将同一用户问句写成多个完整轮次；本次探针替换了该函数，未实测记忆写入次数，需在下一项集成测试中断言。

## 2. 工作台 query 分支是已知未完成项，当前还有明确前端错误路径

`tcm/dataops/research_workbench.html:50` 对所有没有 workspace 的响应执行 `result.proposal.issues.join(...)`。新 `status=query` 提案没有 `issues`，因此进入该分支会抛 JavaScript 错误，而非执行或展示查询结果。这一结论来自源码检查，未声称完成浏览器自动化。

第 51 行也只把 `unified_v2` 识别为两次请求策略；`requirements_v3` 被显示为旧版“一次模型”文案，需要随接线一起更新。

## 3. 生命周期测试口径需精确

- 429 重试、表任务同 key 复用理解、所有权读取隔离已有实际 ASGI 断言。
- 当前恢复测试是同一进程中新建 `RequestJournal` 并重新 reserve，证明预留状态落盘；不能等同于完整服务重启后恢复执行和交付。
- `RequirementsSequence.invoke()` 当前将每个 reply 直接传入 `AIMessage.tool_calls.args`。传入 `TimeoutError(...)` 时没有显式 `raise`，会在消息构造阶段失败；因此现有未知派发用例并未精确注入提供方超时。应让模拟器遇到异常对象直接抛出，再保留 503 → 同 key 409、无新增调用断言。
- `to_query_routes` 与旧 planner 形状相同只能证明静态契约兼容；真实执行、最终文本、证据及状态仍需集成验证。

## 4. 下一项完整任务：聊天与工作台统一查询交付

### 实现范围

1. 在服务层抽取共享的多路查询执行/汇总入口，由聊天和工作台共同调用，避免在两个入口分别追加循环。
2. 保存每个步骤的结构化结果、状态、来源、失败信息和需求对应关系；按对象/来源展示结果块，再生成可读文本。不能只拼占位消息，也不能将所有结果塞进唯一 `tcm` 字段导致同类工具互相覆盖。
3. 全部需求成功才报 success；部分失败保留成功部分并说明未完成项；全失败按既有失败契约返回。诊断链覆盖每一路，不能只记录第一路。
4. 一条用户请求只提交一个完整对话轮次；理解调用数按请求计一次，不能累加每条 route 上复制的 `intent_model_calls`。步骤保留稳定标识。
5. 工作台按 query/澄清/workspace 分支处理。执行服务端已保存并绑定当前所有者的提案，不接受客户端任意提供工具路由；执行时核对来源版本和原有请求签名。同 key 不重新理解，已有结果可重放。
6. 更新运行提示和验收口径。保持默认策略不变、全量读取显式不支持，不在这一步扩大支持范围。

建议受影响文件：`app.py`、`tcm/dataops/service.py`、`tcm/dataops/chat.py`、`tcm/dataops/api.py`、`tcm/dataops/research_workbench.html`、`tcm/answer_text.py`，以及共享执行器和对应测试。根据现有会话写入边界调整 `agents/agent_decision.py` 的执行接口；不要绕过会话隔离。

### 复用已有合成数据

`tests/dataops_fixture.py:make_sources()` 已生成 `herbs.sqlite3`、药典索引及 `enrichment.sqlite3`。缺的是将它们接到聊天工具的运行配置，而不是仓库完全没有合成药材 DB。

ETCM 需连接实际聊天工具使用的数据库/数据集配置；药典还需补齐其真实研究服务要求的合成 PDF、哈希、页码与证据校验链，可复用现有 MinerU 测试夹具。ETCM Alpha 与药典甲草的映射也必须明确，不能让测试默许跨库同名或身份已核验。

### 完成标准（整批做完再汇报）

| 验收 | 必须断言 |
| --- | --- |
| 单路 dataset / ETCM / 药典 | 通过 HTTP 查询并得到实际业务结果，不能止于提案 |
| ETCM + 药典 | 两侧真实合成检索值、证据、需求覆盖均在最终响应与页面中可见 |
| 同类多路 | 两味药或多个字段均保留，不被覆盖 |
| 第二路失败/缺失 | 不报完整成功；保留第一路与明确的未完成原因 |
| 聊天记忆与诊断 | 一条问句一个对话轮次；每一步可追踪；模型计数不重复 |
| 工作台 | query 可执行并显示；澄清/table 原行为不回归；无 `.join` 异常 |
| 重放/所有权/超时 | 同 key 无新增模型理解；异主不可执行/读取；真实抛出的超时进入未知结果保护 |

满足这些标准后更新 acceptance，再进行 40 题冻结评测。将失败分到理解、绑定、规划、执行、汇总、呈现各层，避免把本次交付丢失误计为模型理解失败。

## 5. 复现本次缺陷探针

在仓库根目录执行：

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
import subprocess, sys
from scripts.run_offline_ci import test_environment
out = Path('var/requirements_v3_answer_review_replay')
out.mkdir(parents=True, exist_ok=True)
subprocess.run([
    sys.executable, '-m', 'scripts.run_offline_ci',
    '--worker', 'audits/requirements_v3_answer_review_v1/probe.py',
    '--worker-output', str(out / 'probe.json'),
], env=test_environment(), check=True)
PY
```

注意：这是锁定当前错误行为的审查探针。修复后它应失败；正式回归测试应反向断言完整交付及正确失败状态，不能将该探针的“通过”加入产品验收指标。探针重新运行会更新同目录两份合成 HTTP 记录。
