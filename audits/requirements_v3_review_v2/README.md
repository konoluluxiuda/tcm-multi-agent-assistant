# Requirements V3 修复复核（2026-11-02）

结论：本轮确实修复了上一轮多项问题；但“全部 P1/P2 已修复、目标完成”仍不成立。**应先关闭下列实现缺口，再扩大 answer 接线或真实模型评测。** 本记录不否认已通过的测试，也不把仍未上线的新路径问题描述成当前线上事故。

本轮仅代码审查、离线 SDK 替身与临时数据库/ASGI 探测；没有修改业务代码、重启服务、真实模型调用或推送发布。保留上轮原始探测和回执，没有覆盖旧记录。

## 1. 证据与确认已修项

- 读取 `var/requirements_v3_ci5/summary.json`：历史全仓 CI 为 passed，1699 项、零跳过、零联网尝试。本轮未重新运行整个仓库。
- 本轮重新跑相关 8 个模块：**100 项通过**（26＋19＋12＋4＋4＋3＋21＋11），隔离 worker，零跳过、零联网尝试。
- 用旧观察脚本重新核验 23 项，并增加 8 项有针对性的探测。旧观察中出现异常不统一判失败：例如现在正确拒绝缺 count 等，就是预期改善。脚本 exit 0 仅表示探测完成，不代表系统通过所有验收。
- [verification.json](verification.json) 记录模块结果和选定源码前后哈希，检查期间无变化；[probe-results.json](probe-results.json) 保留实际响应；[probe.py](probe.py) 可复现。SDK 替身不代表真实模型效果。

已确认有效：

| 旧问题 | 本轮复核 |
|---|---|
| 无对象 needs_input、混合 dataset/药材 schema 崩溃 | 现在可通过 schema；注意后续混合/关系计划仍有下述新异常 |
| 重复对象 ref、ready 空需求、缺 count | 现在拒绝 |
| 第二轮无效响应少计 | 实际 2 次、报告 2 次，24 token 均计入 |
| 复核超时被吞 | 现在抛 TimeoutError；完整 HTTP 未知回执仍需生命周期验收 |
| 纠错后错语义直接 ready | 现在 needs_input / semantic_review_budget_exhausted |
| o1/r1 与 o9/r9 引起误拒 | 相同语义现在 ready |
| 候选、父字段、纠错详情未发给模型 | 本轮捕获消息中均存在；不等于能力说明和父作用域已经完整 |
| 显式本地模式 500 | HTTP 200，模型客户端创建 0 次，invoke 0 次，澄清 |
| 旧覆盖突变、简单追加字段合并 | 对应旧反例现在有拦截/合并；仍有下述覆盖和作用域漏洞 |

## 2. P1 新回归：关系/数据库需求通过不了覆盖阶段

位置：`tcm/dataops/requirements_planner.py:215`。

去重键是 `(step.tool, tuple(sorted(step.arguments.items())))`。关系参数 `object_refs`、数据库参数 `datasets` 都含 list；外层转成 tuple 不会让内部 list 可哈希。第一次执行 `key in seen_args` 就抛：

```text
TypeError: unhashable type: 'list'
```

已用两次一致的 SDK 提案跑完整 `run_requirements_v3`，分别对 target 关系与 dataset 介绍复现。普通标量字段测试通过不能覆盖这类参数。

现有 `test_common_target_uses_distinct_tool` 只做 compile→to_query_routes，没有调用 `validate_plan_coverage`，所以绕过了正式 pipeline 中的报错。

修复要求：用稳定 JSON/canonical 序列化或递归不可变值生成去重键；保留必要顺序，不用字符串 repr 掩盖语义差异。增加关系、数据库、混合需求的完整 pipeline 正例；不能只检查最后路由字典。

## 3. P1 未完整修复：已知但错误的工具仍通过覆盖

位置：`requirements_planner.py:138`、`:202`。

`KNOWN_TOOLS` 只能拒绝不存在的名称。`_step_items` 对 herb_attributes、pharmacopoeia_lookup、dataops_table 使用同一套参数解释，不检查当前字段/namespace 是否属于该工具。

复现：原本正确的“黄芪药典功能与主治”计划，把工具从 pharmacopoeia_lookup 改为 herb_attributes，其他参数保持不变：

```text
validate_plan_coverage → ok:true
to_query_routes → herb(name=黄芪, field=功能与主治)
```

这不是合法 ETCM 属性查询。校验“工具存在”与“工具能执行这项需求”不同。

同时，实际覆盖仍丢掉 requirement_id、scope；关系配对结构被展开为逐对象项，dataset 的具体数据集集合也没有进入覆盖键。

修复要求：能力注册、真实参数 schema 和动作所属命名空间一起校验；由合法动作推导覆盖，保留关系对象组/读取范围/数据集范围；再检查最终适配目标等价。补“从正确工具换成另一个已注册工具”的反例，不能只有 nonexistent_tool。

## 4. P1 未完成：all_pages 只是新加标志，现有执行器不支持它

位置：`requirements_planner.py:302`；`tcm/assistant_info.py:46`；`tcm/relation_queries.py:176`。

新的 assistant_etcm_relations 是本地适配器标识，最终仍进入既有 relation_queries.execute。该执行器按 page 计算 offset、每次读取 LIMIT=20，没有消费 all_pages。

用已有合成数据库实际执行 `靶点 Alpha`：

| 参数 | 总数 | 返回数 | next_offset |
|---|---:|---:|---:|
| page=1 | 26 | 20 | 20 |
| page=1, all_pages=true | 26 | 20 | 20 |

两份结果完全相同。因此“参数保留”已做到，“全部查询”尚未实现。answer 未接线时这是适配缺口；不能把未接线作为已修复的证据。

修复要求二选一：实现受预算和快照约束的分页读取、去重及完整性交付；或在新契约执行前明确拒绝暂不支持的 all。不应仅新增一个无人读取的布尔值。完整性验收使用超过一页的 fixture，断言实际返回/交付数量、原始位置和未完成原因。

## 5. P1 新语义漏洞：归一化忽略了关系配对

位置：`requirements_understanding.py:53`。

忽略内部编号是正确修复，但关系需求也被拆成单对象 set，导致以下不同任务 normalize 相同：

```text
提案 A：甲与乙共同靶点；丙与丁共同靶点
提案 B：甲与丙共同靶点；乙与丁共同靶点
```

本轮探测返回 `different_pairs_compare_equal:true`。独立复核将失去检测对象配对改变的能力。

修复要求：一条关系保留其整个对象组，先把 ref 转成宿主规范对象，再按关系是否对称决定组内能否排序；只对独立字段读取做按对象展开。保留需要比较的证据交付与排序含义。补关系配对变化反例、同一对对象交换顺序正例。

## 6. P1 未完成：默认筛选列和父作用域仍可能改变

位置：`requirements_planner.py:265` 至目标生成处；`requirements_understanding.py:75`。

字段并集修复确实保留了旧列，但父 query、research mode/count/monograph_only 没有继承或验证。已复现父目标为“黄芪＋白术、filter_complete、monograph_only=true”，append 子提案可生成“仅黄芪、audit_named、monograph_only=false”。这是适配器放行的变化，不表示已覆盖线上父文件；下层旧保护可能再拒绝。

此外，“只筛有功效的”仍默认加入 medicinal_part；研究执行器会把这列纳入 eligible 判断。默认药典功效/注意同样仍存在。这正是上一轮要求分开“附带展示列”和“用户筛选条件”的问题，没有随字段合并解决。

模型 parent 输入目前仅含旧字段，不含父对象和筛选策略；能力枚举/矩阵说明仍未进入输入。不能以“parent 已传”推断完整父需求已传。

修复要求：完整宿主父契约作为追加基线，仅允许字段扩展；显式变对象/变行策略需要独立操作或澄清。展示所需默认列不得暗中增加筛选要求；无法表达则具体返回 unsupported_delivery，不生成语义不同的目标。增加完整父子交付与旧产物不变断言。

## 7. P1 集成问题：可预期的表达缺口污染未知回执

位置：`requirements_pipeline.py:59`、`service.py` 新策略分支、`api.py:81`。

逐对象字段差异现在在适配器抛 ValueError，这是单元层面的拒绝。pipeline 没将其转换成可持久化的已知失败，service 在 journal.reserve 之后退出，未 complete 回执。

ASGI 复现“Alpha 功效注意、Beta 科名成表”：

1. 两次 SDK 提案后，首次 HTTP 409：“资料契约校验失败，工作区已保留失败状态。”
2. 同 request_key 再次请求，HTTP 409：“请求仍在处理或执行状态未知，未重复调用模型。”
3. 模型次数保持 2，没有重复付费；但本来已知的“不支持该表格表达”被留成未知状态。

修复要求：适配前可预期的表达缺口、范围超限、参数错误转换成具备 code/stage/message 的结果并完成提案回执。真正的传输结果未知继续保持未知，程序异常单独报错。新增 HTTP 首次已知拒绝→同 key 原样重放测试，不能只检查抛 ValueError。

## 8. 尚未接线项的准确口径

- 新 pipeline 的 answer 结果有 query_routes，但 service 新分支只接受带 goal 的 ready；其他返回澄清。**这不是自动退回旧查询执行链。** 旧聊天入口仍可执行既有查询，与新策略 answer 已接通是两回事。
- 生命周期、恢复、权限和真实模型未评测仍是明确未完成项。本轮不要求真实模型来证明以上程序问题，离线 fixture 已能复现。
- “0/多候选一律拒绝”修复也需按能力细分：纯药典查询不应无条件依赖唯一 ETCM 来源。宿主 owner/data_version/选中来源的传递和运行依赖检查仍应在集成验收中覆盖。

## 9. 建议下一轮交付范围

保持默认策略和现有部署，按一个完整修复批次处理：

1. 修列表参数哈希、工具与参数能力匹配、关系语义归一化。
2. 明确全量读取支持范围，完成最终路由的真实工具验收，或显式限制能力。
3. 修父作用域与默认筛选列语义；把已知表达缺口转成可重放结果。
4. 补关系/数据库/混合需求 pipeline、已知拒绝重放、父子与全量交付 ASGI 测试。
5. 再跑全仓，并更新 acceptance 的“已修/部分修/未实现”。此后再推进 answer 接线和未见问句对照。

这是在关闭上一轮已指出的接口契约问题，不是新增业务范围。不能把“新增字段”“新增工具名称”“单测抛错”直接等同真实工具行为已修复。

复现（项目根目录）：

```bash
.venv/bin/python - <<'PY'
import subprocess, sys
from scripts.run_offline_ci import test_environment
subprocess.run(
    [sys.executable, 'audits/requirements_v3_review_v2/probe.py'],
    env=test_environment(), check=True,
)
PY
```
