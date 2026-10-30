# D1验收摘要

2026-10-29。D1为固定本地流程F；真实资料均留本地，模型调用0。

- [离线回归](ci_summary.json)：完整147模块1243项通过，跳过/联网尝试0；其后饮片范围修复对受影响的工具/存储及API模块复测16项通过。两批不相加冒充独立任务数。
- [真实来源一致性](source_consistency.json)：20个不同来源、80个不同字段参考；参考从原始JSON和索引前研究视图建立，先冻结后执行。5个开发场景通过，源目录/索引未修改。状态仍为source_reconciled_pending_human_signoff。
- [网页验收](browser.json)：真实20条交付、白术/黄芪名单、饮片段范围保留、四张表下载与hash、刷新、同key重放、跨owner拒绝、无旧文献页签。JS错误0；外部样式/字体资源5项被拦截，字体排版未验收。
- [实际实现指纹](implementation_hashes.json)，开发任务定义在../../eval/dataops_d1_dev_v1.json；它是已见开发集，不是D3保留集。

原始完整回归位于var/dataops_d1_ci_release/；范围修复后专项的原始JSON已纳入ci_summary。最初文献退役预期冲突的失败保留在var/dataops_d1_ci_v1/，未删除或改成成功。

私有来源参考、产物及运行协议在var/dataops_d1_scope_final/；本轮浏览器状态在var/dataops_d1_preview/。它们不作为开源数据或镜像内容发布。

实现/运行及限制见[交付说明](../../docs/DATAOPS_D1_DELIVERY.md)。下一项D2才接入动态模型决策、人工消歧续跑与增量v2；尚无真实模型效果成绩。
