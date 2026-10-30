# 大结果外置的完整隔离工具循环

> 后续进度（2026-10-29）：大结果外置已整合到[正式A循环](RESEARCH_EVIDENCE_OFFLOAD.md)，不将本隔离循环改名为生产实现。下文未接入与下一项建议是原阶段记录。

2026-10-28。完成小结果内联、大结果外置、受限回读和证据结束校验的实验链路。未修改聊天入口、大纲、正式research_run或项目依赖。

## 本轮新增

[实验循环](../experiments/architecture_candidates/offload_loop.py) 使用原生tool_calls与Pydantic契约，提供fetch_profile、read_evidence、finish_research。宿主绑定来源ID、版本与必需读取范围，模型不能改变owner/run或任意读文件。

- 小于等于16000字节的工具JSON内联；超过阈值保存完整证据，返回小型索引和句柄。阈值是实验配置。
- 读取必须匹配当前运行的证据ID、句柄和宿主声明的field/offset/limit；结果保留来源绑定。
- 只有要求的片段已读取且结束引用为已知证据时才complete。只是“指定片段已核验”，不是整份研究报告或自然语言结论质量。
- 复用RunJournal的pending/ready/terminal、owner、revision和绝对截止时间；限制模型/工具调用及单次等待，输入消息与Schema合计超过64000字节时停止。
- 在模型调用前保存pending，动作返回后保存ready；执行工具前再保存pending，引用和工具结果保存后才进入下一步。

本原型是隔离的Python循环，不是把新能力接入现有LangGraph图；复用消息协议、Limits、RunJournal和EvidenceStore来验证接口。正式接入应整合进现有节点，不能长期维护两套行为不同的生产循环。没有新框架依赖。

## 验收

14项新循环测试＋已有19项存储/发布测试，共33项通过。覆盖小结果、大结果、伪造句柄、越范围读取、伪造引用、提前结束、模型/工具预算、发布/动作恢复、丢失证据、owner/版本及错误来源。

[强杀记录](../audits/offload_loop_candidate_v1/summary.json)：

| SIGKILL边界 | 恢复结果 | 累计替身模型/工具调用 | 底层资料查询 |
| --- | --- | --- | --- |
| 证据已保存，引用未发布 | partial/uncertain_tool_dispatch | 1/1 | 1 |
| 引用已保存，等待模型回读 | complete，回读目标正确 | 3/2 | 1 |
| 模型动作已保存，等待执行 | complete，复用已保存动作 | 3/2 | 1 |

工具2次分别是资料查询和本地证据回读，不是重复查询。约406KB格式化合成资料外置后，成功运行的单次消息与工具Schema序列化输入最多4072字节。不能据此宣称token/费用降低相同比例，没有测真实模型token、语义选择或答案质量。

所有SDK响应来自[固定替身](../experiments/architecture_candidates/offload_driver.py)，外部模型调用0次。并非独立真实用户评测；没有在此实验中验证未知offset搜索。

## 运行

```bash
/tmp/tcm-ci-mail-env/bin/python -m unittest \
  tests.test_offload_loop_candidate tests.test_evidence_offload_candidate \
  tests.test_offload_journal_candidate -q
/tmp/tcm-ci-mail-env/bin/python -m scripts.check_offload_loop_recovery \
  --out var/offload_loop_new
```

out必须不存在，脚本只强杀自己创建的子进程。运行模型契约、阈值、来源、证据库路径、读取要求和实验循环/存储实现哈希绑定到journal；宿主仍需维护模型与传输版本。底层公共依赖完整版本未自动全部纳入实验契约，因此不能把旧实验库直接用于依赖升级后的生产续跑。

## 仍未解决

未引用证据记录保留，不自动清理；两个SQLite库之间没有原子联合事务。证据存储还未纳入统一备份/配额。身份由可信宿主注入，未提供HTTP入口或多机鉴权。此实验完整保存原始结果再按已知位置回读，不是通用语义检索系统。

新循环只有3种工具，未接入真实疾病工具、两药报告、搜索发现、项目记忆等完整业务，也未验证大型任务的模型规划。因此当前可以确认的是：无需整体更换框架，也能借鉴大结果外置并保留本项目的证据与恢复语义。不能据此直接改大纲或宣布最终选型。

## 下一项建议

暂停继续扩大基础机制。先将A、B及本原型的证据汇总成一份选型验收矩阵，明确哪些能力保留、借鉴、待验证；再选择一个完整业务任务，用相同合成fixture与统一接口检验任务完整性。真实模型验收需另行明确范围和次数，不能挪用历史授权。
