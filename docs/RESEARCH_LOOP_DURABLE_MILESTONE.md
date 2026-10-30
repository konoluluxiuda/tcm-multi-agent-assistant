> 后续更新：合成模式已接入会话鉴权页面、恢复/删除和可选journal备份。本文保留执行层阶段记录，最新范围见[生命周期增量](RESEARCH_LOOP_LIFECYCLE_MILESTONE.md)。

# 模型工具循环的持久化与故障恢复

2026-10-27。本轮在已有LangGraph有界工具循环上增加可选持久化执行，不替换工具契约、证据校验、上下文投影或预算限制。验收使用合成SDK与本地合成工具，外部模型调用0次。

## 本轮实现

调用方通过`tcm.research_run_durable.run_durable`注入现有SDK和backend，并提供数据库路径、owner、run id及执行契约版本。底层仍使用`tcm.research_run.run`的decide/execute循环。默认`run`不启用持久化，现有网页/API行为保持原样。

状态分为：

| 阶段 | 含义 | 恢复行为 |
|---|---|---|
| ready → decide | 已保存上一工具结果，下一步是模型决策 | 复用消息、证据和累计预算后继续 |
| ready → execute | 模型动作已校验并保存，尚未执行工具 | 直接执行该动作，不重新请求模型 |
| pending model/tool | 调用前已记录意图并占用预算，结果尚未完成持久化 | 返回partial及uncertain_*_dispatch，不自动重发 |
| terminal | 已结束、取消、预算耗尽或不确定状态停止 | 返回保存结果，不执行新调用 |

持久化提交必须先于外部调度；提交失败则不发出调用。响应已返回但提交失败，同样保留pending，不能把“客户端没保存”理解为“提供方没执行”。原有模型/工具异常若已形成终态或错误观察，按原流程记录，不改成无限重试。

SQLite只存JSON，消息使用LangChain的字典序列化；没有pickle。它保存恢复所需的消息、动作、去重集合、观察、预算和下一节点，区别于旧版只保存脱敏展示快照的on_state回调。文件权限600、目录700，本地Linux进程锁防止同一run并发执行，进程退出释放锁。

绑定包含问题、scope、工具Schema、调用/时间/输入预算、上下文策略、估算器标签、当前`tcm/*.py`源码摘要及调用方提供的执行契约。调用方必须在契约中绑定模型版本、工具数据版本和自定义tokenizer身份；框架无法自动判断任意注入SDK的配置是否改变。绑定变化拒绝续跑。

模型次数、工具次数、累计输入估计及提供方已返回的usage不会因恢复清零。不确定调用已占用的预算保留，未获得usage时不补造计费数字。总时间预算采用持久化的绝对到期时间，停机时间也算在内，恢复不重新获得60/120秒额度。

## 验收

- 50项相关测试通过：持久化10、原循环12、scope9、SDK协议5、上下文5、输入预算4、证据读取5。
- 单测覆盖已保存模型动作/工具观察恢复、响应返回但提交失败、调用前存储失败、预算不重置、时间耗尽、owner与契约隔离、并发拒绝、终态无重复调用。
- 真实SIGKILL三个子进程边界：
  - 保存第一个工具观察后强杀，再由新进程继续：最终4次合成模型调用、3次工具调用，与不中断时一致。
  - 模型请求中强杀：恢复后partial/uncertain_model_dispatch，总计1次模型、0次工具。
  - 工具请求中强杀：恢复后partial/uncertain_tool_dispatch，总计1次模型、1次工具。
- 输入预算累计与不中断执行一致。合成SDK不代表真实模型能力或实际调用成本。

证据见`audits/research_loop_durable_v1/`，原始运行数据留在`var/research_loop_durable_v1/`。

## 本地演示

本机用`/tmp/tcm-ci-mail-env/bin/python`；其他环境使用已安装项目依赖的Python。

```bash
# 新run id，完全离线的合成模型与工具。
python -m scripts.persistent_research_demo \
  --state var/loop_demo/journal.sqlite3 --run-id demo-0001

# 读取同一终态，不产生新的调用。
python -m scripts.persistent_research_demo \
  --state var/loop_demo/journal.sqlite3 --run-id demo-0001 --resume

# 三种真实进程强杀演练，输出目录须不存在。
python -m scripts.check_research_loop_recovery \
  --out var/loop_recovery_new
```

演示中的owner仅是本地CLI标识，不提供账号认证。生产HTTP接入必须从服务端验证过的会话获得owner，不能直接接受客户端自报身份。

## 尚未接入及边界

- 尚未接入聊天页、后台任务队列或真实DeepSeek自动恢复。当前可从代码/CLI验证，不宣称用户已能在网页续跑任意模型Agent。
- 没有提供方幂等键、远端请求状态查询，也不承诺exactly-once。pending状态可能实际尚未发出请求；保守停止会牺牲一部分自动恢复率，避免无依据重试。
- 本轮SQLite journal是独立存储，现有核心备份CLI尚不包含它；不混称为PostgreSQL报告checkpoint。尚需终态删除、保留期、备份和owner接口生命周期接入。
- 单机Linux文件锁，不是跨机器分布式锁；自定义模型及资料版本绑定需要调用方提供，代码升级也可能使旧run不能继续。
- journal含恢复所需的私有上下文，不能作为公开审计输出；公开回执只含计数、状态和摘要。

## 后续任务与回滚

下一步把该执行能力接入受会话鉴权的任务生命周期，先保留合成模式，补齐查看/恢复/删除、备份覆盖及不确定状态展示。只有真实模型接入的外发范围、调用预算和提供方恢复策略明确后，才进行对应在线验收。

本轮通过显式调用`run_durable`启用，原`run`调用无需迁移。回滚可以停止使用新入口并保留journal文件供调查；不能丢弃journal后用同一任务从头执行，再声称没有重复调用风险。本轮没有Git提交、push或Docker更新。
