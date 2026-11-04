# 验收门禁与查询需求覆盖修复（2026-11-01）

本轮关闭独立复核发现的两个优先问题：反例脚本失败却返回0，以及普通查询可以静默丢掉药典字段。完整的“Requirements先行→能力矩阵→工具计划”迁移仍未完成，不将本轮写成全部架构重构验收。

## 实际改动

1. `check_understanding_counterexamples` 改为真正的失败门禁：接受/拒绝结果、拒绝码和阶段都匹配才通过；不匹配或程序异常返回1。KeyError/TypeError/一般ValueError等不再算成成功拒绝。默认运行新版本17题，查询路径不再跳过。
2. 原 `counterexamples.json` 保留。新增 `counterexamples_gate_v2.json`，纠正错误的预期拒绝码；AC04明确只测Schema拒绝，另加真实父目标结构的追加/改作用域反例。该父结构用例不冒充文件恢复测试，原HTTP父子文件不变测试继续保留。
3. 新增共享 `query_coverage.validate_query_fields`，检查四类字段查询的实际覆盖：属性概览、指定属性、单药药典及多药药典。使用现有明确字段约束，完整本地字段合同仅用于消除“性味”等跨来源歧义，没有新建问法关键词清单。
4. “黄芪有什么作用”不能被全量属性替代；“黄芪的药用部位和功效”不能只查部位。已知跨源多步计划仍走原逐对象/逐子句校验；能力不足时澄清，不只执行半句。正确药典查询、ETCM性味、普通属性概览和四项中文属性展开五个底层字段均保留。
5. `SEMANTIC_REGEX_GUARDS` 生效值加入能力元数据、理解诊断与提案幂等签名；查询覆盖代码也绑定回执。未执行的同key提案遇配置变化返回冲突，不重新调用模型。已交付工作区继续按原版本回看。
6. 能力测试改为核对两个策略的实际请求额度及执行开关；同步失效文案断言，没有删掉“理解模型不等于native执行”的检查。

## 验收口径

- [正常门禁](gate-on.json)：17/17、退出码0、查询/父目标路径无跳过。
- [关闭守卫的消融检查](gate-off.json)：14/17、AC03/AC09/AC10不匹配、**退出码1**。这是故意保留的失败，不算模型准确率或成功验收；检查的是编译器，不能外推为完整模型复核链的相同分数。
- 新增11项门禁/查询覆盖/HTTP检查，包含故意注入程序异常、错误标签/错误阶段、查询遗漏、同key重放和配置变化冲突。
- 全仓195模块、1631项检查通过，零跳过、零联网尝试，运行期间源码hash未变化，耗时343.54秒；见[离线CI](offline.json)及[源码快照](source-manifest.json)。每个模块使用标准 `scripts.run_offline_ci --worker`，隔离进程并行3个，单模块120秒上限，清除供应商配置并禁止联网；对所有Python候选进行语法检查。运行前后比对源码hash，不将多轮重复次数相加。
- 本轮不新增真实模型请求。模型返回均为SDK替身；原5/6及其他历史成绩不改。

原执行记录中的“全仓CI全部通过”已纠正：此前v3b–v3e确实failed。原始报告和[独立复核](../understanding_refactor_v1/REVIEW.md)保留，本轮新通过不能反向修改它们。

## 运行与回滚

[部署检查](deployment-checks.json)记录7920/7921的源码、策略及页面状态。7920维持本地，7921维持unified_v2理解＋fixed执行。两实例源码均为`74ea261f…`，`restart_required=false`，8个页面GET和两次帮助控制均HTTP200、帮助调用模型0次。部署辅助脚本先后遇到回执重复独占创建及读取错误计数字段的问题，已按实际PID补录、按`assistant.llm_calls`重新核验，未重复重启或修改业务代码；过程记在[验收回执](acceptance.json)。守卫默认开启，仅将OFF用于显式消融；它从来不代表关闭所有原句解析。未发布Docker镜像或推送Git。

复现门禁：

```bash
.venv/bin/python -m scripts.check_understanding_counterexamples --out var/gate-check.json
# 负对照：预期退出1，不应将其输出标记passed
SEMANTIC_REGEX_GUARDS=0 .venv/bin/python -m scripts.check_understanding_counterexamples --out var/gate-off-check.json
# 标准完整离线检查（串行入口）
.venv/bin/python -m scripts.run_offline_ci --out var/ci_gate_check
```

不迁移或删除现有状态库与文件。回滚应用时保留旧状态目录；理解回执绑定代码和配置，不用旧key强行产生新提案。运行中任务应完成或显式取消后再切换版本。

## 后续未完成项

能力矩阵仍是未接入的能力清单，Requirements仍为提案后的投影，尚不是唯一执行入口。已更正相关docstring和设计进展，不再称正则守卫“唯一回读点”或“仅作提示”。下一步需先版本化契约及旧记录兼容，再逐类接入能力矩阵并用完整请求链验收。真实模型误拒/误放行及泛化收益仍需另行冻结对照；不能用这17条已暴露开发题证明泛化。
