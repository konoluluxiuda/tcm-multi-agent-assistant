# 第二轮修复的验收复核（2026-11-02）

结论：上一轮列出的主要反例已修复，已有可复验进展；但仍有 **父子范围冲突静默执行** 和 **实际计划与交付契约覆盖不完整** 两类阻塞项。不是重新要求全量读取或新业务，也不要求现在做真实模型试验。

本轮只审查与离线验证，未修改业务代码、重启服务、调用真实模型或提交发布。原两轮审计保留不变。

## 1. 验证事实

- 历史全仓回执 `var/requirements_v3_ci7/summary.json`：passed、1702 项、零跳过。此次只核对该回执，未重跑全仓。
- 本轮复跑相关 8 模块：**103 项全部通过**，零跳过、零联网尝试；汇报中的 102 是加总错误：26＋20＋12＋4＋4＋5＋21＋11＝103。
- 复用前两轮观察函数，结果写到新目录；新增 4 个边界探测。见 [probe-results.json](probe-results.json)、[复现脚本](probe.py)、[测试及源码快照](verification.json)。选定源码运行前后哈希一致。
- 测试全部为 SDK 替身、临时 SQLite/ASGI/文件产物。没有外发资料，也没有测量真实模型成功率。

### 已关闭或明确收窄的旧问题

| 检查 | 结果 |
|---|---|
| 关系、数据库 pipeline 的列表哈希崩溃 | 两类都能进入 ready，不再 TypeError |
| 药典工具误换成 herb_attributes | 覆盖校验明确拒绝 namespace 不符 |
| completeness=all | 新 pipeline 在执行前 needs_input，无 query_routes；属于明确未支持，不声称已经读取全量 |
| 语义 normalize 的关系配对 | 不同配对现在比较不相等 |
| 父 query/research 保持 | 适配器现在沿用父对象和 mode/count/monograph_only |
| 筛选模式隐式默认列 | 单来源字段筛选改为明确拒绝；这是能力限制，不是单来源筛选已支持 |
| 已知适配拒绝→同 key | 首次与重放均 HTTP 200、相同 unsupported_delivery，实际模型次数维持 2，不再未知回执 |
| 正常父子追加 | 新增 ASGI 用例通过：字段合并、父对象保留、旧产物字节不变 |

旧执行器单独收到 all_pages 仍只读第一页是已知现状；本轮新策略在入口阻断 all，因此不把这个旧执行器现象再次列为“修复失败”。

## 2. P1：父范围继承前未检查显式冲突，完整请求会完成错误对象

位置：`tcm/dataops/requirements_planner.py:288`，pipeline 到最终 goal 的适配路径。

新代码用父 query/research 覆盖子目标，但没有检查子提案明确指定的对象、策略是否与父目标冲突。保留父文件与保留用户含义是两项不同约束。

### ASGI 复现

1. 建立并完成父表：Alpha 与 Beta 的功效、注意。
2. 用该父表提交“给 Gamma 增加贮藏，保留旧字段”。SDK 替身两次都返回 Gamma 的 append_fields 契约，隔离验证宿主是否会挡住冲突。
3. 服务返回 HTTP 202、proposal ready。
4. 子工作区最终 status=complete。
5. `goal_understanding.requirements.objects` 为 Gamma，但实际 `goal.query.names` 为 Alpha、Beta。

这是本轮实际完整链路复现，非仅构造适配器输出。未调用真实模型，不能推断真实模型出现该提案的频率；但宿主本应无条件拦住这种范围冲突。

### 修复要求

- 在继承前执行显式的父→子需求转移校验：append 的明确对象必须与父作用域兼容，不能先覆盖再校验覆盖后的结果。
- 未显式改对象的“增加贮藏”可以从宿主补齐父对象；显式要求 Gamma、只改父对象子集、改变行策略/数量等，若现有全表追加无法表达，应返回具体澄清，不执行另一组对象。
- 区分字段增量与完整子目标；用最终 ResearchGoal 再验证已接受需求覆盖，确保记录的 requirements 与实际 goal 一致。
- 复用已修复的 known rejection journal 路径，让冲突同 key 重放相同结果、无新模型请求、无子产物、父产物不变。

必要回归：正常继承父对象仍成功；指定父外对象、只改一部分父对象、显式改筛选策略分别阻断；断言最终 goal 对象与已接受契约一致，不能只断言 goal 等于 parent。

## 3. P1：工具名与 namespace 对齐后，交付形式和关系组仍未校验

位置：`requirements_planner.py:156`、`:221` 之后覆盖逻辑及 `to_query_routes`。

### 3.1 answer 计划可被替换成 table 工具并静默变空

对“黄芪功能与主治”的正确 answer 计划，仅将 step.tool 改为 `dataops_table`，参数和 covers 保持不变：

```text
validate_plan_coverage → ok:true
to_query_routes → []
```

dataops_table 接受两个 namespace，因此新检查不拒绝；answer 适配器不处理这个工具，也没有报错。问题是“工具支持当前交付形式”，而不只是 namespace 相同。

### 3.2 理解语义配对修好了，实际计划配对仍能被换

需求为甲乙共同靶点、丙丁共同靶点。在 compile 后把实际动作 object_refs 换成甲丙、乙丁，保持 covers 不变：

```text
validate_plan_coverage → ok:true
to_query_routes → 甲丙、乙丁
```

因为 `_expected_items`、`_step_items`、declared covers 都退化成逐对象三元组并全局比较，丢失了需求 ID、关系组与范围。理解层 normalize 的修复没有自动修到规划层。

这两项是计划突变验收：目前 answer 尚未接入执行器，不宣称当前在线已执行这些错误关系。它们直接证明“改成已注册错误工具也会拒绝”“声明与实际覆盖一致”的验收范围仍不足。

### 修复要求

- 按 `(需求类型、字段/关系、delivery.form)` 解析唯一允许工具或显式等价工具集合；校验工具所属能力，不继续叠 namespace 特例。
- 用类型化动作校验参数、对象数、关系类型和范围；覆盖项保留整组对象与需求对应关系，不能把二元关系拆成无关联的对象集合。
- 声明覆盖与实际覆盖逐步骤一致，最终全局覆盖也一致；具体 dataset 集合、分页范围亦不能丢失。
- 适配器遇到无法处理的步骤必须明确失败，不能跳过产生空 routes；适配后校验完整交付。

必要回归：answer↔table 工具替换；已注册关系工具互换；只换实际配对、不换 covers；完整正确配对正例；未知/不适用步骤不产生空成功。

## 4. 自动回归声明需要收紧

本轮相较上一轮，六个新模块的测试数从 68 增到 71，新增的三个具名用例为：all 拒绝、已知拒绝重放、正常父子交付。

关系/数据库完整 pipeline、已注册错误工具替换、关系 normalize 配对变化，本轮独立探测已验证修复；但当前测试目录未见与汇报描述对应的完整新增断言，不能笼统写成这些都已转为 CI 回归。已有测试也仍有只 compile→adapter、绕过正式 coverage 的路径。

请把已验证修复纳入持久测试，并添加本轮两类边界。测试数只用于核对证据，不是验收目标；无需制造大量近义用例。

## 5. 下一步的有界范围

本轮不再要求扩充框架或额外业务。只关闭：

1. 父子需求转移的冲突检查及最终目标一致性。
2. 能力、交付形式与关系组的完整计划覆盖。
3. 对应正式单测/ASGI 已知拒绝重放与正例回归。

完成后可把这批修复标为通过，再推进已列明的 answer 接线、429/恢复/权限生命周期验证。全量读取维持显式未支持也可以，不需要为了本次验收实现它。真实模型对照留在程序闭环验收之后。

复现命令：

```bash
.venv/bin/python - <<'PY'
import subprocess, sys
from scripts.run_offline_ci import test_environment
subprocess.run(
    [sys.executable, 'audits/requirements_v3_review_v3/probe.py'],
    env=test_environment(), check=True,
)
PY
```

探测脚本成功退出仅表示观察完成；不能把 JSON 中的 coverage ok 或 task complete 当成正确验收，它们在上述反例中恰是缺陷证据。
