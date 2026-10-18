# 50题新问法与炮制范围挑战

2026-10-18完成首轮。结论：当前框架可以支持可追溯的研究检索，但目前的药名子串规则和字段词表不足以承担通用药材问答。下一步优先修复炮制品范围与错误药名匹配，再扩展别名；本轮不调整检索规则，保留失败基线。

## 问题与参考如何固定

- 50个AI新编问题，20标准字段、8字段改述、8药名变体、8炮制品、6范围限制；不是实际用户收集的问题，也不是独立人工盲测。
- [问题文件](../eval/mineru_retrieval_challenge_v1.json)在查询本批结果前冻结，绑定索引和检索器SHA；[冻结收据](../audits/mineru_challenge_v1/freeze.json)保留当时状态。
- 从冻结研究视图独立按预期中文名/字段制作参考，不使用检索输出选择正确答案。40题有候选字段参考；4个炮制品无独立同名条目，按“不直接用母药字段代替已解析炮制范围”的产品要求单列；6题检查范围状态。未把这10题混入字段召回分母。
- 在运行问题前查看43处去重后的原PDF局部裁图，核对标题和第一处字段证据；Gancao、Huangqi、Dangshen可见于原标题。繁简转换是查询文字归一化需求，不表示新增药学别名关系。
- 参考仍来自同一解析语料，局部AI核对不能证明全字段OCR、全文边界、剂量医学正确性或药典真实性。评分要求命中字段ID、药名和证据列表与参考一致，称为“候选证据命中”，不是回答正确率。

参考及SHA见 [参考文件](../audits/mineru_challenge_v1/reference.json)、[参考冻结](../audits/mineru_challenge_v1/reference_freeze.json)和[原图核对记录](../audits/mineru_challenge_v1/source_anchor_review.json)。所有裁图、拼图与完整检索原文均只保留在被忽略的 `var/mineru_challenge_v1/`。

## 实际结果

| 分组 | BM25前5候选证据命中 | 药名＋字段前5候选证据命中 |
|---|---:|---:|
| 标准问法 | 20/20（首位11/20） | 20/20（首位20/20） |
| 字段改述 | 3/8 | 2/8 |
| 药名变体 | 4/8 | 2/8 |
| 有独立条目的炮制品 | 4/4 | 4/4 |

| 行为检查（不计入证据命中率） | BM25 | 药名＋字段 |
|---|---:|---:|
| 无独立条目炮制品未被直接替为母药字段 | 0/4 | 0/4 |
| 超出范围时返回预定状态且无候选 | 0/6 | 5/6 |

首位命中和前5命中仅描述候选字段排序。**炮制品4/4中包含炙甘草的“同甘草”交叉引用，不能解释为4题完整用量查询成功。** 不报告50题总“正确率”，不把范围行为、证据召回和完整内容可用性混成一个指标。

完整分组及逐题元数据见 [评测结果](../audits/mineru_challenge_v1/evaluation.json)，失败与优先级见 [问题清单](../audits/mineru_challenge_v1/failures.json)。没有调用聊天模型、嵌入或外部API，未进行新OCR；本轮不做端到端速度或调用成本比较。

## 需要修复的具体问题

### P0：炮制范围未解析却返回母药

“酒大黄、醋香附、蜜麻黄、盐杜仲”的用量问题分别返回大黄、香附、麻黄、杜仲，状态均为`candidates`。这些炮制名称在母条目内部可能出现，但当前索引只记录粗范围`prepared_slices`，没有证明命中字段属于用户指定炮制品，也没有给出范围未解析提示。这不证明各字段医学内容一定错误，但不能作为已完成的指定炮制品查询。

下一步先增加明确的“炮制范围未解析”状态，阻止静默替代；保留可核对的父条目来源入口。不得为提高通过率直接把所有炮制前缀删除。

### P0：未知复合药名被子串匹配

人工构造的“火星灵芝草”被当成灵芝并返回功能字段。该题检验预先声明的未知名称边界要求，不能因字面含有合法药名就声称未知实体已成功识别。需区分句中正常提及药名与未知名称包含药名的情况，记录解析依据；不能简单拒绝所有带前后缀的自然语言问题。

### P0：跨条目引用未解析

炙甘草原页把用法、注意、贮藏合并指向“同甘草”。当前字段切分只让用量字段命中标签，未解析该来源链接。需保留明确“引用未展开”状态，或按有证据的条目链接展开并同时给出两处出处，限制循环和歧义；不能凭模型补齐。

### P1：字段和药名变化覆盖不足

- “每次多少克、药效、储藏条件、使用警示、服用剂量、取自哪一部分”六题均未识别字段。
- 繁体药名与拼音未识别；含空格的两题可通过现有归一化匹配。
- 地黄的一个字段包含鲜/生两种范围，字段唯一不代表剂型范围唯一。后续范围建模需覆盖字段内部的多个变体。

后续基于此批问题的修复只能报告回归提升；修复后另取新问题验证，不能继续称这50题为独立新评测。

## 复现与工程验证

当前冻结文件已存在，工具拒绝覆盖。重放只需指定新结果目录：

```bash
python -m scripts.eval_mineru_challenge evaluate \
  --out var/mineru_challenge_v1/replay_next

# 重建原图核对包，需PyMuPDF/Pillow；同样选择新的输出目录。
/tmp/tcm-ocr-sample-20261016/bin/python -m scripts.build_mineru_challenge_review \
  --out var/mineru_challenge_v1/review_next

/tmp/tcm-ocr-sample-20261016/bin/python -m unittest \
  tests.test_mineru_challenge tests.test_mineru_research \
  tests.test_mineru_pharmacopoeia tests.test_mineru_heading_recovery \
  tests.test_mineru_review_packet tests.test_mineru_captions \
  tests.test_mineru_revisions tests.test_mineru_retrieval \
  tests.test_pharmacopoeia_segmentation -q
```

新实验可显式指定`--questions/--freeze/--references/--view`运行`prepare`，然后核对原图，最后运行`evaluate`；`reference_freeze.json`与指定freeze文件同目录。问题、参考或检索器SHA变化时拒绝沿用冻结实验。

94项相关工程回归通过，新增8项检验错误证据不能计分、排名、重复字段不完整、母药替代、范围状态、问题/参考篡改拒绝和真实索引评测。两次运行逐题结果一致（排除耗时），43裁图重建SHA一致。检索器、索引、研究视图和正式ETCM库SHA未改，见 [验证记录](../audits/mineru_challenge_v1/validation.json)。工程测试通过不表示50题全部通过。尚未做人审、接入正式聊天或发布Docker镜像。
