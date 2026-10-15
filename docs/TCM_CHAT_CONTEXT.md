# 药材会话状态与字段追问

2026-10-16 更新：已改为本地SQLite成功轮次快照，可在保留Cookie和数据库后重启恢复，并可删除/过期。下方进程内状态描述为首轮交付记录，当前实现以 [持久记忆说明](PERSISTENT_MEMORY.md) 为准。

2026-10-15，接续 M1-05 聊天查询与 M0-02 状态规范化，新增受限问句解析和明确药材槽位。完整 State 重构、自然语言工具规划及持久记忆没有在本轮完成。

## 可用流程

```text
查询 BoHe 的科名
刚才那味药的来源在哪？
它的药用部位是什么？
/herb-clear
```

也可以先用 `/herb BaiBu` 或 `/herb <完整来源ID>` 选择药材，再追问字段。支持明确的“查询/查一下/查 + 名称 + 的 + 字段”句式，以及“它/这味药/刚才那味药/刚才的药材 + 的 + 字段”的追问。药名仍需使用已收录的拼音、拉丁名或英文名；中文字段词不等于中文药名映射已经完成。

字段词包括科名、药用部位（部位）、药性、药味、归经、产地、传统功效分类、适应证、文献ID/PMID、拉丁名、英文名、拼音和来源。“来源”追问显示最近一次字段查询的原值、文件、JSON Pointer、SHA-256；若此前查的是完整资料，则显示该记录各字段的出处。字段查询保留名称和所问字段，缺失值明确标为“来源未提供”，不补造内容。

这是规则支持的句式范围，不代表能理解任意改写、比较、筛选、文献判断或治疗问题。未匹配的普通请求沿用原应用路由。

## 状态与失败行为

| 条件 | 行为 |
|---|---|
| 唯一记录查询成功或通过来源ID选择成功 | 保存来源ID、数据版本、文件SHA-256和最近所问字段 |
| 同名未选择 | 标为待选择；追问返回 `clarify_source`，不沿用更早的药材 |
| 新会话无选择 | 返回 `clarify_entity`，要求先提供药名或来源ID |
| `/herb-clear` | 清除槽位，返回 `context_cleared` |
| 查询失败、中文名未映射、无效指令 | 清除旧选择，后续追问不能退回上一味药 |
| 普通聊天或图片请求 | 清除药材焦点；再次追问需重新指定药材 |
| 所选来源内容变更、消失或部署版本配置改变 | 返回 `context_stale`，清除选择并要求重查 |
| 数据库不可用 | 返回既有503错误并清除选择，不向模型回退 |

追问重新按来源ID读取数据库，并固定选择时的数据版本；同一版本的文件校验和变化也会拒绝沿用。未指定部署版本时，即使有更新批次，追问仍读取原先选择的版本。显式重新查询才会选择新的记录/版本。

槽位保存在当前会话的 LangGraph checkpoint。每轮仍重置临时的 `tcm_request/tcm_result`，仅槽位跨轮保留；来源原文不进入模型消息历史。精确查询及这些受限追问都不调用模型、嵌入或Tavily。

仍使用进程内 MemorySaver，重启失去状态，多工作进程不共享会话。不同cookie会话的隔离检查不等于账号认证或会话所有权验证。没有实现持久记忆、账号系统、跨设备恢复或任意代词理解。

## 原始候选的实际执行

没有修改 `eval/tcm_task_candidates_v1.json`。两条候选在冻结文件中的 `unsupported/planned_chat` 是原设计基线，本轮以真实主应用 `/chat` 路由检查其中原先记录的 `future_target`：

| 候选 | 原始输入 | 实际机器检查 |
|---|---|---|
| MULTI_STEP-04 | 同会话“查询 BoHe 的科名” → “刚才那味药的来源在哪？” | 两轮均返回原目标来源ID、`Labiatae`、同一字段指针与文件SHA，第二轮确为追问 |
| MULTI_STEP-05 | A：“查询 DangGui 的科名”；独立B：“它的部位呢？” | A实际完成药材定位；B要求澄清，不出现A的来源ID |

执行器使用独立cookie客户端，保留原始问句、逐轮结构化响应和耗时；禁止模型与外网调用。没有将结构化独立API响应替代聊天运行。3项评测器故障测试证明错误字段值、实体、出处、缺失轮次、首轮HTTP500或跨会话泄漏不会被判通过。

历史覆盖58题加本轮2题，累计60条候选都执行过某种接口/页面检查；这不是同一代码版本对60题的完整重跑，更不是60/60业务成功。原9条partial、科学解释未核验和人工待签核状态保留，真实用户题仍为0，任务完成率保持null。见 [累计覆盖](../audits/tcm_chat_context/coverage.json)。

## 验证与复现

- 工程回归 **119/119**，15模块，零跳过；包含7项新增真实图/来源变更测试和3项评测器故障测试。
- 真实Chromium：合成来源 **11项**，实际快照 **10项**；包括跨轮字段、出处、歧义选择、清除和独立浏览器会话。
- 原冻结验收 **30/30**，数据库SHA保持 `0e11630a15fc2460ef253607840fac702a2e50c3a4a3839bcea0aed5b6e109ec`，候选集保持原SHA。人类签核仍未完成。
- 上述报告分别见 [离线回归](../audits/tcm_chat_context/offline_ci.json)、[候选逐轮记录](../audits/tcm_chat_context/candidates.json)、[合成浏览器](../audits/tcm_chat_context/browser_fixture.json)、[真实浏览器](../audits/tcm_chat_context/browser_snapshot.json)、[冻结集](../audits/tcm_chat_context/golden.json)。旧版本报告保留不覆盖。

```bash
python -m scripts.run_offline_ci --out /tmp/tcm-context-ci
python -m scripts.eval_tcm_chat_tasks --out /tmp/tcm-chat-candidates.json
python -m scripts.eval_tcm_golden --out /tmp/tcm-context-golden.json
python -m scripts.verify_tcm_chat_ui --server-python /path/to/server/python --out /tmp/tcm-chat-fixture.json
python -m scripts.verify_tcm_chat_ui --server-python /path/to/server/python --database var/tcm/herbs.sqlite3 --out /tmp/tcm-chat-snapshot.json
```

聊天候选执行器自动创建隔离配置子进程，不读取真实 `.env`，使用实际主应用和禁止调用的模型替身。浏览器依赖及部署方式沿用 [聊天查询说明](TCM_CHAT_LOOKUP.md)。本轮未在Docker中验证，也未发布Docker Hub镜像。

下一步优先整理人工复核与真实问题采集，核对现有9条partial及来源语义，再决定中文名称映射、文献证据或模型规划的优先级。当前只能证明工程流程与来源定位通过；需要真实任务和方案对照，才能判断聊天编排相较独立查询页面是否带来收益。
