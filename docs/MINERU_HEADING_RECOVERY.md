# MinerU无标题级别漏切修复与来源身份迁移

2026-10-17。已修复本轮确认的4处漏切，当前研究候选为616条，索引为 `var/mineru_retrieval_v2/`。全部原始识别内容保留，修订及候选仍未人审，未接入正式聊天知识库。

## 修复了什么

原v3只把带 `text_level` 的文本当作标题起点。MinerU部分药名、拼音与拉丁名有文字但没有该标记，导致整条药材并入上一条。

v4新增受约束的回退识别：中文药名、拼音、拉丁名同时存在，标题块对齐且间距合理，并在正确阅读方向附近出现来源句。支持来源句跨栏或接下一页；图表caption不得作为药名标题或来源佐证。缺少佐证时不猜测边界。

对413页同类模式扫描发现以下4个可恢复标题，均已核对原图的标题/来源起始位置、条目末尾及下一标题：

| 恢复条目 | 原先混入 | 章节标题页 | 高清原PDF物理页 |
|---|---|---:|---:|
| 木棉花 | 木通 | 65 | 86 |
| 白头翁 | 白术 | 109 | 130 |
| 半边莲 | 玄参 | 123 | 144 |
| 满山红 | 矮地茶 | 386 | 407 |

扫描另命中两段来源句的字面模式，因不满足标题及后续来源关系而拒绝。该扫描只覆盖“短中文名＋拼音/拉丁名”的特定模式，不能证明全文没有其他漏切。四条是AI原图核对，不是人工金标准；新增条目保留 `heading_level_missing_boundary_inferred` 风险标记。

结果由612增至616候选，检索字段块由8650增至8654；增加主要来自正确恢复来源字段。616不是已经独立核验的原书条目总数。

## 稳定来源身份与修订重基

旧的顺序型ID会随插入条目整体移动。新ID由原PDF SHA、物理页、原标题坐标及原标题块内容指纹生成；序号另存 `ordinal`。稳定性限定于同一冻结OCR导入，不承诺重新OCR后仍保持同一ID。

- 原612个标题均唯一匹配到新来源ID，新4条各有独立身份；旧ID→新ID映射保存于审计和修订输出。
- 迁移通过原始标题来源匹配，不按药名相似或旧编号位置猜测；标题变化或多对一来源映射会拒绝。
- 原11条AI修订全部重基。字词替换还要求原始块、源页、原观察值和字符范围不变；字段索引可以变化，替换不得漂到另一条药材。
- 原始PDF、15,812个识别块内容及blocks文件SHA不变；14,568个正文块仍各有唯一归属，18个caption/脚注元素保留。
- 修订可全部关闭，并与同批未修订研究视图一致；旧候选、修订和失败索引不覆盖。

## 重新评测

排名算法、词表、参数和原28个问题没有改动。24处定位参考按相同标题来源及字段首处证据重基，仅更新ID、字段位置和实际重复字段数。它们仍是原AI来源辅助参考，不是新建独立金标准。

原20题开发集重跑结果：

| 方法 | 前5证据命中 | 无歧义首位命中 | MRR@5 |
|---|---:|---:|---:|
| 全库BM25 | 18/20（原18/20） | 15/20（原13/20） | 0.825（原0.800） |
| 药名＋字段 | 20/20（原20/20） | 20/20（原18/20） | 1.000（原1.000） |

白术的两处字段歧义已消除。另对木通/木棉花、白术/白头翁、玄参/半边莲、矮地茶/满山红八个名称逐一查询用量，均仅返回对应药材的一处字段。该检查属于修复回归，不是新的盲测。

范围/缺项四题仍为BM25 0/4、字段路由4/4；既有四题修订回归两方法均4/4。BM25的丹参禁忌、陈皮保存两题仍未在前5命中，不掩盖失败，也没有据本批结果调参。原基线与本轮完整报告分别保存在原目录和 `var/mineru_retrieval_eval_v2/`。

本轮暖查询均值约为BM25 8.89 ms、字段路由0.20 ms，只是本机进程内查询耗时，不代表HTTP、Docker或LLM端到端性能。

## 当前文件与使用

| 产物 | 路径 |
|---|---|
| 当前解析候选 | `var/mineru_pharmacopoeia_v4_checked/` |
| 616条研究视图及11条修订台账 | `var/mineru_revisions_v2/` |
| 原612条来源映射 | `var/mineru_revisions_v2/source_rebase.json` |
| 新索引 | `var/mineru_retrieval_v2/` |
| 本轮评测 | `var/mineru_retrieval_eval_v2/` |
| 4条原图首尾核对 | `var/mineru_boundary_fix_v4/` |
| 脱敏审计 | [audits/mineru_boundary_v4](../audits/mineru_boundary_v4/) |

默认查询和评测脚本现使用上述新版研究视图/索引。查询仍只返回未审核候选和引用，不生成回答。

```bash
python -m scripts.query_mineru_research '白术的用量是多少？'
python -m scripts.query_mineru_research '白头翁的用量是多少？'

# 完整重建需使用新的输出目录。
python -m scripts.build_mineru_pharmacopoeia --out var/mineru_v4_next
python -m scripts.build_mineru_revisions --base var/mineru_v4_next --out var/mineru_revisions_next
python -m scripts.build_mineru_retrieval --revisions var/mineru_revisions_next --out var/mineru_index_next
python -m scripts.rebase_mineru_retrieval_reference \
  --new-base var/mineru_v4_next --new-revisions var/mineru_revisions_next \
  --out var/mineru_reference_next.json
python -m scripts.eval_mineru_retrieval --index var/mineru_index_next \
  --view var/mineru_revisions_next/research_view.json \
  --references var/mineru_reference_next.json --out var/mineru_eval_next

python -m unittest tests.test_mineru_pharmacopoeia \
  tests.test_mineru_heading_recovery tests.test_mineru_review_packet \
  tests.test_mineru_captions tests.test_mineru_revisions \
  tests.test_mineru_retrieval tests.test_pharmacopoeia_segmentation -q
```

78项相关工程回归通过，新增13项覆盖缺级别标题、来源距离/对齐、跨栏跨页、caption拒绝、稳定ID、来源冲突及修订目标漂移。解析、修订、索引均实际重建，SHA一致；原始问题文件SHA未变。

## 下一任务

新版只读检索已接入[研究页面](MINERU_RESEARCH_UI.md)，支持原文/AI修订对照、字段/标题原页证据及未审核标记。下一步补充未用于这次修复的新问题，继续验证名称/字段别名、多个炮制范围及词法检索失败；不把这次20题回归当作独立泛化证明。
