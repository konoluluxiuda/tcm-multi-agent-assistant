# ETCM任务工具注册与返回契约（2026-10-21）

本轮将研究任务引擎内的三个只读资料操作收敛到固定注册表，使确定性控制器和后续规划器可以复用参数校验、结果类型和错误处理。保留Python、LangGraph、FastAPI、Qdrant及DeepSeek路线，没有引入新框架。

## 调用链及实现范围

当前实际调用链：FastAPI任务接口 → 所有者检查和任务revision → `LocalRelationTaskEngine` → `LocalTaskTools.invoke` → 参数校验 → 任务快照检查 → 原有本地读取函数 → 再检查快照 → 类型校验 → 控制器保存状态和报告。

| 注册工具 | 输入 | 已有读取函数 | 输出数据 |
|---|---|---|---|
| resolve_sources | 本任务两味药的原始名称，必须与任务一致 | tcm.chat.execute_request | 来源候选分组和来源记录 |
| common_targets | 1–1000的整数页码 | tcm.relation_queries.shared_targets | 交集总数、页码、翻页信息和双方原始行 |
| source_references | 空对象；仅查询已完成后可选调用 | tcm.task_literature.reference_leads | 每侧最多5条本地PMID线索，不获取文献 |

共同靶点工具只使用服务器任务中已经确认的两份来源，不能通过参数指定其他来源、数据路径或URL。注册表不接收动态函数或导入路径。`finish_task`仍是控制器生成报告的内部动作，不开放成任意写工具。

这不是整个项目的全局工具迁移：聊天工具和原有合成原生tool calling引擎维持原样，MCP未实现，真实ETCM任务仍由确定性控制器执行。

## 返回和错误

`tcm/task_tools.py`提供严格Pydantic参数、来源分组、共同靶点见证、分页、PMID分组及`ToolResult`。来源元数据、原始JSON行和PMID候选保留开放内容字典，保留原值，不冒充已完成实体语义核验。

`ToolResult`包含`version=1`、工具名、`status=ok/empty/error`、类型化`data`、结构化`error.code`与工具耗时`elapsed_ms`。成功/空结果必须含数据且不能含错误，失败不能附成功数据。空结果仍须由控制器结合任务要求解释：来源未确定是partial；已确认两份来源且共同标识为零可以完成。

错误区分unknown_tool、invalid_arguments、invalid_state、unavailable、snapshot_changed、invalid_result。错误不携带原异常文本、SQL或私有路径。参数中的字符串页码、布尔页码和额外字段被拒绝。返回数据不满足类型或计数约束时不能落成成功结果。

控制器将错误映射回已有API行为：快照变化409、无效参数/状态422、资料不可用或返回不合规503。失败不保存新任务状态、不扣除已保存查询计数，也不写成空结果；未来若加入自动重试，需要另加尝试预算，本轮没有自动重试。操作仍需接口所有者和revision校验，注册表不是独立鉴权服务。

## 状态、兼容与成本

工具接收任务副本，不修改任务、报告或原数据。查询额度在实际读取前检查。现有动作观察内容保持不变；新增动作的`result`只保存结果版本、状态、错误字段和耗时，不再复制整份data，原始数据仍留在observation及报告。

历史任务不补写结果字段，已完成报告和下载不重算；待选择旧任务可使用新适配器继续，仍按原v1完成口径。无数据库迁移，异常类移到`tcm/task_errors.py`并从旧模块兼容导出。

耗时包含注册工具的校验及两次快照检查，不代表整条聊天延迟，也不证明业务效果提升。82模块725项完整离线回归通过，跳过与网络尝试均为0；最后对齐动作日志参数后，受影响的3模块33项复测通过。完整离线命令为`python -m scripts.run_offline_ci --out var/task_tools_v1/ci_final`。最终17项浏览器检查通过，双方2条原始行与2处PMID锚点与本地JSON核对一致。已知真实来源操作验收结果见`audits/task_tools_v1/summary.json`；独立真实用户验收仍未完成。

## 使用、回滚与下一步

源码预览：http://127.0.0.1:7877/research-tasks 。界面操作保持不变：两味药 → 确认来源 → 分页选择靶点 → 查看双方原始行和下载。

回滚需要同时恢复`research_task_local.py`、`research_task_api.py`和新增工具模块至本轮前版本；无需改写任务库。旧页面会忽略新增结果元数据，旧代码读取任务时也应保留原字段，禁止批量删除历史动作。保留上一轮v1/v2完成口径兼容约束。

下一项是有界TaskPlan与待澄清计划状态，接入前应先用合成资料验证；随后再评估确定性规划和模型规划是否共用这些工具。注册表提供schema不代表已经启用真实资料模型调用，当前不向模型发送ETCM、药典或文献片段。

本轮不提交Git、不push、不构建或上传Docker。Docker引擎当前无可用Unix套接字，7877仅为源码预览。
