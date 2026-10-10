# 简单 RAG / 完整 Agent 对照运行

新增入口：`compare_rag.py`。不修改线上路由、知识库或模型配置。

## 已完成与未完成

2026-10-09：6 项标准库单元测试通过，29 条现有 QA 数据通过格式检查。
本环境缺少运行依赖、Docker WSL 集成不可用，尚未执行真实模型/API 对照。
`--check` 只检查数据及依赖可见性，不代表服务连通或模型可用。

## 两种模式

- simple：原始问题 → 同一 Qdrant 集合的混合检索 → 原有回答生成器。不做查询扩展、重排、路由及输入/输出过滤，不调用 Tavily 或 CV。
- full：现有完整 Agent 流程，包括原来的过滤、路由、RAG、搜索及输出过滤。为了隔离测试，每题使用独立会话；为了避免反复打开本地数据库，在评测进程内复用一个 RAG 实例。这不是完全不加干预的线上性能基准。

两者保持同一聊天/嵌入模型、同一集合与检索 top_k。完整流程会在重排后减少上下文，这是待比较的流程差异。
首次模型加载、检索初始化的时点不同，当前逐题延迟不宜直接用于宣称性能提升。后续需统一预热并重复运行。
模型调用数只统计聊天请求的 LangChain 回调事件，不包含嵌入调用与供应商内部重试。
如果响应提供 token_usage 则记录，暂不估算费用。

## 在有 Docker 的原运行环境执行

先确保使用包含当前依赖的 `medical-assistant` 镜像。以下命令用挂载读取当前代码。
本地 Qdrant 目录不能被多个进程同时打开。先停止使用同一知识库的应用/入库任务，再顺序运行；不要同时运行两个模式。

```bash
# 只检查输入，不调用模型
python compare_rag.py --check

# 分别先跑 3 条，确认运行路径；这不是效果验收样本量
# 两条命令必须顺序执行
docker run --rm --env-file .env -v "$PWD:/app" -w /app medical-assistant python compare_rag.py --mode simple --limit 3
docker run --rm --env-file .env -v "$PWD:/app" -w /app medical-assistant python compare_rag.py --mode full --limit 3

# 联调通过后，对相同的全部问题运行
docker run --rm --env-file .env -v "$PWD:/app" -w /app medical-assistant python compare_rag.py --mode simple
docker run --rm --env-file .env -v "$PWD:/app" -w /app medical-assistant python compare_rag.py --mode full
```

simple 不要求 Tavily 密钥。full 故意保留当前缺失密钥时的失败行为，用于定位问题；其搜索错误不应被解读为检索质量差。
full 仍受原应用导入阶段 CV 权重加载的约束，缺失权重可能在第一题前失败。
当前包级导入仍会加载 Docling 等依赖，simple 还不是一个完全剥离重型依赖的部署版本。
启动阶段失败直接返回非零退出状态；题目阶段失败记录到报告并继续下一题。

## 输出及解读

`data/eval/comparison/` 下每次产生独立命名的 `.jsonl` 和 `.summary.json`。
每完成一题就刷盘，失败不会丢失前面已完成题目的结果。

- 对照前检查 dataset_sha256、case_ids、模型/维度相同，确保期间未更换或重新导入知识库。
- 每题保留检索文本、来源、片段 ID、原始 RAG 输出、最终回答、异常、耗时、聊天调用与供应商返回的用量。
- 下游搜索异常不会抹掉已经捕获的检索记录。
- 文件名完全相等才计算来源文件命中，但文件命中不等于证据片段命中。
- review 栏留待人工填写答案支持情况、引用正确性、拒答合理性；不再使用英文关键词重合率评判中文答案。
- 现有 29 条自动生成题仅用于工程筛查，不是中草药验收集。参考答案本身也需要人工核查。

## 中文领域数据格式

可通过 `--dataset path/to/qa.json` 指定人工核查的问题数组，每项包含非空字符串：
`id`、`question`、`reference_answer`、`source`（预期来源文件名）。
当前模式要求一个预期来源，适用于有答案的问题；无答案题、多个等价来源、跨文档证据的正式计分仍待扩展。
不要为通过校验而给无答案题编造来源。待中草药语料确定后，再建立对应的可回答/不可回答/歧义与多轮评测集。

## 离线回归

```bash
python -m unittest discover -s tests -v
```

覆盖：中文输入与重复 ID、空数据集、检索后搜索失败仍保留证据、题目之间记录隔离、来源严格匹配、内部错误回退不计为成功。
这些测试不替代 Docker 中的端到端验证。
