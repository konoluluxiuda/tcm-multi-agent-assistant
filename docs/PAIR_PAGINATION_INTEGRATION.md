# 正式两药报告：完整分页与请求覆盖

2026-10-28。本轮把已验证的分页及完成门槛接入现有`run_pair_report`，不是继续增加隔离实验循环。没有新增模型请求，没有更新Docker、重启服务或修改路线图。

后续已完成[真实浏览器与PostgreSQL强杀验收](PAIR_PAGINATION_BROWSER.md)，修复了异步checkpoint导致的已读页重查竞态，回归增至54项。下文53项及浏览器未验收描述保留为本轮初始记录；以后续验收记录为当前状态。

## 行为变化

- “列出全部/所有共同靶点”进入逐页查询；普通共同靶点或明确首页请求仍只查询首页。不因“全部字段”误开启全部靶点模式。
- 两侧请求的药典字段必须存在且带出处，才能报告complete；缺少字段写入`completion.missing_fields`。
- 全部模式逐页校验来源ID、版本、目录revision、total、页偏移、数量与跨页重复。只有最后一页已读且累计数量等于total才完成；合法零交集可以完成。
- 每页是现有LangGraph中的独立节点执行和checkpoint写入。续跑复用已保存的来源与页面，不把整个分页循环包在一个不可恢复的工具调用里。
- 最多10页（200个不同标识）；达到上限、超时、来源变化或坏证据均保留已验证页面并返回partial，不声称已查询全部。
- 页面展示保留的部分结果，后续查询按钮使用实际next_offset，不再固定跳到第2页；JSON下载继续包含原始见证。

涉及`pair_research_report.py`、`relation_queries.py`、`cross_source_report.py`、`research_runs.js`。底层共同靶点查询新增只读快照revision，本地worker支持offset。未迁移业务数据库。

## 验收结果

53项针对性测试通过：新增正式分页9项，加上两药报告、checkpoint、协议、关系查询、独立验收器及跨来源报告回归44项。

新增测试实际运行了本地子进程worker及临时合成数据库：

| 场景 | 结果 |
| --- | --- |
| 全部共同靶点 | complete，20＋3项；48条双方原始见证行通过文件哈希及JSON位置核验 |
| 明确首页 | complete，20项，只查询一次 |
| 第二页超时 | partial，保留第一页20项 |
| revision变化或跨页重复 | 拒绝合并坏页，保留已验证部分 |
| 分页预算耗尽 | partial，不把已读数量当作完整数量 |
| 一侧注意字段缺失 | partial，指出对应药材与字段 |
| 合法零交集 | complete，0项，不虚构结果 |
| 第二页执行中断后恢复 | 已保存来源和第一页不重查，未完成的只读页重新执行 |
| 更换请求后复用原checkpoint | 拒绝，防止旧结果被当作新任务 |

[集成对照记录](../audits/pair_pagination_integration_v1/summary.json)保留8次合成执行。原平铺参照仍复现“只读首页却complete”，正式子图现在完成全部23项。因此记录中剩余的1次false_complete属于保留的旧参照，不是新正式子图。它不是新的真实模型对照。

## 恢复与部署限制

沿用现有只读报告图的恢复语义：已保存节点不重复执行；执行中断且尚未保存的只读页允许重查。本轮未把它改成实验模型循环的pending未知状态停止策略，两者不能混称。

执行契约版本变为2，并绑定对象、问题、分页范围及上限。旧checkpoint不直接按新契约续跑；生产PostgreSQL包装还会验证代码与资料fingerprint。应新建任务使用新逻辑，不能通过改旧绑定强行续跑。已完成的历史报告仍按原记录查看。

测试使用MemorySaver验证节点恢复，没有本轮实跑PostgreSQL强杀；已有PostgreSQL绑定代码未更改。前端代码已更新，但当前测试环境缺少Node/Playwright，未执行本轮浏览器验收。尚未重启现有部署，因此不能宣称用户正在打开的页面已经更新。

此次接入的是正式确定性两药报告图。A/C实验模型适配器仍未合并到通用聊天模型循环；不声称已经完成任意多步骤任务规划。

## 运行

```bash
/tmp/tcm-ci-mail-env/bin/python -m unittest \
  tests.test_pair_report_pagination tests.test_pair_research_report \
  tests.test_pair_checkpoint tests.test_pair_protocol tests.test_relation_queries \
  tests.test_pair_architecture_preflight tests.test_cross_source_report -q
```

原前置脚本现支持复测新子图，历史审计不覆盖。下阶段应在本地实际服务中验收页面、任务队列及恢复入口，再决定部署；不需要为本次确定性分页修复额外调用外部模型。
