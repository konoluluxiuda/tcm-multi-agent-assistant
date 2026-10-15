# 独立 AI 来源与契约复核

日期：2026-10-14。审核者为同一平台的独立 AI 子代理；并非另一模型提供商，未作不同模型声明，不是人类审核或科学签核。

套件 SHA-256：`8d3ff70dda9d1e9d4530bdc7830f63bcbcca0131677e4f23daf67c9db044be72`。源目录共 2076 文件；冻结快照 JSON 对指纹：`9921499429128fa21b4b8dc2c5100f359970d8fa796a1dcee32ca8259fe02d80`。盲推清单指纹（TAB/LF）：`5383a6e18d7fbd4f8f55f38575e0074fef5fffc6438fb2301e6ab54c8396c80d`。两个指纹使用不同序列化约定，含相同来源路径及文件哈希。

## 来源一致性

核对 66 冻结记录、858 字段的缺失/存在状态、原值、规范值、原字段名及 JSON Pointer，文件 SHA 逐条对照。盲推仅由预测结果路径选出66条（实际数量见JSON）代表记录，保留完整原字段项及基础 section供追溯；全量来源仅保留路径/哈希清单。首次全量盲推产物已移至/tmp，仓库子集保留原件SHA和选择依据。发现 8 项不一致；另有 5 项合理展示规范差异。普通字段末尾空白在盲推中保留，而冻结值去除；这类差异不改冻结答案，原值仍逐字检查。8项HTML名称差异（若当前套件存在）是冻结保留不换行空格U+00A0、盲推折叠为普通空格；按字面值仍列为不一致，未自动修改或接受冻结展示规范。

## 契约一致性

30 工程用例的状态、结果路径、筛选计数、分页及比较项均逐项检查。源值参与推导的用例 21 项；另有 9 项纯契约或条件性用例。这些仍共享产品字段映射、精确名称/子串匹配、AND、排序与分页规范，可能共享规范错误。全部用例不能称为独立数据真值或真实任务正确率。

未映射中文、缺版本、非法参数及 readiness 使用显式产品/API 契约。版本导入状态、SQLite schema/就绪性无法从源 JSON 证明。本脚本没有读取 SQLite、调用生产查询、模型或 API。异常类别、HTTP 状态和错误语义已核对；5条（若套件包含）精确异常文案不是盲推得到，review.json 明确标为未独立验证。HTTP 空筛选文案与 repository 文案可合理不同。

## 真实任务缺失与人工复核

当前只覆盖结构化检索与字段对齐；不验证药材实体科学归一化、中文别名映射、术语分类、临床效力/安全性、PMID证据支持性或真实医疗问答。尤其 Cold 子串包含 Mildly cold 等修饰词，417不是受控词表的精确类别数。

建议人工逐项复核中文名称/同名来源身份、修饰词与缺失含义、来源条目中的链接与 PMID，以及代表性真实用户任务；记录审核人、日期和依据，再做人工签核。需另以隔离环境验证缺版本与就绪性契约，并决定是否将精确异常文案作为外部稳定规范。

## 可重复过程与边界

先运行 `--phase blind`，仅提取套件 id/operation/input，独立遍历源 JSON 后保存 blind_predictions.json；随后 `--phase compare` 才读取 expected/records 和答案复核文档。`--phase all` 按同样顺序执行。脚本受套件 SHA 和源指纹绑定，漂移会阻止对照。盲推虽然标准 JSON 解码整份套件，但不访问答案对象；这是流程隔离，不是密码学答案屏蔽。

首次盲推普通值未 trim 的规范保留；比较阶段显式记录可解释规范差异，不重写盲推或冻结答案。原文件以只读方式打开，没有源目录、冻结 JSON 或 SQLite 写入。该可重复审计器检查来源与契约一致性，并不独立判定科学正确性。

## 用例逐项结果

| 用例 | 核对结果 | 依据 |
|---|---|---|
| 01-detail-huangqin | 已核对，受上述边界约束 | source_values + explicit normalization/identity contract |
| 02-name-case-and-space | 已核对，受上述边界约束 | source_values + explicit normalization/identity contract |
| 03-latin-name-html-normalized | 已核对，受上述边界约束 | source_values + explicit normalization/identity contract |
| 04-detail-gancao | 已核对，受上述边界约束 | source_values + explicit normalization/identity contract |
| 05-detail-danggui | 已核对，受上述边界约束 | source_values + explicit normalization/identity contract |
| 06-detail-renshen | 已核对，受上述边界约束 | source_values + explicit normalization/identity contract |
| 07-sparse-basic-fields | 已核对，受上述边界约束 | source_values + explicit normalization/identity contract |
| 08-ambiguous-huashi | 已核对，受上述边界约束 | source_values + explicit normalization/identity contract |
| 09-specific-huashi-source | 已核对，受上述边界约束 | source_values + explicit normalization/identity contract |
| 10-chinese-name-unmapped | 已核对，受上述边界约束 | product_contract: Chinese mapping deliberately unsupported |
| 11-unknown-name | 已核对，受上述边界约束 | source_values + explicit normalization/identity contract |
| 12-unknown-version | 已核对，受上述边界约束 | product_contract: version is external import state; source JSON cannot prove absence |
| 13-missing-pubmed-field | 已核对，受上述边界约束 | source_values + explicit normalization/identity contract |
| 14-family-first-page | 已核对，受上述边界约束 | source values + product substring/AND/sort/pagination contract |
| 15-family-second-page | 已核对，受上述边界约束 | source values + product substring/AND/sort/pagination contract |
| 16-family-last-page | 已核对，受上述边界约束 | source values + product substring/AND/sort/pagination contract |
| 17-page-beyond-total | 已核对，受上述边界约束 | source values + product substring/AND/sort/pagination contract |
| 18-family-and-flavor | 已核对，受上述边界约束 | source values + product substring/AND/sort/pagination contract |
| 19-property-and-flavor | 已核对，受上述边界约束 | source values + product substring/AND/sort/pagination contract |
| 20-no-filter-results | 已核对，受上述边界约束 | source values + product substring/AND/sort/pagination contract |
| 21-empty-filters | 已核对，受上述边界约束 | external explicit product/API contract; not JSON truth |
| 22-unsupported-filter | 已核对，受上述边界约束 | external explicit product/API contract; not JSON truth |
| 23-negative-offset | 已核对，受上述边界约束 | external explicit product/API contract; not JSON truth |
| 24-excessive-limit | 已核对，受上述边界约束 | external explicit product/API contract; not JSON truth |
| 25-compare-traditional-fields | 已核对，受上述边界约束 | source values + product compare contract |
| 26-compare-ambiguity-and-missing | 已核对，受上述边界约束 | source values + product compare contract |
| 27-compare-sparse-and-missing-reference | 已核对，受上述边界约束 | source values + product compare contract |
| 28-compare-unknown-version | 已核对，受上述边界约束 | product_contract: import state cannot be inferred from source JSON |
| 29-empty-compare | 已核对，受上述边界约束 | external contract; API empty names forwarded to repository |
| 30-dataset-readiness | 已核对，受上述边界约束 | conditional contract: requires successful import/schema; source availability alone does not prove DB readiness |

核对项共4585，实际验证4580，未独立验证5。未验证项ok=null，不计为通过；8项字面差异仍保留为mismatch。

## 不一致详情

```json
[
  {
    "key": "etcm_herbs/CuMaoGanCao.json.latin_name.value",
    "expected": "Glycyrrhiza aspera Pall.",
    "observed": "Glycyrrhiza aspera Pall.",
    "ok": false,
    "classification": "reasonable HTML whitespace convention difference; kept as literal mismatch"
  },
  {
    "key": "etcm_herbs/CuMaoGanCao.json.english_name.value",
    "expected": "Glycyrrhiza aspera Pall.",
    "observed": "Glycyrrhiza aspera Pall.",
    "ok": false,
    "classification": "reasonable HTML whitespace convention difference; kept as literal mismatch"
  },
  {
    "key": "etcm_herbs/DuBianDou.json.latin_name.value",
    "expected": "Physostigma venenosum Balf.",
    "observed": "Physostigma venenosum Balf.",
    "ok": false,
    "classification": "reasonable HTML whitespace convention difference; kept as literal mismatch"
  },
  {
    "key": "etcm_herbs/DuBianDou.json.english_name.value",
    "expected": "Physostigma venenosum Balf.",
    "observed": "Physostigma venenosum Balf.",
    "ok": false,
    "classification": "reasonable HTML whitespace convention difference; kept as literal mismatch"
  },
  {
    "key": "etcm_herbs/Huai.json.latin_name.value",
    "expected": "Sophora japonica L.",
    "observed": "Sophora japonica L.",
    "ok": false,
    "classification": "reasonable HTML whitespace convention difference; kept as literal mismatch"
  },
  {
    "key": "etcm_herbs/Huai.json.english_name.value",
    "expected": "Sophora japonica L.",
    "observed": "Sophora japonica L.",
    "ok": false,
    "classification": "reasonable HTML whitespace convention difference; kept as literal mismatch"
  },
  {
    "key": "etcm_herbs/ZiSuiHuai.json.latin_name.value",
    "expected": "Amorpha fruticosa L.",
    "observed": "Amorpha fruticosa L.",
    "ok": false,
    "classification": "reasonable HTML whitespace convention difference; kept as literal mismatch"
  },
  {
    "key": "etcm_herbs/ZiSuiHuai.json.english_name.value",
    "expected": "Amorpha fruticosa L.",
    "observed": "Amorpha fruticosa L.",
    "ok": false,
    "classification": "reasonable HTML whitespace convention difference; kept as literal mismatch"
  }
]
```
