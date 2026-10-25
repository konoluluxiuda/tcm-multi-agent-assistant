# 本地 Cross-Encoder 重排接入与对照

2026-10-25。重排已接入当前单药/两药报告文本分支，使用本地模型，不新增真实资料外发或外部聊天模型调用。没有修改默认检索方案。

## 实际调用链和边界

后台报告 → 药材/字段/炮制范围保护 → SQLite关键词与本地dense/Qdrant召回 → RRF合并最多20个候选 → 可选Cross-Encoder → 返回最多6项带原始出处的片段。

- 使用`Xenova/bge-reranker-base`的固定ONNX量化导出，revision `cbd4c47f870c4bb77534e06b8853de9c6e958b82`。权重约279MB，分词器约17MB，校验固定公开哈希；构建/查询均不自动下载。
- 原模型支持中英文，参见[BAAI模型说明](https://huggingface.co/BAAI/bge-reranker-base)；量化文件来自[固定导出版本](https://huggingface.co/Xenova/bge-reranker-base/tree/cbd4c47f870c4bb77534e06b8853de9c6e958b82)。此次实测针对这一导出，不能外推到全精度模型。
- 成对输入为“当前查询”和“药名+字段名+原文”。每对最多512 token，batch最多4，最多20候选；记录截断标记，原文与出处不改写。
- 按模型logit排序，不把BM25、余弦与logit直接取平均。分数并列时按chunk ID稳定排序。logit不是正确率、药效概率或校准置信度。
- 单候选不加载模型。歧义、缺失字段、无词法证据等原有保护不绕过；失败保留原RRF顺序并记录降级。
- 推理预算12秒（不含初始化/文件校验），使用ONNX RunOptions终止标记中断超时推理；外层继续受45秒子进程预算保护。模型初始化卡住仍可能导致整条文本分支超时，不承诺任意故障都能保住该分支。

新文件为`tcm/local_reranker.py`；不把遗留reranker.py自动加载远程模型、混合不同量纲分数的实现直接搬入新主线。未添加新Python推理依赖，沿用`requirements-dense.txt`。

## 对照设计和结论口径

**原44题回归**：名称/字段、BM25、Qdrant+RRF、Qdrant+RRF+重排在同一数据快照上比较。重排开关开启后目标Hit@5仍29/35、MRR@5仍0.8143、拒答9/9，错误药材0题。实际重排调用为0：可执行结果多为单候选，多候选的正文诊断触发了歧义保护。因此这组结果只能证明原保护/回归没有退化，不能证明重排质量。

**新增12题显式优先项开发诊断**：六味药各问“功效和贮藏，重点是功效/贮藏”。题目与目标来源在模型评分前冻结，但检查过词法候选，标签由同一资料生成，非独立保留集。分别比较RRF、只从问句解析“重点是”的简单规则、Cross-Encoder。

首轮无规则对照时RRF首位正确6/12，模型12/12。随后补充规则基线，规则同样12/12；模型均时约2.25秒，规则方案约0.75秒（均含共同检索开销）。完整结果见 `audits/local_reranker_v1/priority_comparison_with_rule.json`。必须同时报告规则结果，不能只拿6→12宣传模型必要性。这批题只测已找到候选的阅读优先级，不是药学正确率、总体任务完成率或Recall。

**上线选择**：保留显式配置的重排演示。已有结果尚不证明其优于BM25或简单优先项规则；缺少真实用户改述和独立相关性标签。耗时包含每题模型加载/哈希校验，并与其他验收并发运行，只作为本机诊断，不是生产p95。

## 最终验收

全仓离线回归：112个模块、969项测试全部通过，跳过0项；364份源文件指纹一致。实际ONNX推理、超时中断后复用、失败降级及浏览器刷新回看均通过。记录见 `audits/local_reranker_v1/summary.json`。

## 复现与使用

公开模型准备脚本需要`curl`；默认只验证已有文件，显式`--download`才联网下载固定文件。网络端仅收到公开文件请求，不读取语料或会话。首次urllib下载遇到TLS重定向断连，使用curl保持证书校验后完成，未关闭TLS验证。

```bash
python -m scripts.prepare_local_reranker --out var/local_reranker_v1/model --download
# 在requirements-dense.txt对应的本地环境运行以下命令
python -m scripts.check_local_reranker --model var/local_reranker_v1/model --out var/rerank_acceptance.json
python -m scripts.eval_cross_source_retrieval \
  --qdrant-generation var/qdrant_research_v1/generation \
  --reranker-model var/local_reranker_v1/model --out var/rerank_regression.json
python -m scripts.eval_reranker_priority --model var/local_reranker_v1/model --out var/rerank_priority.json
```

本机独立演示（默认7879不变）：

```bash
/tmp/tcm-ci-mail-env/bin/python -m scripts.preview_local_tasks \
  --port 7881 --state var/local_reranker_v1/preview_state \
  --diseases var/disease_catalog_v1/build/diseases.sqlite3 \
  --dense-python /tmp/tcm-dense-20261019/bin/python \
  --qdrant-generation var/qdrant_research_v1/generation \
  --reranker-model var/local_reranker_v1/model
```

访问 http://127.0.0.1:7881/ → 本地研究 → 药典+ETCM报告 → 当归 → **功效和贮藏，重点是贮藏**。展开“检索说明”可核对实际后端、候选数、重排状态；页面仍优先展示结果，技术字段折叠。浏览器验收检查真实持久结果中的Qdrant/dense/reranker状态和出处，并验证刷新历史。

移除`--reranker-model`即可回滚到原排序，无数据迁移。向量库、模型权重、完整原文不进入Git；指标摘要保存在`audits/local_reranker_v1/`。本轮未提交Git、未push、未发布镜像。

## 后续尚缺

- 不同问法、跨字段/长文本、难负例的独立相关性评测；开发诊断不能代替用户任务。
- 常驻模型/批处理、真实负载与CPU资源隔离；当前每个任务子进程加载模型，成本较高。
- Qdrant独立服务与多机部署；此次仍为本地SDK持久模式。
- 自主LLM协作、PostgreSQL checkpoint、语义记忆、MCP等大纲项仍未完成。本轮只补检索重排，不把模型排序误称为Agent任务推理。
