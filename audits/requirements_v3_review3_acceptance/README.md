# 第三轮两项阻塞关闭验收（2026-11-02）

结论：**第三轮报告约定的“父子范围冲突”与“交付形式/关系组覆盖”两项阻塞，本次复验通过，可以关闭。** 此结论限定于该修复批次，不表示 answer 已执行、全部生命周期已验收或真实模型效果已证明。

本轮仅审查、离线测试及新增本验收记录；未修改业务代码、默认配置或运行实例，没有真实模型请求，没有 Git/Docker 发布。

## 1. 验证范围与证据

- 历史完整 CI：读取 `var/requirements_v3_ci8/summary.json`，确认 passed、1714 项、零跳过、零联网尝试；本轮没有重跑全仓。
- 本轮独立复跑：7 个 requirements 模块的 83 项，加 `test_understanding_v2` 21 项、`test_understanding_gate` 11 项，**9 模块共 115 项全部通过**。所有模块使用标准 `run_offline_ci` worker 和 `test_environment()`，进程隔离、禁用联网；零跳过、零联网尝试。
- 汇报“8 模块 83 项”应改为“7 个 requirements 模块 83 项”。83 本身正确。
- 额外 6 组有断言的关闭检查全部通过，使用 SDK 替身、临时 ASGI 与文件存储；不计入上述 115 项。
- 记录选定业务源码和 requirements 测试的运行前后 SHA256，一致。此哈希范围不是全仓所有文件。

证据：[测试与源码快照](verification.json)、[关闭检查结果](closure-results.json)、[检查脚本](check.py)。前几轮问题回执保留不变。

## 2. 父子追加冲突：通过

确认正常追加和对象顺序变化正例继续通过。额外检查了：

| 用例 | 实际结果 |
|---|---|
| 父 Alpha/Beta，明确要求 Gamma 追加 | HTTP 200、unsupported_delivery，无新子工作区 |
| 父 Alpha/Beta，只给 Alpha 追加 | HTTP 200、unsupported_delivery，无新子工作区 |
| 上述请求使用同 key 重放 | 返回同一 proposal、replayed=true；SDK 实际总请求保持 2 次 |
| 拒绝前后父文件 | SHA256 不变 |
| 拒绝前后工作区列表 | ID 集合不变 |
| 修改父行策略 | 适配前明确拒绝 |
| 修改父 top_n 数量 | 适配前明确拒绝 |

正式 ASGI 正例 `test_parent_append_preserves_fields_and_artifact` 与冲突用例同时通过。之前“需求写 Gamma，实际交付 Alpha/Beta”的完整链路反例，本次已被阻断。

## 3. 计划覆盖与适配器：通过

- 正确甲乙/丙丁共同靶点计划通过；仅篡改实际参数为甲丙/乙丁，保留 covers 时拒绝。
- answer 步骤改成 dataops_table，覆盖检查拒绝。
- answer 适配器收到无法处理的 table 步骤直接抛错；pipeline 有已知拒绝与空路由保护。
- 新增关系和 dataset 完整 pipeline 正例通过，没有重新引入列表参数异常。
- 全量读取仍由新路径显式拒绝，属于已公开的未支持项，不要求为关闭本轮阻塞实现分页循环。

一个不阻塞验收的测试命名建议：`test_registered_relation_tool_swap_rejected` 的实际断言是允许两个适配器标识进入同一个真实关系执行器，并非拒绝。建议改为描述“等价关系路由”的名称，或明确记录允许的等价映射，避免后续误读测试覆盖范围。

## 4. 下一阶段边界

可以进入下一批 **answer 接线＋请求生命周期验收**，不需要继续用本轮已关闭反例阻塞工作：

1. 将经校验的 query_routes 接入真实查询执行器；单字段、跨来源、关系、数据库介绍均核对实际结果与出处，失败不得退化成空成功。
2. 同批补 429 后同 key 重试、进程恢复、所有权隔离、未知派发回执；保持 provider 请求计数与重放口径。
3. CLI 与运行元数据暴露新策略，先独立端口/状态目录验收，默认部署不自动切换。
4. 更新实际完成口径后再开展冻结的未见问句评测。首轮本地语义依据优化可以独立评估收益；它不是本次阻塞的关闭条件。

接线时仍需验证来源绑定、运行依赖与最终工具参数。这些属于已经列明的集成验收；本轮 fixture 成功不能替代这些真实执行检查。

复现额外关闭检查：

```bash
.venv/bin/python - <<'PY'
import subprocess, sys
from scripts.run_offline_ci import test_environment
subprocess.run(
    [sys.executable, 'audits/requirements_v3_review3_acceptance/check.py'],
    env=test_environment(), check=True,
)
PY
```

该脚本包含明确断言，失败会非零退出；与前几轮用于收集缺陷观察的 probe 脚本口径不同。
