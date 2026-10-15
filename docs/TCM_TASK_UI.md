# 任务候选的浏览器验收与部分验证项梳理

日期：2026-10-15。真实 Chromium 操作临时本机服务，未修改生产页面、候选答案、原冻结集或数据库；不是人类签核。

## 页面流程结果

| 候选 | 实际操作 | 来源记录数变化 | 结果 |
|---|---|---:|---|
| MULTI_STEP-01 | Labiatae + Cool，再添加 Pungent；打开记录并展开味来源 | 21 → 17 | 浏览器检查通过 |
| MULTI_STEP-02 | Ranunculaceae + Cold，再添加 Rhizome；打开记录并展开部位来源 | 32 → 18 | 浏览器检查通过 |
| MULTI_STEP-03 | Kidney + Bark，再添加 Warm；打开记录并展开药性来源 | 16 → 7 | 浏览器检查通过 |

每次筛选都通过真实按钮触发。浏览器逐页核对完整来源ID集合、分页与可见行，然后返回第一页；详情通过所选来源ID打开。来源展开核对文件、哈希、JSON Pointer及原始值。

当前页面将结果列表保留在详情上方。返回操作是滚回该列表，检查表单条件和列表内容未变；没有使用浏览器Back，也不宣称刷新、关闭窗口或跨设备后恢复。这是已有界面行为验证，本轮未添加返回按钮或持久化功能。

截图：[流程1来源展开](../audits/tcm_task_ui/TCM-CAND-MULTI_STEP-01-source.png)、[流程2来源展开](../audits/tcm_task_ui/TCM-CAND-MULTI_STEP-02-source.png)、[流程3来源展开](../audits/tcm_task_ui/TCM-CAND-MULTI_STEP-03-source.png)。中文截图使用已有临时字库，字体未提交或打包。

## 9条部分验证项的结论

| 类型 | 数量 | 新增证据 | 仍不支持的结论 |
|---|---:|---|---|
| 中文能力缺口 | 3 | 实际表单提交中文，降级文案可见，无错误药材详情 | 中文名称解析已经完成 |
| 参数契约说明 | 1 | 保存limit=0的真实HTTP422响应及query.limit位置 | 候选的contract说明必须作为API响应字段返回 |
| 文献解释范围 | 5 | 页面显示PMID原值或未提供，并显示未核验及关联文献不等于每项属性获证实的提醒 | 论文支持具体功效、没有相关研究、科学结论已核验 |

独立AI只读复核将这些内容区分为能力缺口、说明文字和科研解释边界，而不是一概视为缺失响应字段。原API报告的9条partial不自动改成passed；补充证据与未验证判断保存在 [partial_triage.json](../audits/tcm_task_ui/partial_triage.json)。

本轮浏览器候选检查为3条流程、5条PMID显示、3条中文降级，共11项通过；另有1项实际HTTP422检查通过。5条PMID与3条中文题已在前一轮API题内，不能重复计作新增任务。因此，60题中58题至少执行了API或浏览器检查，2条聊天规划题仍未执行。任务完成率保持null，人类审核待完成。见 [覆盖记录](../audits/tcm_task_ui/coverage.json)。

## 真实失败与修复

首轮3条页面流程都等待详情响应超时，而截图显示详情已经加载。原因是测试脚本将浏览器中的编码路径 `%3A` 与来源ID里的冒号直接比较；修正为URL解码后匹配，再完整重跑。保留 [首轮报告](../audits/tcm_task_ui/report_initial.json)，没有为通过测试改生产接口或候选答案。

首轮截图的中文缺字来自本地测试环境，引用已有临时fontconfig字库重跑后，最终截图中文可读。最终 [report.json](../audits/tcm_task_ui/report.json) 记录来源指纹、源码SHA、浏览器版本、JavaScript错误和外部页面请求；页面外部请求为0，数据与候选未变。

## 复现与边界

[脚本](../scripts/verify_tcm_task_ui.py) 需要轻量API依赖、Playwright和已安装Chromium。本轮使用已有 `/tmp/tcm-api-check-20261012` 环境及缓存的 Chromium，不调用模型或付费服务。一般环境运行：

```bash
python -m scripts.verify_tcm_task_ui --out /tmp/tcm-ui-check
```

可指定 `--suite`、`--db`、`--browser-executable`。脚本启动临时回环Uvicorn服务，结束后自动关闭；浏览器页面只允许该服务的请求。该检查依赖本地私有快照和浏览器，不进入默认离线CI；现有工程回归单独保留。

浏览器操作成功不能替代真实用户完成任务、科学内容核验或生产部署验收。下一项可推进结构化查询接入聊天，先支持已有来源ID/拼音等名称；中文映射及文献结论仍按大纲独立推进。
