# P0-4b：有界问句契约与资料路由

2026-10-29。实现与限制见[交付说明](../../docs/QUESTION_CONTRACT_MILESTONE.md)，当前排期见[统一大纲](../../docs/TCM_PROJECT_ROADMAP.md)。

- [摘要](summary.json)：新增14项测试；全量145模块1231项通过，0跳过、0联网尝试。
- [完整CI报告](ci_final.json)：CI启动时语法文件466份；后加浏览器脚本另做最终467份语法检查，没有重复声称全量CI再次执行。
- [原24题开发回归](replay.json)：固定评分器17/18、5题边界、1题PubMed关闭；原8/18首轮没有改写，不能当作独立准确率。
- [BC03标签差异核对](field_label_reconciliation.json)：实际返回双字段；两项原始值、SHA、JSON位置均匹配冻结参考。原评分只接受“科”，worker返回“科名”，仍保留`requirements_not_met`，没有人工签核。
- [冻结与源码](source_manifest.json)：原题/参考字节一致，评分AST未变；新旧源码独立冻结。完整原始资料不进入公开审计。
- [浏览器](browser.json)：三个真实本地场景的表格、默认折叠、交互出处、下载、刷新同run通过，JS错误0；未审字体版式。
- [四种强杀](recovery.json)：保留同job及累计计量，已保存结果不重查，派发不确定时停止为partial。
- [7896预览](preview.json)：原状态目录重启后，双字段与刷新同run通过，外部模型关闭。

本轮没有真实模型请求、Git提交、镜像构建或上传。模型非固定语序与外发边界由合成SDK替身验证，不能据此报告DeepSeek的效果或计费。

私有批次保留在`var/question_contract_v1/frozen`、`replay`、`runtime`及`ci`，原批次在`var/business_closure_v1/`。下一步为未参与开发的新题冻结与受控模型对照，来源正确性/引用语义需要审核。
