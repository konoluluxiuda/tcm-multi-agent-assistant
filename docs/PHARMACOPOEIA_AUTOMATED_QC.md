# 自动质检、异常复核与研究检索预览

2026-10-17。按“人工不能审核整部药典”的反馈，取消研究预览必须逐字段人工签核的统一前置条件。先进行机器质检、隔离异常、分层抽检；人工审核状态独立保留。正式用途的准入条件还需独立评测和风险门槛，不将人工逐条审核视为唯一办法。

## 已运行的首批结果

| 处理结果 | 字段数 | 当前用途 |
|---|---:|---|
| machine_checks_passed | 28 | 可检索、可查看原文出处；仍未人工审核 |
| review_required | 5 | 异常复核队列，不进入当前研究索引 |
| not_available | 3 | 保持未知，不补写无禁忌，也不要求逐条填满 |
| 从通过字段中分层抽样 | 6 | 另选的检查点，与5个异常不重叠 |

若执行本批全部异常复核与抽样，需查看11个字段位置，而不是把36个全部作为必审前置。这个四味药试验有手工区域、目的性选页和扫描件重叠，**不能据此估算整部药典的错误率或人工工作量降低比例**；统计中的character_accuracy与manual_workload_reduction保持null，人工参考与批准仍为0。

机器决策读取双引擎原始OCR字段，不使用33份AI视觉草案作为放行答案。索引固定选择RapidOCR原值；没有按置信度、模型多数票或AI草案拼出更漂亮的句子。两套引擎即使一致，也只表示规则检查通过，不能证明原文正确。

## v1规则

1. 校验源PDF、OCR报告/页面文件、手工区域清单和页面指纹；从原OCR重新抽取字段，与输入候选核对。被手工或AI改写过的候选不能冒充原始OCR进入该流水线。
2. 检查坐标、页码、证据、字段边界与抽取异常；输入损坏会拒绝构建，字段阻断异常或暂停规则会隔离对应字段。
3. 双方字词差异、拉丁词混入其他文字、低模型分数、重复候选进入队列。数字、注意用语与用量/注意字段的差异提高优先级。
4. 只有**末尾单个中文句号缺失**可作为明确标记的例外进入研究预览。本批大腹皮性味归经因此可预览；内部标点、字词、大小写差异不因此放行，原候选文本不改写。
5. 模型分数阈值0.9仅为可配置工程初值，未校准成字符正确率。手工区域末尾的贮藏字段带边界提示；其他疑似截断字段阻断。还没有实现全书自动药材条目边界。

策略：[pharmacopoeia_qc_policy_v1.json](../eval/pharmacopoeia_qc_policy_v1.json)。通过字段按**来源SHA × 字段组**分层：identity、safety、description，每层默认抽1个；固定种子排序可复现。这是覆盖性抽检，不是随机代表性评测，也不是两个独立版本药典的质量比较。

`suspended_source_sha256`可暂停某来源，`suspended_strata`可暂停某个“来源SHA|字段组”。目前没有人工抽样结果，因此没有自动估算错误率或自动调阈值；需要操作者根据实际复核记录设置暂停项并重建批次。策略文件改变后，正在运行的预览API会拒绝旧索引，返回409并提示重建，不会继续使用过期策略。

## 本地使用

本地只需Python标准库与支持FTS5 trigram的SQLite，本环境已验证SQLite 3.45.3。不依赖聊天模型、嵌入模型、Tavily或云OCR。

```bash
# 使用已保存的双引擎结果构建新批次；已有目录时改用新的--out
python -m scripts.build_pharmacopoeia_quality
# 这是API服务，请使用此命令启动，而非普通http.server
python -m scripts.serve_pharmacopoeia_research --port 7868
```

打开 http://127.0.0.1:7868/ 。这是独立研究预览页，展示机器质检统计、5个异常、6个抽样点及可检索来源。例：

- “丁公藤”：精确药名匹配，查看符合预览条件的字段；中文名称仍是试验候选映射。
- “孕妇”：两个字符使用短词子串查询，返回丁公藤注意项原文和PDF第25页/印刷第4页。
- “归脾”：使用FTS5 trigram，返回大腹皮性味归经原文。
- 选择山麦冬＋来源：该字段在复核队列，当前不返回原文候选；没有采用AI草案替它作答。
- 选择一枝黄花＋注意：未提取到，不返回“无禁忌”。

这是**词法检索基线**，支持关键词、精确药名和字段筛选，不是自然语言语义RAG或诊疗助手。结果是带来源的原文候选，不生成额外答案。API：`GET /api/quality`、`GET /api/search?q=...&herb=...&field=...&limit=...`，最多30条，拒绝未知/重复参数。

SQLite索引通过只读连接检索，服务仅监听127.0.0.1；HTTP不提供数据库下载或整个仓库目录，不提供修改接口。脚本执行结束后未留下服务进程，按上述命令启动即可。

## 产物与边界

`var/pharmacopoeia_research/`保存quality.json、research.sqlite3、review_queue.json、sampling_manifest.json、页面和原图；保持忽略。首轮开发批次单独保留在`var/pharmacopoeia_research_initial/`。公开审计只含决策、计数、指纹及测试统计，不复制完整OCR、AI草案或原图。

本轮只验证四味药九类字段；没有全书自动切条目、向量检索、重排或正式聊天集成，没有修改ETCM或记忆功能，也没有发布镜像。28条是研究预览资格，不是28条人工批准资料。

## 验证

- 170/170离线回归，20模块；新增8项测试验证高风险差异隔离、来源/坐标错误、有限句号例外、字母混用、可复现抽样、暂停分层、AI草案不解锁原文、只读词法检索和查询转义。连接显式关闭，并以ResourceWarning作为错误验证新增测试。
- Chromium/API 10项通过：统计与队列、短词查询、跨页原图、异常/缺失不作答、中文trigram、精确药名、错误参数/文件暴露阻断、移动端、策略改变使旧索引失效、请求后数据库SHA不变。
- 首次浏览器测试的表达式等待触发CSP，改为函数等待后通过；未放宽页面CSP，失败记录保留。
- 实际篡改一份字段候选的正文，构建器在建索引前拒绝；原PDF和ETCM数据库SHA保持不变。

证据：[统计](../audits/pharmacopoeia_quality/summary.json)、[逐字段决策](../audits/pharmacopoeia_quality/field_decisions.json)、[抽样清单](../audits/pharmacopoeia_quality/sampling_manifest.json)、[浏览器/API](../audits/pharmacopoeia_quality/browser.json)、[首次失败](../audits/pharmacopoeia_quality/browser_initial_failure.json)、[离线回归](../audits/pharmacopoeia_quality/offline_ci.json)、[输入改写拒绝](../audits/pharmacopoeia_quality/input_integrity_check.json)、[来源完整性](../audits/pharmacopoeia_quality/source_integrity.json)。

下一步优先扩大独立页面样本、验证自动条目边界及抽样漏检，再用实际复核数据校准规则和人工工作量预算；不再以全书人工逐条签核阻塞研究功能。
