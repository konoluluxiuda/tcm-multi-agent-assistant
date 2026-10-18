# 研究检索的范围与引用保护

2026-10-18完成查询层保护，未重新识别PDF或重建索引。目标是区分“检出某个字段”和“用户指定范围已有完整候选证据”，不把不完整片段作为有效命中。

## 当前行为

| 情况 | 返回状态 | 页面展示 |
|---|---|---|
| 指定炮制前缀＋已知药名，但没有精确同名条目 | `variant_scope_unresolved` | 有效命中为空；只提供相关母条目标题原页供核对 |
| 字段只有标题，或正文为“同某条目／见某条目” | `reference_unresolved` | 有效命中为空；原文放入未完成片段 |
| 多字段查询同时包含完整候选和未完成片段 | `partial_evidence` | 分开显示有效候选与未完成片段，查询仍未完成 |
| 已知药名嵌在无法确认的复合名称中 | `name_not_resolved` | 不把子串识别成已确认药名 |
| 精确匹配已有炮制品且字段正文可用 | 保持正常检索 | 不剥离炮制前缀，不替换为母药 |

这些保护同时用于药名字段路由和BM25；BM25仍是探索工具，没有通用拒答阈值。全部内容仍未人审，不生成回答，`answer_generated`始终为false。

示例：

- “酒大黄的用量”不再直接返回大黄用量；相关母条目标题保留原PDF证据入口。
- “炙甘草的用量”返回未完成状态。OCR用量标题保持原值；相邻的“贮藏：同甘草”作为独立引用提示来源展示，不自动把母药内容复制到当前字段。
- “法半夏怎么贮藏”遇到“同半夏”时提示引用未展开；“法半夏的用量”仍可返回其独立字段。
- 人工构造的“火星灵芝草”不再作为灵芝查询；“药典中甘草的功效”“陈皮该怎样保存”“丹参配伍禁忌”等已支持的正常问法保留。

## API变化与实现边界

检索响应增加`guard_version: 1`。被保护阻止的响应带`lookup_complete: false`，不得把这种响应当成完整查找结果。

- `hits`：仅保留本轮规则认为正文可用的候选；不等于医学正确或已批准。
- `related_entries`：范围未解析时的相关母条目名称、ID、标题证据与可供预览的chunk ID，不包含替代用量。
- `unresolved_evidence`：被排除的原始字段及其证据，包含`incomplete_reason`；可能带`reference_target_candidate`与独立的`reference_witness`。该目标名称只是原文读数，尚未证明实体关联正确。

旧客户端不能只判断HTTP 200或是否收到片段；需要识别状态，并把`unresolved_evidence`与普通命中分开。没有增加自动医学实体映射、内容补全或审核通过接口。

字段空正文时，只检查同条目、同粗范围下接续的最多两个字段；中间出现实质正文则停止寻找引用提示。直接引用不会被后续引用覆盖。仅展示提示，不推断不同字段等价。完整正文内的局部引用、任意长距离引用、循环引用展开尚未实现。

名称保护使用明确的炮制前缀和有限的中文上下文规则，保留最长精确已知药名及现有字段词表。它不是完整中文实体识别器，未支持的自然语言可能保守拒绝；不承诺发现所有未知名称或炮制后缀。鲜/生地黄等同一字段内部的多范围也尚未结构化解析。繁体、拼音和新字段别名仍按原能力处理，不为提高本次数字做同义词扩展。

索引的原构建参数及SHA保持不变；保护是索引之后的查询策略，单独冻结检索器与保护模块SHA，不把原索引清单的构建代码SHA当作当前运行策略。历史冻结评测若发现代码变化会拒绝沿用；本轮使用带旧基线关联的[新回归冻结](../audits/mineru_guards_v1/freeze.json)，未修改原50题、原参考或旧结果。

## 验证结果

以下是已知问题修复回归，不是独立新测试或临床正确率：

| 检查 | 修复前 | 修复后 |
|---|---:|---:|
| 50题中的标准字段查询（药名字段） | 20/20 | 20/20 |
| 4个无独立炮制条目未被静默替为母药（两种方法均同） | 0/4 | 4/4 |
| 50题范围状态且无命中（药名字段） | 5/6 | 6/6 |
| 炙甘草空用量标题 | 普通命中 | 未完成片段，并提供引用提示原页 |
| 原28题中的20个标准问题（药名字段） | 20/20 | 20/20 |
| 原28题范围检查／已知修复回归（药名字段） | 4/4、4/4 | 4/4、4/4 |

炙甘草被排除后，50题中“有独立条目的炮制品”候选证据命中由4/4变为3/4。这是停止把空标签计为有效候选，并非已经完成跨条目引用解析；该题仍未完成。字段改述、药名变体分别仍为2/8。BM25在50题范围检查中仍只有1/6满足预定状态，不能拿来直接生成通用问答。

- 104项相关工程测试通过，新增10项覆盖精确炮制名保留、显式筛选不能绕过保护、复合名称、自然问法、多药、共享引用原文/证据、直接引用、部分字段及BM25空标题隔离。
- 12组Chromium页面检查通过，新增炮制品母条目证据、炙甘草两处来源与未知复合名称清空；未完成片段计数单列，原页图片可加载，无外部请求或页面脚本错误。
- 原PDF、索引、研究视图及ETCM正式库SHA保持原值。浏览器截图、完整返回原文和开发中间结果只保留在`var/mineru_guards_v1/`。

审计：[50题回归](../audits/mineru_guards_v1/challenge.json)、[原28题回归](../audits/mineru_guards_v1/original_regression.json)、[页面验收](../audits/mineru_guards_v1/browser.json)、[校验记录](../audits/mineru_guards_v1/validation.json)。

## 运行与下一步

本机页面 <http://127.0.0.1:7869/>。重启命令与PDF依赖见[研究页面运行说明](MINERU_RESEARCH_UI.md)。回归复现使用新的输出目录：

```bash
python -m scripts.eval_mineru_challenge evaluate \
  --freeze audits/mineru_guards_v1/freeze.json \
  --out var/mineru_guards_v1/replay_next

/tmp/tcm-ocr-sample-20261016/bin/python -m unittest \
  tests.test_mineru_guards tests.test_mineru_challenge tests.test_mineru_research \
  tests.test_mineru_pharmacopoeia tests.test_mineru_heading_recovery \
  tests.test_mineru_review_packet tests.test_mineru_captions \
  tests.test_mineru_revisions tests.test_mineru_retrieval \
  tests.test_pharmacopoeia_segmentation -q
```

2026-10-18已完成[独立研究聊天入口](MINERU_RESEARCH_CHAT.md)，以确定性工具路由按状态展示证据或要求澄清，实际验证追问及服务/浏览器重启恢复。没有调用模型或新增LangGraph工作流。尚未接入正式聊天、未人审或发布新Docker镜像。
