# 固定验收集复核说明

日期：2026-10-12。M1-06 的来源对照与自动验收已完成，人工签核待完成。

## 直接核对的代表记录

下列仅记录源库值，未评价其科学正确性。所有值及字段位置可在固定 JSON 的 records 中展开。

| 源文件 | 应核对内容 | 预期 |
|---|---|---|
| HuangQin.json | Property / Flavor / Family | Cold / Bitter / Labiatae |
| GanCao.json | Flavor / Medicinal Part | Sweet / Root, Rhizome |
| DangGui.json | Family / Property | Apiaceae / Warm |
| RenShen.json | Property | Mildly warm，保留修饰词 |
| BaiShao.json | PubMed | 原库基础字段未提供，返回 missing |
| HuaYeEZhangChai.json | 基础字段稀疏 | 仅 4 项基础字段；Property 等明确 missing |
| HuaShi .json 与 HuaShi_.json | 拼音 / 身份 | 原名均带末尾空格；返回两个来源，不自动合并 |

## 固定用例清单

筛选结果集由独立源文件遍历取得，不从被测查询函数取得。排序为相对来源路径。

| ID | 操作 | 预期状态 | 匹配总数（筛选） |
|---|---|---|---:|
| 01-detail-huangqin | lookup | ok | — |
| 02-name-case-and-space | lookup | ok | — |
| 03-latin-name-html-normalized | lookup | ok | — |
| 04-detail-gancao | lookup | ok | — |
| 05-detail-danggui | lookup | ok | — |
| 06-detail-renshen | lookup | ok | — |
| 07-sparse-basic-fields | lookup | ok | — |
| 08-ambiguous-huashi | lookup | ambiguous_entity | — |
| 09-specific-huashi-source | lookup | ok | — |
| 10-chinese-name-unmapped | lookup | unsupported_name_mapping | — |
| 11-unknown-name | lookup | not_found | — |
| 12-unknown-version | lookup | dataset_not_found | — |
| 13-missing-pubmed-field | lookup | ok | — |
| 14-family-first-page | filter | ok | 127 |
| 15-family-second-page | filter | ok | 127 |
| 16-family-last-page | filter | ok | 127 |
| 17-page-beyond-total | filter | ok | 127 |
| 18-family-and-flavor | filter | ok | 49 |
| 19-property-and-flavor | filter | ok | 417 |
| 20-no-filter-results | filter | ok | 0 |
| 21-empty-filters | filter | ValueError | — |
| 22-unsupported-filter | filter | ValueError | — |
| 23-negative-offset | filter | ValueError | — |
| 24-excessive-limit | filter | ValueError | — |
| 25-compare-traditional-fields | compare | ok | — |
| 26-compare-ambiguity-and-missing | compare | ok | — |
| 27-compare-sparse-and-missing-reference | compare | ok | — |
| 28-compare-unknown-version | compare | dataset_not_found | — |
| 29-empty-compare | compare | ValueError | — |
| 30-dataset-readiness | ready | ready | — |

## 签核项目

- [ ] 确认精确名称匹配、HTML 纯文本名称和空白/大小写归一化符合预期。
- [ ] 确认拼音同名返回多个来源；来源 ID 不能当作已核验的药材实体 ID。
- [ ] 确认缺失 PMID 不能解释成“没有研究”，存在 PMID 不能解释成每项字段均有证据。
- [ ] 确认筛选为英文原文子串 AND 条件，417 条 Cold + Bitter 结果不是精确术语分类。
- [ ] 核对候选集中的预期字段、原值、指针和校验和，并确认数据版本。
- [ ] 记录人类审核人、日期、修改依据，通过 Git 留下审定记录。

## 当前结果

数据层 30/30、Docker HTTP API 30/30 通过；评测器 7 项故障/契约回归通过。字段错误、错误来源位置会被检测；快照变化阻止评测。原始数据、SQLite 和冻结候选集均未被评测过程修改。

候选集： [eval/tcm_golden_v1.json](../eval/tcm_golden_v1.json)。运行方式：[eval/README.md](../eval/README.md)。结果：[验收报告](../audits/tcm_golden/report.md)。
