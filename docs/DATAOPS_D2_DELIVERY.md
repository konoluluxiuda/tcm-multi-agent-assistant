# D2：动态执行、人工等待与增量交付

更新：2026-10-29。D2工程链路已实现并做本地验收。**网页当前开放的是合成模型替身，不是已验收的真实DeepSeek自主Agent。** 原生Tool Calling适配器有离线契约测试；真实模型观察回传尚未运行，不能用本轮结果宣称模型效果提升。证据见[验收记录](../audits/dataops_d2_v1/README.md)。

## 运行与使用

已有本地资料和虚拟环境时：

```bash
.venv/bin/python -m pip install -r requirements-research-chat.txt
.venv/bin/python -m scripts.preview_local_tasks \
  --port 7908 --state var/dataops_d1_preview --dataops-synthetic
```

打开 <http://127.0.0.1:7908/dataops>。不加`--dataops-synthetic`时仍只提供D1固定流程；该参数开启本地决策替身，不产生外发授权。聊天仍走原有路径，不自动变成DataOps循环。

1. 输入“白术”和“黄芪”各一行，选择功能与主治、注意，点击“动态整理”。任务在后台执行，可关页，之后从已保存任务打开。
2. 查看结果和下载v1；在字段中增加“贮藏”，点击“增加字段并生成下一版”。新版本保留旧字段，增加差异表，v1仍可下载。
3. 可勾选“同时重查存在对应问题的记录”，使这些记录依赖的文档字段失效并重查；其余记录复用。需要变更记录范围时由API提交明确的新Goal，本页修订默认沿用当前结果集。

目标以页面所选记录、字段和完成策略为准；“任务说明”保存原文作为决策背景，**尚未提供任意自然语言到完整Goal的可靠编译器**。不把未识别的自由文本要求悄悄当成已完成。真实模型接入与自然语言目标覆盖须在后续业务验收中验证。

真实20条样本没有多个候选，人工消歧使用明确标注的合成资料演示：

```bash
.venv/bin/python -m scripts.preview_dataops_fixture \
  --state var/dataops_d2_demo --port 7909
```

打开 <http://127.0.0.1:7909/dataops>，输入`Alpha`、`Gamma`各一行，点击动态整理。Gamma有两个候选，可选“丙草”、跳过保留未知或取消；刷新/重启后仍可从任务记录继续。该服务只有四条合成资料，不能用于真实药材查询。

## 实际调用链

```text
DataOps页面 → 结构化Goal与原问 → owner/来源/父版本校验 → 原队列
→ ResearchWorker → DataOps Engine → 原生SqliteSaver
→ model → tools → 观察 → model …
                 ↘ wait: interrupt → 释放worker → answer → Command(resume)
→ 构建表格 → 重新读取校验 → finish → 工作区投影与下载
```

`model.py`提供原生`bind_tools`适配器及独立的SyntheticDriver；HTTP目前只接后者。模型返回一个工具动作及简短目的，宿主校验参数和范围；不输出隐藏长思维链。工具包括查询、来源核对、字段读取、比较、证据回读、询问、构建、校验、结束。批量工具内部每10条循环，避免每条都规划。

`Goal`由宿主保存，工具不能修改；`plan`保存实际动作及目的，观察驱动后续动作。当前是追加行动记录，不是可编辑的复杂依赖计划画布。缺少列/来源核对时不能构建；没有文件校验不能结束。审查任务允许把缺失列出后完成，要求全部补齐的任务仍有缺项则partial。

## 状态与恢复的边界

| 位置 | 保存什么 | 不承担什么 |
|---|---|---|
| 原SQLite队列 | 排队、租约、取消、SSE事件及待处理回答 | 不重建图状态 |
| 每个工作区`graph.sqlite3` | 原生图位置、Goal、观察、字段结果、待答问题 | 不由旧A journal再次恢复 |
| `dispatch.sqlite3` | 模型动作派发前预留、请求摘要hash及已落地响应 | 不宣称远端请求恰好一次 |
| `workspaces.sqlite3` | owner隔离、父子版本、请求幂等、结果/页面投影 | 不能作为另一套图恢复器 |
| 工作区`vN.xlsx` | 不可变交付物、manifest hash、逐格校验 | 不覆盖旧版或写回资料 |

等待状态为非终态`waiting_input`，不占worker租约。回答核对owner、任务版本、问题ID、候选及队列revision；相同回答幂等，过期/越界回答拒绝。等待节点在`interrupt`前不调用模型或写文件。两个不同Python进程验证了等待与续跑。

中途租约失效需显式恢复；源版本或关键执行代码/提示版本变化拒绝重放。模型请求预留后无响应记录视为`model_dispatch_unknown`，停止且不能静默重发；已记录响应可复用。原子发布后元数据落地前崩溃，可重新读取并比对同版本文件后补提交。

首版仅支持SQLite队列/单机本地文件，DataOps入口拒绝PostgreSQL队列配置；没有宣称分布式部署、用户账户系统或等待任务自动清理。SQLite轮询/文件锁不能代替多主机调度。

## 增量、来源与上下文

父版本来源hash必须一致；新增字段只读新增字段，明确重查的记录重新核对全部依赖文档字段。人工选择候选也会使该映射相关字段失效；选择不等于身份人工审核。源文件或索引发生变化时拒绝旧值复用，应另建完整任务。

差异按来源ID＋字段比较值、状态和证据，不按Excel行号。每个子版本有独立工作区ID、`parent_id`和递增revision；同一父版本只允许一个有效子版本，防止两处修改互相覆盖，冲突后应打开最新版本再改。没有实现自动合并两个并行编辑。

完整证据保存在本地图状态，给模型的是确定性覆盖摘要、状态计数、有限异常和最近观察。列表带总数且仅展示前若干项，不删Goal；证据工具只接受当前任务已有ID，每次最多3段、每段800字，标记截断。不是无限增长的消息历史，也不是向量语义记忆。

默认上限：8次模型决策、24次顶层工具、200次逻辑来源读取、每批10条、最多50条、16KB决策上下文、300秒活跃节点耗时；等待不计入。两次无进展停止；显式只读TimeoutError最多允许额外一次动作，模型不自动重试。`leaf_reads`计量查询/逐记录适配器调用，不是SQLite每条SQL或文件hash I/O数量；会重新核对来源hash，仍有性能优化空间。HTTP worker超时为305秒，取消依靠租约守卫阻止后续提交；不能物理撤回已经发出的网络请求。

## 验收与下一步

本轮覆盖原生Tool Calling响应解析、观察分支、字段缺失、真实20条来源对照、增量读取、XLSX重开校验、跨进程HITL、版本/owner/并发保护、未知派发、租约恢复、预算和取消。初次网页验收发现刷新后标题不回填；后续并发复测发现等待状态与旧工作区快照可能短暂不一致，均已修复；开发失败与修复记录见验收目录，API失败JSON保留在私有`var/dataops_d2_v1/`中。

真实资料仍为`source_reconciled_pending_human_signoff`，未升级为身份/药典内容审核通过。真实模型对照、30题冻结集、50条规模/性能和新Docker镜像属于D3，均未完成。不将合成策略的动作数写成DeepSeek调用成本或准确率。

回滚：移除`--dataops-synthetic`或关闭`ENABLE_DATAOPS_AGENT`，D1和旧聊天继续使用；保留工作区、队列、checkpoint及v1/v2文件。不要删除状态后声称已经验证历史恢复，也不要让旧镜像接管新任务。

实现核对参考：[LangGraph中断语义](https://docs.langchain.com/oss/python/langgraph/interrupts)、[持久化说明](https://docs.langchain.com/oss/python/langgraph/persistence)、[SQLite适配器元数据](https://pypi.org/pypi/langgraph-checkpoint-sqlite/3.1.2/json)。
