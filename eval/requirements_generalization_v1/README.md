# 需求理解评测：开发集（历史 v1）

本目录不是最终未见题。原草稿实际 **43 场景 / 46 轮**，此前文档的40题、预算129均不适用于该草稿。完整三策略上限是138次。草稿由参与实现的 Agent 编写，保留作开发资料，不宣称独立泛化。

- `questions.private.draft.jsonl`（仅本地保留，不进入Git）：历史原始意图；旧来源断言不完整，不能直接作为新评分器输入。
- `dev_synthetic.json`：5场景、6轮，验证单药、双来源、追加表格、边界和聊天。
- 来源补齐的旧草稿：`var/requirements_eval_repair_v2/references2/questions.private.json`，仅开发参考。
- 独立 AI 原句及参考移至 `eval/requirements_heldout_v2/`，40场景、43轮；已进入真实模型对照，最新进度见[收尾验收](../../audits/requirements_completion_v1/README.md)。

新执行器运行真实 ASGI 与文件交付，合成 dry-run 仅替换模型。合成结果 V 5/5、R/G 各3/5，不包装为全通过或真实模型收益。

```bash
.venv/bin/python -m scripts.eval_requirements_generalization prepare \
  --synthetic --suite eval/requirements_generalization_v1/dev_synthetic.json \
  --out var/my_new_requirements_smoke
.venv/bin/python -m scripts.eval_requirements_generalization dry-run \
  --out var/my_new_requirements_smoke
```

目录必须新建，结果不可覆盖；业务失败返回非零退出码。详见 [修复及验收](../../audits/requirements_eval_repair_v2/README.md)。
