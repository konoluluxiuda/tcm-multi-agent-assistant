# 请求诊断链

诊断阶段只增加观察记录，没有修改提示词、名称目录、校验通过条件或工具能力。后续[第一版业务修复](../audits/repair_attempt_v1/README.md)另行记录，不覆盖诊断原始返回和旧分数。诊断库不参与任务执行、队列幂等或恢复决策；记录失败不会触发模型重试。

## 查看一个新请求

先打开聊天页或资料任务页初始化会话。在浏览器开发者工具的Network中找到`POST /chat`或`POST /api/dataops/research-questions`，复制响应头`X-Request-ID`。这是服务端生成的32位编号，不接受客户端伪造值。

同一浏览器会话访问：

```text
/api/dataops/diagnostics/替换成32位请求编号
```

接口需要原会话Cookie；其他会话返回404，未登录会话返回401。响应为`Cache-Control: no-store`。不需要为排查再次提交问句或再次调用模型。

本机管理员也可直接读取已有状态。把示例中的请求编号替换为实际值，`--root`指向该实例的`DATAOPS_ROOT`，通常是`STATE/dataops`：

```bash
.venv/bin/python -m scripts.inspect_request_diagnostics \
  --root var/dataops_question_demo/dataops \
  --request-id 替换成32位请求编号 \
  --out var/diagnosis/request.private.json
```

默认终端仅打印阶段、状态、原因码；`--out`以0600权限保存脱敏详情，已有文件不会覆盖。CLI具有本地管理员的文件访问权限，不能当作不需要鉴权的远程接口。

## 记录内容和关联

| 阶段 | 可核对内容 |
|---|---|
| 请求/路由 | 当前问句、服务端请求编号、选中的路由；请求key仅记录hash |
| 名称与来源 | 本次实际匹配的名称/来源候选、父版是否绑定、数据发布指纹 |
| 本地理解/策略 | 本地解析结果，是否进入兜底，是否重放缓存 |
| 模型输入 | Prompt和工具Schema的SHA256、Schema profile、工具名称和允许名称 |
| 模型返回 | 解析后的工具名、参数、usage；在业务校验之前记录 |
| 参数/需求校验 | 明确拒绝码、字段位置、对象集合差异、项目代码位置及文件hash |
| 字段归一化 | 模型提交字段与宿主最终字段，是否加入默认列 |
| 入队 | 工作区、版本、job ID、已知忙碌或派发失败 |
| 后台执行/交付 | driver、节点、动作参数、最新工具观察、文件hash与执行状态 |

后台任务通过独立诊断库中的工作区关联重新取得原请求编号；不把诊断字段塞进队列payload，因此同key重试不会因诊断编号变化而改变队列绑定。长生命周期worker会重置继承的请求上下文，避免后续任务误挂到第一个用户的轨迹。

开启Langfuse时，`langfuse_trace_id`与请求编号相同；理解和后台执行在同一trace中，所有观察也带`diagnostic_request_id`。本地入口为`http://127.0.0.1:3037/`。此字段是关联标识，不保证导出成功：7914默认关闭Langfuse，只保留本地诊断；7896/7912按原配置导出。导出不可用不阻塞执行。本轮实际SDK回读验证了合成拒绝和交付的完整关联。

## 如何判断原因

沿时间线先看路由和名称，再看模型工具参数、需求校验、执行观察。首个`needs_input`可能只是本地解析失败，随后已被模型成功补充；不能把第一个非成功事件直接当作最终根因。

`complete`仅表示执行器完成当前目标。交付事件明确记录`business_acceptance=not_evaluated`，不自动宣称满足用户原始需求；应结合独立验收合同和输出检查。本轮未为旧评测改分。

典型原因码包括`mentioned_object_omitted`、`unknown_alias`、`query_entity_not_grounded`、`pair_requirement_coverage_failed`、`filter_contract_invalid`和`schema_validation_failed`。未分类异常只记异常类型及项目代码位置，不记录任意异常文本、栈局部变量。代码位置附文件hash，后续修改时应按对应版本核对。

三条旧失败问句的新诊断重放及具体触发点见[本轮报告](../audits/diagnostic_chain_v1/README.md)。这是新观察，不能断言历史那次返回完全相同。

## 保存范围

诊断库位于`DATAOPS_ROOT/diagnostics.sqlite3`，默认保存30天、每owner最多1000个请求、每请求最多256个事件；内容有大小和深度限制。已知密钥、鉴权字段与隐藏推理字段会脱敏；无法解析的工具JSON只记录hash和长度。诊断文件和会话Cookie不应上传Git。

Langfuse内容导出沿用`TCM_TRACE_CONTENT`开关；关闭时不导出输入输出正文。本地诊断仍保存受限的脱敏参数。未初始化会话、鉴权前拒绝或业务尚未绑定owner的请求可能只有HTTP日志/可选trace，没有可经会话读取的本地记录。历史没有保存的模型返回不会补造；原已消费的评测目录不能复跑。

7916保留上一轮`external-clean-v1`复现快照，不属于本轮诊断部署。新增观测保持原有业务局限，用同一旧题得到更具体原因不等于任务能力提升。


名称/兜底阶段新增了公开`failure`原因结构和工作台排查编号。真实请求已区分名称边界误拒、单属性Schema误拒及规则先行解析错误；原始参数和首测保留，见[实例与验收](../audits/name_resolution_v1/README.md)。

2026-11-01补齐普通药材/药典查询：执行后的失败信息同时位于聊天顶层`failure`与对应`tcm.failure`/`research.failure`，含固定的`code/stage/message/automatic_retry`。同一请求的`query_failure`事件保存原因码和结构，配合`routing`和`query_execution`核对来源及结果；不从空的中间药名槽位推测解析器出错。范围、示例及16次HTTP验证见[本地查询修复](../audits/local_query_repairs_v1/README.md)。鉴权拒绝等执行前错误仍按原HTTP接口返回，不保证带此结构。

提案修复阶段增加`name_grounding`，记录宿主验证方法和字面位置，不等于药材身份已人审。本地诊断的脱敏深度从8改为16，常规`analysis_plan.steps[].call.arguments`可以完整回读；字符预算仍为48,000，超限仍明确截断。`tool_calls_sha256`针对脱敏前SDK解析调用结构，不能当供应商原始响应hash；也不能用脱敏后的含密钥结构强行重算。Langfuse导出保持默认较浅上限，未做本轮服务端回读。

同一进程对同路径诊断事务使用有界路径锁，原SQLite跨进程协调和best-effort降级保留。24个并发初始化请求及原并发owner隔离回归通过，不表示任意负载、跨进程或分布式下不会丢记录。旧已截断参数不会因修复补全；[验收及边界](../audits/proposal_validation_repairs_v1/README.md)另行记录。


## 统一理解 V2 的诊断

`model_input/model_return` 新增 `phase=initial|correction|semantic_review`。`understanding_correction` 记录已收到响应后的纠正原因，`semantic_validation` 记录本地合同或独立复核的比较依据及差异字段。参数校验通过后仍可能被语义核对拒绝，应以最终 `understanding_result` 为准。

`external_model_calls` 和 `usage` 累计最多两次，任务 `goal_understanding` 同时保存策略、需求合同、复核依据和阶段。同key重放显示本次新增0；超时属于未知状态，不把已尝试请求算为0或自动纠正。复核与纠正共享一次追加额度。Langfuse模型观察也带phase，但本轮未新增服务端回读验收。见[实现与测试](../audits/understanding_v2/README.md)。
