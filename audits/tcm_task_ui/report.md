# 浏览器验收补充报告

日期：2026-10-15。环境：已有Python轻量API环境、Playwright 1.63.0、实际Chromium与临时回环Uvicorn服务；数据只读，无模型调用。

3条页面流程、5条PMID显示提醒、3条中文降级均通过，另保存实际HTTP422验证。页面流程真实点击筛选、翻页、查看记录与展开来源，返回时滚到保留的结果列表；没有模拟API响应或将API重放冒充页面动作。

产物：[最终逐项报告](report.json)、[首轮失败](report_initial.json)、[9条partial梳理](partial_triage.json)、[60题覆盖记录](coverage.json)、[工程回归](regression_local.json)。截图及复现命令见 [运行说明](../../docs/TCM_TASK_UI.md)。

首轮3个超时来自测试匹配器未解码来源ID路径，截图已有详情；修正匹配后通过。没有修改生产页面、API或候选答案；保留首次失败截图。最终源码/候选SHA、数据库未变和外部页面请求为0均记录于报告。

58条候选至少完成某种执行检查，2条聊天未执行。既有46/9/5的API分布是前次API检查范围的历史结果，不被本补充重写；任务完成率仍null、人类签核仍pending、论文结论未验证，全部60题仍是designed。
