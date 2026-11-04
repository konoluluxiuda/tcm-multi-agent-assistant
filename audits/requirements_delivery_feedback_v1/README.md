# 回答/表格契约与模型纠错反馈修复（2026-11-03）

接续[真实对照首轮](../requirements_heldout_v2_live_v1/README.md)。本轮仅修改和离线验证，不重试未知请求、不调用真实模型、不覆盖冻结题集或首轮成绩。

## 改动

1. `requirements_v2.py` 把 Delivery 改为按 `form` 判别的 AnswerDelivery / TableDelivery。回答分支的 row_policy、count 在模型可见JSON Schema中明确只能为null，默认可省略；表格分支row_policy必填。保留旧持久化载荷的null字段兼容、top_n数量校验以及追加必须table约束。
2. `requirements_understanding.py` 提示加入合法answer/table最小例子；每次输入从现有能力注册表生成标准字段名清单，提示ETCM科名、药用部位、性味归经的标准字段，避免直接填写中文显示名。
3. `correction_fields` 输出稳定错误码、字段路径和修复要求。例如 `answer_table_parameter_forbidden` 明确要求省略row_policy/count，并保留对象和字段需求。不向模型提供原始错误输入、内部路径、行号/hash或任意异常内容。
4. 不放松执行门禁：最多两次请求；一次理解加一次纠错后，没有独立语义依据仍返回 `semantic_review_budget_exhausted`，不自动执行、不增加第三次调用。协议签名已有requirements模块文件hash，源码改变后不能沿用旧签名。

## 验证

- 相关6个测试模块100项通过，覆盖schema、理解预算、规划、历史审查反例及ASGI。
- 新增 `test_requirements_delivery_feedback.py` 6项：模型Schema分支、真实失败形态反馈、纠错不绕过预算、合法两次复核、旧null载荷兼容、top_n/append拒绝。
- [6份真实返回离线回放](recorded_replay.json)：前三道V任务每道两份返回，均维持拒绝且给出具体改法；没有修改模型返回，没有把它们改写为成功。
- 全仓离线CI通过：1799项、207模块、0跳过、0联网尝试，见同目录 `acceptance.json`。运行日志：`var/requirements_delivery_feedback_v1/`。

## 边界与后续

这是工程契约修复，尚无真实模型重测结果，不能声称V已经改善。已见的前三题只能用于回归，第四题未知请求不能自动重派；后续36题保留原句与参考。原冻结manifest会拒绝修复后的源码，这是预期保护；新增实验必须另建冻结目录。

第二题的来源歧义、来源默认策略与旧覆盖规则冲突仍保留待处理，本轮没有用改评分来提高成绩，也没有扩大修复范围到来源路由。表格top_n的数量关联仍由宿主校验并在提示/反馈中说明；本轮显式Schema分支解决的是已实测阻塞的answer/table参数串用。

默认策略未改；7932/7936容器未重建或重启，仍运行此前已验收镜像。Git未提交/推送。原始首轮11次请求及未知派发回执保持不变。
