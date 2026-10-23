# 聊天候选规模对照（P2f）

疾病候选排序下新增默认折叠的“查看规模对照”：展开后显示药材来源、实测共同靶点、同规模期望和差值。原排序、数量与双方原始记录入口保留，不新增模型调用或逐题随机模拟。

期望 = 疾病背景内靶点数 × 药材靶点数 / 当前药材背景总数。背景外标识保留在原Jaccard计算中，不参与规模期望。正差值不等于疾病针对性或疗效，不输出显著性或推荐等级。页面只说明一次这些边界。

后端输出严格校验背景大小、期望与差值，非有限值或不一致结果拒绝展示。疾病分析revision升级；旧会话焦点可能提示重新查询，原记录不删除。刷新新查询会重新计算，并将说明恢复折叠。

本轮独立预览：http://127.0.0.1:7878/ 。示例：

```text
/analysis disease-rank Dermatosis Papulosa Nigra --top-k 5
```

启动命令：

```bash
python -m scripts.preview_local_tasks --port 7878 --state var/disease_size_context_v1/state --diseases var/disease_catalog_v1/build/diseases.sqlite3
```

本机已用 `/tmp/tcm-ci-mail-env/bin/python` 启动；外部模型关闭。7877旧进程未更新，Docker未发布。

验收：24项相关离线回归通过，6项真实浏览器检查通过，包括真实来源公式、默认折叠、展开数值、排序切换、刷新恢复和JS错误检查；模型调用0。未重复全套回归或增加独立效果成绩。见[验收摘要](../audits/disease_size_context_v1/summary.json)。

受影响文件：`tcm/disease_analysis.py`、`tcm/analysis_tools.py`、`templates/index.html`及疾病分析测试。回滚需同时回退后端字段契约和展示；无数据库迁移。下一项优先整合未提交修改、复核部署说明并准备一次端到端演示，不默认继续扩评分算法。
