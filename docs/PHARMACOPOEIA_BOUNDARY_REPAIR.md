# 药典边界修复：缺字、漏行与未知身份

本轮修复条目标题存在、但来源或名称识别不完整时正文被并入前一味药的问题。最终切分器为v5；原30页失败报告、两次新增页面暴露的问题和各版源码指纹均保留，没有用修复后的结果覆盖旧基线。

## 最终行为

| 原始OCR情况 | 处理结果 |
|---|---|
| 中文名、拼音、拉丁名对齐，但来源首行消失 | 切出独立候选；来源未知，不继承前药内容 |
| 来源开头为“本品以”“本品系” | 允许提取来源候选，与条目边界分别判断 |
| 中文标题只剩一个字 | 仍作为待核实边界，标记身份未解决；不补药名 |
| 标题包含中文括号别名 | 保留整条标题及括号内容为原文候选，不拆成两味药、不自动映射别名 |
| 疑似拼音乱码，但拉丁名、来源开头及对齐均明确 | 保留边界，拼音字段状态为 unresolved_ocr_value，原始乱码保留 |
| 中文标题整行消失，只剩对齐的拼音、拉丁名与来源 | 切出中文名为 null 的条目，身份未解决，不借拼音推断药名 |

正常标题依据同栏、连续间距、横向对齐的中文/拼音/拉丁名组合识别；缺中文名或拼音格式损坏时，还必须观察到同栏邻近的来源开头。单独的英文行、跨栏组合、缺少来源的损坏组合、明显错位等不作为恢复依据。已识别标题内部的拼音行不会重复创建条目。

“切出边界”与“识别正确”是不同状态：

- `identity_status: unresolved` 表示标题缺字、缺中文行、乱码或低分等异常；其余也只是 `unverified_ocr`。
- `source_status: unresolved_missing_source_opener` 表示来源首行缺失，原文字段仍为未知；标题中的拼音、拉丁名可保留独立证据，不依赖来源存在。
- `title_flags`、`requires_source_review` 提供待复核线索；高分错字也可能没有自动报警，不能只凭置信分数放行。
- 所有候选 `human_signoff: false`、`canonical_entity_mapping: null`。完整来源候选也不等于专业内容正确。

原图、OCR行、文字分数、坐标与页码均保留。归一化标题仅用于机器候选，原文行不改写。饮片、附属质量标准继续分节，跨来源/缺页不接续。现有字段词表、未知炮制品映射、跨栏断开的标题等限制仍存在。

## 真实结果与参考的边界

本轮经历两次新页面暴露问题：第一次8页发现括号标题和乱码拼音；第二次4页发现中文标题整行消失。它们都已用于修复，**现为开发回归集，不能继续称为最终版本的独立验证**。各轮失败报告保留，分别记录2处和1处漏切。

最终v5复用原始OCR，重建以下三组结果；没有重新OCR这些42页：

| 已知回归组 | 修复前检测/AI参考标题 | 最终v5检测/参考 | 尚存名称差异 |
|---|---|---|---|
| 原30页 | 36/44 | 44/44 | 5 |
| 新增8页，已转为回归 | 12/14 | 14/14 | 0；急性子的拼音仍为乱码状态 |
| 随后4页，已转为回归 | 6/7 | 7/7 | 3；其中平贝母候选中文名为null |

三个回归组均无观察到的漏切、额外切分、歧义或跨组串接。对原8处失败，还按原始行索引检查：对应标题已成为新条目，前一候选不再包含这行，区别于仅按粗略页栏数目判断。

上述均为同一AI助手读图建立、未经人审的参考，衡量粗略页栏标题对应，不是像素级边界或全文准确率。药名“士贝母”“白”“本”等原始错误仍保留，不会因为修复边界而自动成为正确药名；也没有证明其他正文内容正确。

审计目录：[边界修复](../audits/pharmacopoeia_boundary_repair/)。其中`final_regression30_*`、`final_regression8_*`、`final_regression4_*`是最终回归结果；`holdout_v3_report.json`与`holdout_v4_report.json`是修复过程中保留的失败记录。

## 缓存重建与复现

新增只读OCR重建入口，在输出前验证计划、来源PDF、运行完成状态、推理配置、页面缓存指纹和原图指纹；输出必须是新目录，不能覆盖旧试验。边界回归失败时返回退出码2，保留失败报告，不把工程完成当业务通过。

```bash
# 在新的目录重建原30页，不重复OCR。
python -m scripts.resegment_pharmacopoeia_batch \
  --out var/pharmacopoeia_boundary_repair_v5/recheck30

# 对此前8页重建，明确使用其原始计划、缓存与参考。
python -m scripts.resegment_pharmacopoeia_batch \
  --plan eval/pharmacopoeia_boundary_repair_holdout_v1.json \
  --batch var/pharmacopoeia_boundary_repair_v3/batch \
  --reference eval/pharmacopoeia_boundary_repair_visual_v1.json \
  --out var/pharmacopoeia_boundary_repair_v5/recheck8
```

默认原30页的重建参考已用于开发，输出明确标为`known_failure_regression`、`independent_validation: false`。复现依赖忽略目录中的原OCR和页面图。旧冻结评估器会拒绝用已修改的抽取源码冒充旧版本重放；各轮私有目录保留了对应源码快照，公开审计保留SHA，不篡改旧冻结记录。

本轮还实际验证：修改OCR缓存副本会被拒绝且不创建输出；故意删除一页的参考标题后，重建命令返回2并保留失败报告。正文、图片、私有数据库不会进入公开仓库或镜像。

## 最后一次新页面验证与工程验收

在v5冻结后另取434页版本物理150–153页（印刷131–134），先读图登记6处标题，再做4页新OCR。该组没有参与v5调规则：**6处标题均有对应、漏切0、额外切分0、歧义0、名称差异0**；3个候选跨页，最后地榆缺后续页，继续保留未闭合状态。

这只是一组4页、有目的选择、AI参考未人审的新样本。它提供了修复后的新页面证据，不能外推为全书100%正确，也不代表来源、性味、用量等全文正确。

- 最终[新页面计划](../eval/pharmacopoeia_boundary_repair_holdout_v3.json)、[视觉参考](../eval/pharmacopoeia_boundary_repair_visual_v3.json)、[冻结指纹](../audits/pharmacopoeia_boundary_repair/freeze_v5.json)、[实际结果](../audits/pharmacopoeia_boundary_repair/holdout_v5_report.json)。计划/参考文件的v3表示第三组新页面；切分代码版本为v5，不能混淆。
- 新增共11项测试，最终203/203离线回归通过、0跳过。对部分标题缺失、乱码、错位、跨栏、重复切分与未知身份均有工程负例检查。
- 最后4页再次运行命中全部缓存、新OCR为0、条目SHA一致。两份PDF、ETCM数据库及三组原始失败候选均通过未改动校验。
- 本轮新增识别16页：8页、4页、再4页；已知42页回归复用OCR。原30页及后续两组失败仍各自保留，未发布镜像或导入正式知识库。

复现最后一组冻结检查（需保留忽略目录的原图/缓存，且对应源码未再变更）：

```bash
python -m scripts.eval_pharmacopoeia_boundaries \
  --plan eval/pharmacopoeia_boundary_repair_holdout_v3.json \
  --reference eval/pharmacopoeia_boundary_repair_visual_v3.json \
  --freeze audits/pharmacopoeia_boundary_repair/freeze_v5.json \
  --batch var/pharmacopoeia_boundary_repair_v5/batch \
  --out var/pharmacopoeia_boundary_repair_v5/recheck-new.json
```

下一步优先对来源漏行、药名漏字、乱码拼音的少量异常区域做局部重识别对照，并确认未知身份仍不进入规范药材映射。边界修复后再逐批扩大章节覆盖，继续抽样检查；不要求人工逐字段审核全书，也不直接把全部未审核OCR接入聊天。当前原始OCR中的专业错字和字段范围准确性仍需单独验证。
