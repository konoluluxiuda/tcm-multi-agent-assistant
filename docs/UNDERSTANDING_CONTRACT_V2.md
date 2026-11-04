# 统一理解链 V3 设计：需求契约、能力矩阵与校验分界

> 状态：设计稿（2026-11-01）。目标是把“理解→校验→错误反馈”重构为单一链路：
> `问句 → 统一模型理解 → Requirements 契约 → 机械校验(只读契约) → 有限纠错 → 工具/交付`。
> 本稿不改运行代码，先冻结结构与验收口径。

## 1. 核心原则

1. **理解只产出结构化契约，不再产出“命令字符串”。** 模型只回答“用户要什么”，不回答“该调哪个工具”。工具计划由契约经能力矩阵机械派生。
2. **校验只读契约，绝不回读原句。** 任何需要“读原句”才能做的判断都不属于机械校验。重复语义判定是已定位的一类失败原因；资料覆盖、能力缺口和运行状态仍需分别处理。
3. **能力矩阵是“能查/能交付”的唯一真值。** “能查成分”不等于“能把成分放进表格单元格”，由矩阵一锤定音。
4. **语义判断只能是有界复核，不是正则证明。** 两次模型意见一致是“一致性证据”，不是正确性证明；不一致或不确定→澄清。

## 2. 需求契约 Requirements（唯一结构）

在现有 `tcm/dataops/understanding_contract.py:Requirements` 基础上扩展，作为理解与校验之间的唯一接口：

```text
Requirements {
  objects:   [{name, source_ids[], identity_verified}]   # 对象已绑定到目录候选
  fields:    [{namespace: etcm|pharmacopoeia, name}]      # 带命名空间，杜绝“性味”歧义
  row_policy: keep_all | filter_complete | top_n          # 三选一，显式枚举
  count:     int|null                                      # 仅 top_n 用
  output:    answer | table
  operation: new | append_fields
  monograph_only: bool
  filters:   [{field,op,value}]                            # 仅 ETCM 基础属性
  unresolved: [string]                                     # 未解决要求，非空则不入队
}
```

关键点：
- **`row_policy` 是显式枚举**，直接对应语义差异：“全部保留(keep_all)”“只留齐全(filter_complete)”“挑前 N 条(top_n)”。这三者语义不同，不能再靠正则从原句猜。
- **`fields` 带命名空间**，机械区分 `etcm:性味`(药性+药味) 与 `pharmacopoeia:性味与归经`，消除“性味”映射歧义。
- **`unresolved` 非空即阻断**：模型不能“只执行支持的半句”，未解决要求必须显式保留。

## 3. 能力矩阵 Capability Matrix

一张静态表回答“某字段在某交付形式下是否可用”。校验器只查这张表，不再调用 `relation_tasks()`/`field_tasks()` 重新推导。

```text
capability(字段, 交付形式) -> allowed | denied(reason)

字段示例 × 交付形式：
  etcm:medicinal_part     answer✓  table-cell✓
  etcm:family             answer✓  table-cell✓
  etcm:ingredients         answer✓  table-cell✗(仅查询/关系)  relation✓
  etcm:targets             answer✓  table-cell✗(仅查询/关系)  relation✓
  pharmacopoeia:功能与主治  answer✓  table-cell✓  pair-report✓
  pharmacopoeia:贮藏        answer✓  table-cell✓  pair-report✓
```

用途：
- 模型提案要求“把成分放进表格”而矩阵 `table-cell=✗` → 直接返回可反馈的机械错误“成分支持查询，不支持作为表格单元格”，而不是正则拒绝。
- 这张表同时是工具注册说明、参数约束、自然语言规则与输出能力的唯一维护点（消除当前多处维护点）。

## 4. 机械校验 vs 语义判断的分界

| 类别 | 判据 | 例子 |
|---|---|---|
| 机械校验(程序可证) | 不读原句即可判定 | 对象ID在目录、字段在枚举、row_policy 合法、count 在 1..5、父版本未换对象、预算、权限、矩阵可交付 |
| 语义判断(有界复核) | 需判断“是否与用户意图一致” | “作用”到底指功能与主治还是全量属性；“挑两条”是否等于 top_n |

**机械校验不通过 → 反馈具体错误码+字段路径，最多纠正一次。**
**语义判断不确定 → 澄清，不创建任务。**

## 5. 当前校验器“回读原句”的位置（重构切点清单）

以下位置仍在用正则/局部解析器回读原句，是重构要替换的目标：

| # | 位置 | 现状 |
|---|---|---|
| 1 | `research_question.py:181` `explicit_requirements(question,catalog)` | 正则扫原句字段别名 |
| 2 | `research_question.py:189` `mode_constraints(question)` | 正则扫原句数量/保留/缺失 |
| 3 | `research_question.py:139` `all_complete_request(question)` | 正则扫“哪些…都有记录” |
| 4 | `research_question.py:200-216` | 正则扫 monograph/否定/来源/“未要求属性” |
| 5 | `research_question.py:419-437` `_request_once` | `explicit_requirements` + `grounded(n,question)` 再次回读 |
| 6 | `research_plan_intent.py:44-125` `validate_question` | `grounded()`/`relation_tasks()`/`field_tasks()`/计数/页码 整段回读 |
| 7 | `research_question.py:523-529` `request_proposal` | 用 `local_proposal(question)` 重新解析原句比差异 |
| 8 | `request_policy.py:27-47` `needs_fallback` | “看似完整的 regex route”决定是否绕过模型 |
| 9 | `research_question.py:253-303` `local_proposal` | 整段本地语法回读 |

**重构目标：删除 1–9 的“回读原句”校验，改为只校验 `Requirements` 契约 + 查能力矩阵。**

> 实施进展复核（2026-11-01）：`semantic_guards`仅集中了一部分会拒绝提案的正则约束，并非提示。`SEMANTIC_REGEX_GUARDS=0`仅跳过该函数，查询检查、preflight和local_proposal仍读取原句。Requirements仍是ResearchDraft/QueryIntent之后的投影，能力矩阵尚未接入；本稿目标尚未全部实现。旧CI“全部通过”记录已更正。新门禁检查拒绝原因和阶段，覆盖查询、表格及父版本，并将关闭守卫作为预期失败的消融检查；详见[后续修复](../audits/understanding_gate_fix_v1/README.md)。

## 6. 有限纠错反馈设计

- 反馈内容只含：错误码 + 字段路径/类型 + 允许范围（例如“字段 `xxx` 不在枚举 `[...]`”）。
- 不回传：原句解析、stack、源ID、模型首轮提案全文。
- 预算：首理解 + 一次纠正，或 + 一次独立复核，共 2 次上限；纠正与复核不能各用一次。
- 可修复错误码（机械可证）：`schema_validation_failed`、`field_requirement_mismatch`、`filter_contract_invalid`、`mentioned_object_omitted` 等；`语义不确定`、`资料不存在`、`权限不允许`、`派发状态未知` 分别处理，不统一重试。

## 7. 反例验收口径（对应 `eval/understanding_v2/counterexamples.json`）

验收必须同时测两侧，缺失任一侧都不能算通过：

1. **正例（正确提案能执行）**：正确理解的对象/字段/行策略/数量 → 机械校验放行。
2. **反例（错误提案必须拦截）**：
   - “整理三味药的功效、注意” → 提案变成“只返回齐全药材”(row_policy 悄悄变 filter_complete) 必须拒。
   - “只整理功效、注意同时有记录的” → 提案变成“保留全部”(keep_all) 必须拒。
   - “增加贮藏” → 提案变成“删除/替换旧列” 必须拒。
   - “有什么作用” → 提案变成“ETCM 全量属性 dump” 必须拒/纠正为“功能与主治”。
3. **同义改写**：`只留齐全`/`挑齐全的`/`筛出都有记录的` 应归一化到同一个 `row_policy`。
4. **改变含义用例**：`全部保留↔只选齐全`、`增加↔删除`、`作用↔全量属性`、`挑前2条↔核对全部`。

分别记录：理解正确率、宿主误拒、错误提案放行、执行完成率、纠错成本。
