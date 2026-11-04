# Requirements V3 独立代码审查（2026-11-02）

结论：实现方向正确，已有版本化模型、能力注册与部分服务接线；但 **U1–U3 尚未达到交接说明的完整验收标准，不能把剩余工作理解为仅补 U4 接线**。新策略保持可选，先修本记录中的契约/规划/语义问题。

本轮仅审查、运行离线测试和添加审计文件；没有修改业务实现、重启服务、调用外部模型或发布 Git/Docker。

## 1. 核实后的进度与证据

- 磁盘状态比用户粘贴的汇报更新：已有 `requirements_pipeline.py`、`to_research_goal`、`to_query_routes`、service 新策略分支和两项 HTTP 测试。
- 最新既有全仓回执 `var/requirements_v3_ci3/summary.json` 的确为 passed，1677 项；六个新模块合计 46 项。这里是读取历史回执，未在本轮重跑全仓。
- 本轮重新运行上述六模块＋`test_understanding_v2`＋`test_understanding_gate`：**8 模块、78 项通过，零跳过、零联网尝试**。使用标准 CI 的隔离 worker 和 `test_environment()`，并行 3 个。
- 另做 23 个观察型反例探测，结果见 [probe-results.json](probe-results.json)，源码与运行回执见 [verification.json](verification.json)，复现程序见 [probe.py](probe.py)。这些是暴露问题的探测，不计入“验收通过”测试数。
- 运行前后比对本轮检查的 requirements 模块、矩阵、service 和新测试文件哈希，未变化。该快照范围不是全仓所有文件。
- 全程 SDK 替身与临时 ASGI 数据，无真实模型评测。

统计需纠正：U1/U2/U3 的 20＋9＋7＝36；既有两模块 21＋11＝32，因此当时组合应为 68，不能写 61。当前新增适配器/pipeline/HTTP 后为 46＋32＝78。现有 `audits/requirements_v3/README.md` 同时写 service 已修改/未修改、HTTP 测试已加入/尚未完成，需按当前代码统一；原回执不覆盖。

## 2. P1：覆盖检查信任 covers 声明，未验证实际动作

位置：`tcm/dataops/requirements_planner.py:153`，尤其覆盖集合生成处。

反例：保留原 covers，将步骤的实际 `object_ref` 改成 o999、字段改成注意；或将工具名改为不存在的工具。`validate_plan_coverage` 均返回 `ok:true`。重复原步骤、增加一个 covers 为空的无关步骤，也返回通过。

现有“换对象”测试同时修改 arguments 与 covers，测到的是声明缺失，没有覆盖“声明正确、实际动作错误”。集合去重还掩盖重复动作。`Coverage.scope` 未进入比较，不能证明完整读取。

风险：计划看上去覆盖全部需求，但执行的对象、字段或工具可能不同，回到“测试绿却漏要求”的原问题。

修复要求：

1. 从注册能力＋经过真实参数 schema 校验的动作推导实际覆盖；与需求和 covers 同时比较，不能由 covers 自证。
2. 校验工具存在、参数类型、来源绑定、step ID 唯一性、未授权额外动作和重复动作。
3. 覆盖键包含范围；合法合并工具调用可覆盖多条需求，但不得用 set 静默掩盖重复执行。
4. 先冻结可执行计划，适配到最终路由/交付目标后再做等价性检查。

必补测试：只改参数、不改 covers；只改工具；重复步骤；无覆盖额外步骤；all 降级 page。全部需在执行前拒绝。

## 3. P1：表格适配丢失逐对象范围与父字段，校验的不是最终执行目标

位置：`requirements_planner.py:183`、`requirements_pipeline.py:59`。

`to_research_goal` 直接把所有对象的字段合成全局并集，没有读取父目标；pipeline 校验完 plan 后，又从 bound 独立构建 goal，未检查最终 goal 与原需求一致。

已复现：

- 父表有功能与主治、注意和 family；追加贮藏后适配结果只剩贮藏，并默认加入 medicinal_part。机械校验仍通过。这里只证明适配器丢列，不宣称线上旧文件已被覆盖；后续父子保护可能拒绝它。
- “黄芪功效、白术科名”的逐对象要求变成每味药都需功效和科名。若为 filter_complete，现有 `research_graph.py:35` 按全部列判合格，会改变筛选结果。
- 未要求 ETCM 属性时默认加 medicinal_part；未要求药典时默认加功效和注意，可能无声增加筛选条件。
- 6 个指定对象通过新机械校验，但生成 `target_count=6`，真实 ResearchGoal 的上限为 5，最终结构不合法。
- top_n 缺 count 仍通过，适配器自行补 1。

修复要求：父目标以宿主元数据合并，保留对象、旧字段、策略与来源版本；明确展示列与完整性筛选列；逐对象要求若当前 ResearchGoal 无法表达，具体拒绝或单独扩展契约，不能求并集冒充等价。输出前调用真实 `ResearchGoal.model_validate` 并验证语义映射，不在入队后才失败。

必补测试：父子完整交付＋旧产物哈希不变；不同药不同字段的筛选；默认附带列不改变筛选；对象/字段/数量边界；缺 count 澄清。

## 4. P1：纠错可以绕过语义核验，模型也没收到必要上下文

位置：`requirements_understanding.py:44`、`:114`、`:133`。

已复现：首轮 schema 错误，第二轮将“黄芪有什么作用”改成“查询 ETCM 科名”，结果直接 ready。代码在 used==2 时跳过复核，没有完整本地语义依据，也没有返回预算不足澄清。两次请求硬上限目前确实守住，但不能用它替代语义通过。

另外，`_once` 虽然接收 catalog、parent、feedback，真实模型消息只有通用 system＋原句：

- catalog 只记录诊断，没有放入提示或 schema 候选。
- parent 完全未进入模型输入；“增加贮藏”无法知道旧对象/旧字段。
- 纠错提示提到了 validation_feedback，却没有发送具体错误码与字段路径。
- 字段名 schema 为任意字符串，没有来自能力矩阵的受限枚举/字段清单。

探测 `model_context` 中候选、父字段、纠错码均未发送。依靠 fixture 预先给出正确答案，不能证明模型获得足够信息。

修复要求：构建可审计的受限输入包，真正发送候选名称、能力字段、父需求摘要和纠错详情。纠错耗尽预算且缺独立语义依据时停止澄清；不要增加第三次调用规避预算。新增断言检查实际 messages/tools 内容，不能仅检查函数参数存在。

## 5. P1：二次失败少计调用，复核超时被当成正常澄清

位置：`requirements_understanding.py:118`、`:139`。

已复现：

| 路径 | SDK 替身实际 invoke | 返回 external_model_calls | token 记账 |
|---|---:|---:|---|
| 首轮 schema 错＋纠错仍错 | 2 | 1 | 仅计首轮 12，漏第二轮 12 |
| 首轮正确＋复核 schema 错 | 2 | 1 | 同上 |
| 首轮正确＋复核超时 | 2 | 1 | 第二轮用量未知，但派发尝试也未区分 |

第三种还被广泛 `except (GoalResponseRejected, Exception)` 吞掉，转换成 `semantic_review_failed`。这与“任何未知派发保持未知回执”的现有设计不一致；服务随后可能把它存为普通完成提案。没有观察到自动第三次请求，不应误报为仍会无限重试。

修复要求：分离 dispatch attempts、已接收 response、已知 usage；每个收到的无效响应仍计请求与 usage；未知用量标未知，不能假装零。所有阶段的网络未知、程序异常与可恢复 schema 错分别处理，未知进入原有 journal unknown/reserved 流程。

必补测试：第二轮无效工具、schema 错、超时、程序异常；累计用量；HTTP 未知→同 key 不再派发；不能把程序异常作为正确澄清验收。

## 6. P1：基础 schema 仍有可复现崩溃和空需求放行

位置：`requirements_v2.py:82`。

- `needs_input + objects=[] + requirements=[]` 无法通过：前面的“无对象必须有 dataset 需求”与后面的“needs_input 不得带需求”冲突。最常见的对象不明确澄清不能正常表达。
- 同时有药材对象与 dataset 需求，访问 `DatasetReq.object_refs` 抛 AttributeError，未转为 schema ValidationError。
- 两个对象使用相同 ref 可以通过；绑定结果也仅比较 set，无法发现重复引用。
- 有对象、ready、requirements=[] 可以通过，可能编译空计划或靠默认字段生成无关表。
- delivery 对 count 与 row_policy 没有配套约束。

修复要求：先区分 ready/needs_input，联合类型按 kind 校验；引用必须唯一且有效；ready 必须有可执行需求；count 只在 top_n 使用且必填。明确已声明但未被使用对象的处理规则。

这些属于 U1 验收缺口，不是等到 U4 才应处理的集成问题。

## 7. P1：能力注册与最终工具行为尚未一致

位置：`capability_matrix.py:62`、`requirements_planner.py:78`、`:249`。

- common_target 被注册为 `herb_relation_records`，但该真实工具 `RelationArgs.kind` 只支持 ingredient/target。用真实参数模型验证 common_target 会失败。
- completeness=all 的 target 需求通过全部新校验，最终路由却固定 page=1，不保留 all。由于 answer 尚未接线，这是确定的适配错误，尚不能说已在当前线上返回错误全量结果。
- 注册项有 object_min/max/runtime_dep，但校验未使用这些约束；runtime_dep 目前均未配置，未形成运行依赖检查。
- 名称零候选或多候选均可通过 `validate_requirements`；宿主绑定的 selected_source_ids 没传到执行适配器。绑定 owner/data_version 从 catalog 特殊键取值，而当前 service 传入普通 bounded_catalog，应显式传真实宿主上下文。

修复要求：使用真正支持的共同靶点执行路径或显式未支持；单药多关系按真实工具参数展开；支持全量时实现完整读取与成本预算，不支持则在执行前拒绝。校验“来源未选择”与“药典无需该 ETCM 绑定”等情况，不能一刀切要求所有任务必须有 ETCM 候选，但需要每项能力的前置条件。

必补测试：注册表逐项核对真实参数/执行适配；运行依赖关闭；单药与两药关系；全量/分页；来源歧义与绑定复用；数据版本改变。

## 8. P1：新策略的显式本地请求返回 500

位置：`service.py:184`。

ASGI 实测 requirements_v3 下 POST `/api/dataops/research-questions`，`use_model:false`、普通可本地整理问句：**HTTP 500**。configured_model 被构造一次，SDK invoke 0 次。

原因：新策略分支在检查 allow_model 分流之前直接创建模型，随后把 authorized=False 传入只允许模型的 pipeline，触发 PermissionError，并用含 traceback 的 RuntimeError 重新包装。API 返回的是泛化 500，不是局部可解释的本地结果。

修复要求：模型关闭/显式 opt-out 在创建客户端前走完整本地适配或具体澄清；零 provider dispatch，也不依赖有效供应商配置。将错误恢复接回现有 structured failure/unknown 机制，移除临时 traceback 重包装。

必补测试：新策略＋use_model=False；部署模型关闭；无供应商密钥；同 key 重放；失败后回执状态。answer 已生成 query_routes 但 service 只接受 goal 的缺口需另行接线，不能把 answer ready 转成泛化“需求未确定”。

## 9. P2：语义相同但内部编号不同会误报分歧

位置：`requirements_understanding.py:36`。

normalize 只排序部分列表，仍比较模型自行分配的 o1/o9、r1/r9、字段顺序与需求分组。仅更换内部 ref/id，语义完全相同的两次提取就返回 semantic_disagreement。

修复：将对象引用映射到宿主规范对象，再归一化为逐对象、字段/关系、范围、交付策略的语义项；保留真正有语义的顺序要求。临时编号、可合并的需求分组不得影响比较。加入同义结构正例及对象/字段/否定改变的反例，不能直接忽略全部差异。

## 10. 测试为什么没发现这些问题

现有测试确实执行且通过，不需要否认其结果。问题在测试断言的边界：

- 复核成功用两份完全相同的 dict，未验证等价但不同编号/分组。
- 纠错成功用例允许未经语义复核直接 ready，把错误行为写进期望。
- 二次失败只断言 model.calls==2，未核对 returned calls 和 usage。
- 计划测试把实际参数和 covers 一起改，没测试两者不一致。
- 表格测试对象字段相同、数量不超过 5、没有追加旧字段保持。
- HTTP 两题只检查成功表格与普通重放；下载断言 HTTP 200，没有逐单元格验证新契约语义，也没覆盖 429、未知、父子恢复、显式本地模式。

因此，继续增加相同 fixture 的通过数帮助有限。应先将以上反例转成回归，验证最终工具参数和文件内容，然后推进后续集成。

## 11. 建议给实现 Agent 的下一轮任务

顺序：

1. 修 U1 schema 和语义归一化；补无对象澄清、混合 dataset、重复 ref、空 ready、数量边界。
2. 修 U3 真正输入的候选/父上下文/反馈，纠错语义门槛，失败计数与未知派发。
3. 修 U2 实际动作覆盖、注册表与真实参数一致、完整性范围、执行前预算和来源绑定。
4. 修表格适配与显式本地模式；对最终 goal/路由再验完整性。
5. 再补 answer 接线、429/父子/恢复/权限完整 ASGI 测试，重跑全仓；更新真实阶段状态后才做未见问句对照。

默认策略先不切，保持旧运行实例。并非要求重写整个框架，而是补齐本轮引入的接口契约与验收漏洞。

复现观察脚本（项目根目录，使用标准离线环境；只使用 SDK 替身）：

```bash
.venv/bin/python - <<'PY'
import subprocess, sys
from scripts.run_offline_ci import test_environment
subprocess.run(
    [sys.executable, 'audits/requirements_v3_review_v1/probe.py'],
    env=test_environment(), check=True,
)
PY
```

脚本 exit 0 只表示观察程序完成；不能将其中暴露的缺陷解释成系统验收通过。修复后须把正确期望写成正式断言，再以新回执记录结果，保留本次问题证据。
