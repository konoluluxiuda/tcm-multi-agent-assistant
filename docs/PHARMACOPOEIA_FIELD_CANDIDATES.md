# 四味药材的字段候选与来源复核

2026-10-17。接续 [OCR试验](PHARMACOPOEIA_OCR_PILOT.md)，已将四味药的手工区域识别结果组织成独立字段候选，提供只读查询与本地资料卡/原图展开。没有接入正式聊天，也没有写回ETCM或创建正式知识索引。

## 实际交付

- 四味药共36个字段位置：33个有候选值，3个为“所选OCR片段未观察到注意项”。没有将未观察到解释成无禁忌或无需注意。
- 字段包括来源原文、药用部位、拼音、拉丁药材名、性味归经、功能主治、用法用量及注意、贮藏。拉丁药材名独立于来源中的植物学名，不覆盖ETCM的拉丁名。
- 原文证据包含PDF文件SHA、物理页号、印刷页号、栏位、OCR行坐标、行序号和模型分数；生成33张带红框的原图裁剪。跨页条目仍指向实际页码，不复制成虚构的单页出处。
- 抽取只读取手工区域和OCR标题，**不使用 `expected_fragments` 生成答案**。同字段多次出现时保存多个候选，不静默选择；未知标题也会结束上一字段，错误括号/空内容进入待复核状态。

| 药材 | 当前状态 | 原因 |
|---|---|---|
| 一枝黄花 | quarantined | 新确认功能主治中“疮疖”识别成“疮疗” |
| 丁公藤 | pending_human_review | 字段已提取，尚未人工签核；末段结束边界仍需核对 |
| 大腹皮 | quarantined | 拼音失真，以及“脘”识别成“院” |
| 山麦冬 | quarantined | `spicata→spicala`、`短葶→短亭`，仍保留识别原值 |

这五项视觉复核标记保存在 [标记清单](../eval/pharmacopoeia_ocr_flags_v1.json)：前三项来自上一轮诊断，后两项为本轮展开功能主治原图后确认。它们仍是AI视觉标记，不是人类审核结果。自动拼音检查可以报告候选拼音不一致，但不代表程序能自动发现全部学名/中文错字；没有自动替换机制。

**三条隔离，一条待审，零条批准。** 隔离条目仍可显示用于复核，但查询返回 `unreviewed_candidate`，携带状态和异常，不能当作已验证资料。

## 抽取边界及真实修正

初版“药用部位”正则在大腹皮的OCR漏句号后，把后续采收描述也吞进字段。现改为本试验明确支持的“干燥全草、干燥藤茎、干燥果皮、干燥块根”词表，只保留原文短语；来源描述全文另外保留。未匹配到的部位描述标为未解析，不能以该四词表推广到全书。

新增回归证明漏句号不会吞入采收说明。同字段重复不覆盖；字段内容延续到下一页时保留两页证据；疑似第二条药材标题会截断并隔离。这个标题检查只支持明确的中文/拼音/大写拉丁标题组合，不等于自动识别任意药典条目边界。

末字段到手工区域末尾时标为 `selected_region_end` 并提示复核。手工区域仍是前提，尚未验证全书缺页、自动章节边界、公式表格或任意扫描版式。

## 本地使用

生成候选和原图需要PyMuPDF，可复用上一轮临时PDF环境或安装独立OCR依赖。无需重新运行OCR模型：

```bash
python -m scripts.build_pharmacopoeia_candidates
python -m scripts.query_pharmacopoeia_candidates 一枝黄花 medicinal_part
python -m scripts.query_pharmacopoeia_candidates 一枝黄花 cautions
python -m scripts.eval_pharmacopoeia_candidates
python -m http.server 7864 --bind 127.0.0.1 --directory var/pharmacopoeia_candidates
```

浏览器打开 `http://127.0.0.1:7864/`，选药材和字段，展开“原图与出处”查看红框、页码与SHA。页面是独立的本地只读复核工具，不是现有7860聊天页，也不提供审批或写入按钮。

若尚无上一轮的 `final/` OCR文件，先运行 `python -m scripts.pilot_pharmacopoeia_ocr --out var/pharmacopoeia_ocr_sample/final`，或通过 `--ocr-dir` 指向相同来源的OCR目录。

默认结果为 `var/pharmacopoeia_candidates/candidates.json`、`index.html` 与 `assets/`。这些文件含OCR原文或原图，只保存在忽略目录；仓库保存代码、标记清单和不含完整OCR的统计审计。原PDF和既有OCR文件未改动。

## 验证

- 工程回归 **140/140**，17模块；新增11项测试覆盖字段边界、空值与缺失、重复标题、跨页证据、错误区域溢出、来源错配、坐标错误、原值保留及漏句号回归。
- 实际四药候选查询 **25/25** 检查通过：17个既有参考片段及出处、3个缺失注意项、2个错误值不改写、跨栏/跨页定位与未知药材澄清。不是25个真实用户任务，也不验证药典内容的科学正确性。
- 真实Chromium **7项**通过：字段卡和原图、跨页注意项出处、学名/拼音异常保留、缺失含义、未找到不显示旧卡，以及OCR中的HTML仅作为文字显示。
- 首次浏览器检查因选择器同时匹配正文和证据JSON失败，修正为直接子元素后完整通过；保留 [首次失败](../audits/pharmacopoeia_fields/browser_initial_failure.json)。生产页面没有为适配预期而改写资料。
- 原冻结验收 **30/30**，人类签核仍pending。原PDF校验和保持不变，未修改结构化药材库。

报告：[候选统计及原图指纹](../audits/pharmacopoeia_fields/summary.json)、[25项实际查询](../audits/pharmacopoeia_fields/checks.json)、[浏览器检查](../audits/pharmacopoeia_fields/browser.json)、[工程回归](../audits/pharmacopoeia_fields/offline_ci.json)、[原冻结验收](../audits/pharmacopoeia_fields/golden.json)。审核状态和业务完成率未改成人工通过或准确率。

复现浏览器检查需Playwright/Chromium：

```bash
python -m scripts.verify_pharmacopoeia_review
python -m scripts.run_offline_ci --out /tmp/tcm-field-ci
```

下一步应核对这些字段与异常，形成独立的审核后资料集，再用少量来源问题验证检索引用。药典PDF真实性、中文药名规范映射、专家语义审核及生产导入仍待完成；当前复核卡不能代替这些步骤。
