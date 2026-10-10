# ETCM2.0 数据映射草案

日期：2026-10-10。适用于当前本地快照，不代表 ETCM 官方完整数据规范。原始目录只读；本文件是后续 JSON loader / repository 的实现依据，首批药材基础字段已实现 SQLite 存储，见 [MVP 使用说明](ETCM2_MVP_USAGE.md)；下列词表映射、交叉链接配对及中文名称补齐仍为后续设计。

## 1. 实际源结构

四个目录分别对应药材、成分、靶点与疾病，每个文件是接口响应：`code / msg / data / name / page_title`，部分含 `complete_fetch_summary`。

`data` 是 section 数组。药材基础字段位于 `id=base_information` 的 `value` 数组，每项为 `key/value`，可附 `linkformat`。关联记录位于 `id=related_table` 中按 `type` 区分的表。不能把所有 JSON 当作同一种平面记录，也不能把整个响应直接送入向量化。

成分记录需要递归解析 `base_information.children`。例如化学名称出现在 `Physicochemical_property → Basic_Information → value`，不能因外层 `value=""` 就认定没有基本信息。

## 2. MVP 药材字段映射

以下源字段均指 `base_information.value` 中的 `key`。最终须保存真实数组位置对应的 JSON Pointer；表内逻辑路径仅用于说明。

| 源字段 | 建议目标字段 | 处理规则 | 初期用途 |
|---|---|---|---|
| Herb Name in Pinyin | name_pinyin_raw / name_pinyin_normalized | 保留原值；去首尾空格；搜索键另做 casefold；不要凭拼音推定中文 | 精确查询、候选解析 |
| 无对应中文字段 | name_zh | 默认 null；通过可核验名称表或人工复核补充，记录来源 | 中文入口，不能静默猜测 |
| 无别名专用字段 | aliases | 未提供；分别保留已确认别名与待核验候选，不把 null 当“无别名” | 后续中文召回 |
| Herb Name in Latin | latin_name_raw / latin_name_text | 保留 HTML 原值，用 HTML 解析器提取文本；不借此自动决定唯一基原 | 查询、展示 |
| Herb Name in English | english_name_raw / english_name_text | 同上；该字段可能与拉丁名相同，不自动视为另一别名 | 查询、展示 |
| Family | family_raw | 原值保存；后续受控词表归一化 | 展示、筛选 |
| Habitat | habitat_raw / habitat_terms | 原值优先；仅按验证过的规则拆分 | 展示、筛选 |
| Medicinal Part | medicinal_part_raw / medicinal_part_terms | 保留原值与标准化映射版本；缺失不补推 | 区分部位 |
| Property | properties_raw / properties | 字典映射保留英文、中文值与映射状态；未知词保留 | 属性筛选 |
| Flavor | flavors_raw / flavors | 支持逗号及空格，受控词表映射；缺失状态单独记录 | 属性筛选 |
| Meridian Tropism | meridians_raw / meridians | 同一值可能带/不带 meridian 后缀；先枚举词表再统一 | 属性筛选 |
| Herb classification based on efficacy | efficacy_class_raw | 只当原库分类，不当疗效验证 | 展示、筛选 |
| Indications | indications_raw | 当前语义混合：有的为疾病/症状，有的为传统功效表述；初期不强制拆成 effects + indications | 原文展示、受限语义检索 |
| PubMed | reference_pmids | 保留 ID；校验格式、去重；不把“有关联 PMID”当作每个字段均有证据 | 后续文献查证 |
| Database Cross References | cross_references | 保存标签与 linkformat.value.param；仅在长度对应时配对，否则进入异常清单 | 来源核对 |

`source_namespace` 固定为本地 ETCM2 快照标识，并附 dataset_version。
`source_record_id` 标识一份源记录，`herb_id` 标识核验后的药材实体，两者不要混用。
可先按目录相对路径生成确定性的源记录 ID，并保存迁移映射；文件更名不能静默生成第二份实体。
重复记录、同名异物或炮制差异未解决前，不自动合并为同一 herb_id。

## 3. 来源与缺失值契约

每个字段/关系至少可追溯到：

```text
source_namespace + dataset_version
relative_file_path + file_sha256
section_id + JSON Pointer
raw_field_key + raw_value
normalization_rule_version + review_status
```

`complete_fetch_summary` 是抓取过程自报统计，必须保留；其中 `status=ok` 不等于科学内容可靠，`expected=actual` 不等于全站数据完整。`skipped` 单独标注，不能改成已完整抓取。

使用 `missing / present / malformed / unresolved` 等显式状态；可选字段缺失用 null 或缺失标记，不将缺失当成已确认空集。来源未核验、中文名称未映射应能展示给用户。

源文件目录位于项目外部。后续通过只读挂载或导入器显式路径访问；不复制 169 GiB 原始响应进仓库、Docker 构建上下文或公开静态目录。

## 4. 关联表拆分

| 药材 related_table.type | 初期目标 | 约束 |
|---|---|---|
| ingredient | herb_ingredient_source_rows | 保存原成分名称、属性与记录定位；名称匹配前先有独立成分身份审计 |
| target | herb_target_source_rows | 保存 UniProt / Gene Symbol 作为核对候选；当前靶点文件有错配风险，暂不连成可信知识图谱 |
| disease | herb_disease_source_rows | 原标签为 Enriched Diseases 时保留该语义，不改写为已验证治疗适应证 |
| chinese_patent_drug | herb_patent_formula_source_rows | 先保存关联与来源，后续单独去重成方剂实体 |
| traditional_chinese_medicine_formula | herb_traditional_formula_source_rows | 保留古籍字段与方剂名；不能把方剂关联推成单药临床证据 |

以上都是**源表记录**，并非已经去重、方向一致且证据核验完毕的关系数量。

`linkformat` 是链接模板/展示信息；`basic_network`、热图、富集图、分布图通常包含派生图表结构。初期排除出检索正文；如后续使用，应单独验证实体 ID、关系类型、计算方法与统计含义。

对 QED、FDAMDD、ADMET 等字段保留原始名称、value、unit、quality 与所在 section，不根据字段名称猜测单位、实验来源或临床含义。`quality` 标签不能当成临床证据质量等级。

## 5. 中文名称补齐策略

1. MVP 可先支持拼音、拉丁名和英文原名，UI 明确当前名称覆盖范围。
2. 中文查询需要有来源的 `Chinese name ↔ source entity` 映射表；优先处理明确的少量实体，逐条验证。
3. 对同音、异体字、地方名、同名异物、部位和炮制名保留一对多关系，需要时向用户澄清。
4. 机器翻译或 LLM 建议只能进入候选/待审表，不进入已确认实体字段。
5. 当前目录不能直接支撑完整的“中文药名＋别名查询”；先通过小范围映射建立验收集。

## 6. 第一批实现边界

**先做：** 药材基础信息 loader → 原始记录表 → 规范化字段表 → 拼音/ID 精确查询 → 原文与来源展示。

**暂缓：** 全量成分/靶点/疾病入库、关系推理、网络药理学、多 Agent 自动科研报告，以及将所有关联表向量化。

首批回归应包含：标准记录、字段稀疏记录、HTML 名称、首尾空格/重复名称、未知词项、中文未映射、无记录、重复导入、同一源记录更新、异常响应隔离。

源数据来源和使用/分发范围仍需确认；本次只读审计不代表已确认可以公开分发原数据。
