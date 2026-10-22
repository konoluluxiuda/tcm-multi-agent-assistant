# 疾病来源靶点索引

日期：2026-10-23。对应 P2a：全目录本地审计、版本化SQLite索引和可追溯查询。该索引尚未接入聊天或候选药材排序，也没有建立外部核验的疾病基因真值集。

## 为什么单独建库

药材JSON中的疾病富集表，和疾病JSON中的靶点表是两类来源记录。本次只从 `etcm_diseases` 的 `related_table/type=target` 提取靶点，不将药材富集关系倒推成疾病基因答案。

疾病名称、Gene Symbol 和 UniProt 保留原文，原行可按文件SHA与JSON Pointer核对。以内容哈希生成来源ID，重复内容只索引一次；同名不同内容保持不同来源并返回歧义，不猜外部疾病ID、不做中文自动翻译或同义词合并。UniProt只通过现有目录一致的格式检查，不代表已核验标识和物种。

## 本轮全量结果

7705份JSON全部扫描，共34,542,210,363字节。当前严格来源一致性检查通过7593条，其中7582条有可用靶点、11条没有靶点表。112条文件名不匹配暂不入库：109条是斜线转下划线的候选、2条是冒号转下划线的候选、1条疑似文件名编码问题，不等于112条内容错误。

| 项目 | 结果 |
| --- | --- |
| 入库原始靶点行 | 2,047,010 |
| 不同来源UniProt标识 | 563 |
| 来源内重复标识行 | 43,565，原行仍保留 |
| 无明确UniProt行 / Gene Symbol冲突来源 | 0 / 0 |
| 来源自报完整 / skipped / 未记录状态 | 7504 / 75 / 14 |
| 缺少明确物种字段 | 7593条来源，不能自动标为人类 |
| 落入药材背景 / 背景外标识 | 549 / 14，药材背景共有962个标识 |
| 完全相同的非空靶点集合 | 381组，涉及1632条来源；最大一组110条 |

相同集合的疾病无法仅靠当前靶点重叠方法区分。后续排序须报告这种限制，并使用独立资料验证疾病特异性，不能将来源关联排序包装成疾病特异性预测。

全量离线回归 **92模块843项通过**，跳过和网络尝试0。独立SQL核对全库行数、去重标识与孤立行；13份原始JSON的3554行逐条核对原行、位置、SHA及完整靶点集合。实际CLI查询返回283个标识并核对当前页20条原行。此验证不构成医学有效性评估。

## 本地运行

在项目根目录，使用已安装依赖的Python环境。实际目录见下方命令，输出目录必须是新的，不能覆盖已有索引。

```bash
python -m scripts.build_etcm_diseases \
  --source /home/zry/workspace/HDCTI/dataset/ETCM2.0 \
  --enrichment var/research_update_20261020/runtime_catalogs/catalogs/enrichment.sqlite3 \
  --version disease-local-2026-10-23 \
  --out var/disease_catalog_v1/new-build
```

本次生成的数据库位于 `var/disease_catalog_v1/build/diseases.sqlite3`。按来源原名查询并核对当前页原始文件：

```bash
python -m scripts.query_etcm_diseases \
  --database var/disease_catalog_v1/build/diseases.sqlite3 \
  --name 'Dermatosis Papulosa Nigra' \
  --verify-source /home/zry/workspace/HDCTI/dataset/ETCM2.0
```

也可使用返回的 `etcm-disease:...` 来源ID查询。每页20条原始行，`--page 2`翻页；返回完整去重标识集合、原始行数、重复/缺失标识、Gene Symbol冲突、抓取记录和背景覆盖。没有匹配时返回 `not_found`；多个同名来源返回选项，不合并。数据更改后，`--verify-source`拒绝旧SHA或不一致的原行。

## 数据质量与发布约束

- 核对 `Disease Name` 与文件名、已有页面名/标题的一致性；缺失、冲突和结构异常进入文件级审计，不补造身份。严格文件名不匹配可能来自 `/` 等字符转义，不能直接认定来源内容错误；本次没有放开所有标点匹配。
- 缺少、空列表、多值或格式异常的UniProt保留原行并计为缺失，不参与去重集合。同一标识不同Gene Symbol保留并报告冲突。
- `source_reported_complete`仅说明来源标记ok、预期/实际/存储行数一致且只有一张靶点表；不代表已独立证明抓取完整。skipped、计数不符、缺少状态分别保留；没有靶点表不是生物学负例。
- 物种只保留明确出现的物种/organism/taxon类字段。缺少时标记 `not_recorded`，不从疾病名称或Gene Symbol猜测人类；外部疾病标识仍为 `not_mapped`。
- 与药材背景的交集仅用于计算覆盖，绑定背景数据版本、来源指纹和靶点集合哈希。背景外标识保留，不静默丢弃后重新定义分母。
- 原始资料只读。独立新目录中先写 `catalog.building.sqlite3`，全部处理、快照变化检查和SQLite完整性检查完成后才生成 `diseases.sqlite3`。失败不会覆盖旧库，读取器拒绝未完成目录；逐文件结果在 `files.jsonl`，统计在 `summary.json`。
- 单文件解析上限16 MiB，拒绝符号链接和越界路径；开始/结束核对整个目录的文件状态，来源内容SHA绑定到索引。未扫描无关PDF、成分或靶点目录。

## 实现与验证范围

`tcm/disease_catalog.py`提供解析、只读查询和原行核对；`scripts/build_etcm_diseases.py`负责批量审计/构建；`scripts/query_etcm_diseases.py`为本地命令行入口。无需新框架、向量库或模型调用，不修改现有聊天开关、记忆、药材目录或数据库Schema。

`tests/test_disease_catalog.py`覆盖计数/溯源、身份冲突、内容去重、同名歧义、空表、缺失标识、物种与抓取状态、版本变化、未完成索引、只读约束、目录隔离、符号链接和分页。完整测试与全量实际数据核对结果见[验收记录](../audits/disease_catalog_v1/summary.json)。独立原始JSON/SQL核对验证提取准确性，不是疾病关联的生物学独立验证。

## 后续接入与回滚

下一步在现有分析注册表增加疾病资料查询，明确选择疾病来源后，按该来源靶点集合与药材集合做候选排序。需要同时展示原疾病集合、药材背景内集合及缺失部分，并保留未核验物种/来源完整性的状态。首版定位来源关联探索，不能输出治疗推荐或将分数叫疗效概率。

这一步尚未切换运行服务；停止使用新派生目录即可回滚，旧药材查询和聊天不受影响。未来上线应通过配置整体切换已完成目录，保留上一版本；不得覆盖原JSON或原地改写生产索引。本轮未提交Git、未发布Docker，外部模型与网络调用均为0。
