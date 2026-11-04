# 需求契约先行改造：U1–U4 实施记录（2026-11-01）

> 状态：U1–U3 及 U4 的契约→ResearchGoal 适配器已完成并有离线测试；**完整服务接线与 ASGI 全链验收尚未完成**。本记录区分“已实现”与“仍待接入”，不把契约/规划层测试通过写成完整链路完成。`live_model_evaluated:false`，全程未发起真实模型调用。

## 1. 实际变更清单

| 文件 | 操作 | 职责 |
|---|---|---|
| `tcm/dataops/requirements_v2.py` | 新增 | `requirements-v2` 契约：`RequirementsDraftV2`（判别联合 `read_fields/relation/dataset`）、`BoundRequirementsV2`（宿主绑定）、严格 schema |
| `tcm/dataops/capability_matrix.py` | 升级 | 从静态清单升级为 54 项 `Capability` 注册表（工具/对象数/运行依赖/分页），`resolve()` 抛安全 `deny_reason` |
| `tcm/dataops/requirements_planner.py` | 新增 | `bind_requirements` / `validate_requirements` / `compile_plan` / `validate_plan_coverage` / `to_research_goal`；机械规划不接收 question |
| `tcm/dataops/requirements_understanding.py` | 新增 | `submit_requirements_v2` 工具 + prompt + `request_requirements`（2 次预算：首理解 + 一次纠错或一次独立复核） |
| `tcm/dataops/requirements_pipeline.py` | 新增 | `run_requirements_v3` 全链编排 + 诊断阶段 |
| `tcm/dataops/service.py` | 修改 | `requirements_v3` 受开关保护的分派分支；幂等签名纳入新模块指纹 |
| `tcm/dataops/understanding_contract.py` | 修改 | `requirements_v3` 策略值 |
| `tcm/capabilities.py` | 修改 | `requirements_v3` 的 2 次请求上限 |
| `tests/test_requirements_v2.py` | 新增 | 契约 20 项 |
| `tests/test_requirements_planner.py` | 新增 | 规划/覆盖 9 项 |
| `tests/test_requirements_understanding.py` | 新增 | 2 次预算 SDK 替身 7 项 |
| `tests/test_requirements_adapter.py` | 新增 | 契约→ResearchGoal 适配 4 项 |
| `tests/test_requirements_pipeline.py` | 新增 | 全链编排/answer 路由 4 项 |
| `tests/test_requirements_v3_http.py` | 新增 | ASGI 全链（table 交付→XLSX + 幂等重放）2 项 |

未改动：`service.py`、`request_policy.py`、`research_question.py` 的既有分派；默认策略仍是 `local_then_one_model_v1` / `unified_v2`。

## 2. 契约与编译接口

```text
问句(仅理解阶段) --submit_requirements_v2--> RequirementsDraftV2
  --bind_requirements(目录快照, 父元数据)--> BoundRequirementsV2
  --validate_requirements(能力注册表)--> ValidationResult
  --compile_plan(能力注册表)--> CompiledPlan
  --validate_plan_coverage--> CoverageResult
  --to_research_goal(table)--> ResearchGoal(现有交付引擎)
```

- 覆盖检查粒度：`(requirement_id, object_ref, namespace, name)`；换对象、换命名空间、删步骤、加无关步骤均判失败。
- 能力矩阵已接进 `validate_requirements`（如 `etcm:ingredient` 作为 `table_cell` 拒绝为 `deny_reason`）。
- 机械规划函数签名不含 `question`，不读全局原句。

## 3. 离线测试结果

本轮新增 40 项测试全部通过，0 联网尝试、无真实模型调用：

| 测试模块 | 数量 | 覆盖 |
|---|---|---|
| `test_requirements_v2` | 20 | 严格 schema、未知键、对象引用、重复需求 ID、数据集无药名、父版本、needs_input 语义 |
| `test_requirements_planner` | 9 | 绑定、字段/关系校验、成分进表格拒绝、逐对象覆盖、删步骤/换对象/加步骤 |
| `test_requirements_understanding` | 7 | 接受、纠错、复核分歧、预算耗尽、未知派发、无授权、schema 严格性 |
| `test_requirements_adapter` | 4 | keep_all→audit_named、top_n→select_complete、filter_complete、answer 拒绝 |

配合既有 `test_understanding_v2`(21)、`test_understanding_gate`(11) 共 61 项一起运行通过。全仓离线 CI 最终结果见 `acceptance.json`（源码快照、命令、退出码、模块/项数、网络尝试、模型请求数）。

## 4. 两条完整示例轨迹（契约→计划；未执行真实工具）

**轨迹 A：跨源字段查询（黄芪药用部位 + 功效）**

```text
问句(语义阶段) -> submit_requirements_v2 ->
  RequirementsDraftV2{
    objects:[{ref:o1,mention:黄芪}],
    requirements:[
      r1 read_fields [o1] etcm:medicinal_part,
      r2 read_fields [o1] pharmacopoeia:功能与主治
    ],
    delivery:{form:answer, include_evidence:true}, unresolved:[]
  }
-> bind_requirements(黄芪 -> [src]) -> BoundRequirementsV2(identity_verified:false)
-> validate_requirements -> ok
-> compile_plan -> [s1 herb_attributes(medicinal_part, covers r1), s2 pharmacopoeia_lookup(功能与主治, covers r2)]
-> validate_plan_coverage -> ok（两条需求各自有覆盖，换对象即缺项）
```

**轨迹 B：表格追加（父版本保留）**

```text
问句(append_fields) -> RequirementsDraftV2{operation:append_fields, delivery:{form:table,row_policy:keep_all}}
-> bind_requirements(parent 元数据绑定 parent_workspace_id/revision)
-> to_research_goal -> ResearchGoal{query.names=原对象, document_fields=并集, research.mode=audit_named}
-> （待服务接线）复用现有父子作用域校验与交付，旧文件 hash 不变
```

注：轨迹 A/B 的契约→计划部分由单元测试断言；到 XLSX 的真实交付与旧文件不变的断言属于 ASGI 全链验收，**尚未完成**。

## 5. 剩余风险与未完成项

1. **answer 交付查询链未接线**：`to_query_routes` 已实现并单测（把 `CompiledPlan` 映射为 herb/research/assistant 路由），`run_requirements_v3` 对 answer 交付也返回 `query_routes`，但 `DataOpsService` 路径对 answer 交付仍返回澄清——answer 查询的实际执行仍走既有聊天查询链，尚未让新策略接管。
2. **预算细节**：`request_requirements` 当前对 ready 结果一律做一次独立复核（2 次），尚未实现“首轮有完整本地语义依据则 1 次”的优化；`SEMANTIC_REGEX_GUARDS` 与旧 `query_coverage`/`validate_question` 的关系未在新策略里显式处理。
3. **CLI 未暴露**：`preview_local_tasks.py` 尚未增加 `--understanding-policy requirements_v3` 选项；三端口仍跑 `local_then_one_model_v1` / `unified_v2`，未重启。
4. **真实模型未评测**：`live_model_evaluated:false`；40 条未见问句、基线对照、误拒/放行率、端到端完成率均未做。
5. 本轮未提交/推送 Git，未构建/发布 Docker。

## 6. 下一步

按交接说明第 7/8 节完成 service 接线、answer 路由桥接、诊断阶段与 ASGI 全链测试；随后由独立 Agent 另开上下文准备 40 条未见问句做真实模型对照（另需授权与预算，本轮未发起）。
