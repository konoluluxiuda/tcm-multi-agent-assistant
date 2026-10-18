# 六个差异字段的原图诊断与AI转录草案

后续：[九类目标字段的转录覆盖](PHARMACOPOEIA_FULL_REVIEW.md)已扩展到33份AI草案和3条区域缺失观察，保留本页作为六字段阶段记录。

2026-10-17。接续[双引擎复核页](PHARMACOPOEIA_DUAL_REVIEW.md)。本轮实际展开六个差异字段的原图，整理AI视觉诊断及六份完整字段转录草案，并修复页脚进入来源字段的问题。没有运行OCR模型，没有写入正式知识库。

## 发现与修复

| 字段 | 原图诊断（仍待人审） |
|---|---|
| 一枝黄花功能主治 | 原图“疮疖肿毒”；Rapid漏“疖”，Paddle识别成“疮疗” |
| 丁公藤来源 | 属名Erycibe在Paddle中混入希腊字母γ，成为Erγcibe |
| 大腹皮来源 | Rapid漏两处句号；双方都把页脚·28·带进来源 |
| 大腹皮性味归经 | Paddle漏句末句号，本处没有把标点差异计为医学事实错误 |
| 大腹皮功能主治 | 原图“脘腹胀闷”；Rapid为“院腹胀闷” |
| 山麦冬来源 | Rapid有spicala和“短葶”漏字，Paddle有mucari；草案照图保留Baily署名 |

页脚修复仅排除**已知印刷页码、双侧点号装饰、行中心位于页面底部94%以后**的独立OCR行。匹配本轮·28·，两套结果各排除1行；裸数字、带单位数量、正文内相同数字、与本页不符的页码均保留。原始行、坐标及排除原因放在`excluded_margin_lines`，完整区域图仍可看到页脚。此规则不是任意书籍的通用页边识别。

文字比较仍是36个字段位置：20原值一致、7归一化一致、6文字差异、3双方未提取到注意项。去除页脚没有把六处原有文字差异改成一致。历史`var/pharmacopoeia_dual_review/`及其审计保留，当前新版使用下面的新目录。

## 查看新版

```bash
python -m http.server 7866 --bind 127.0.0.1 --directory var/pharmacopoeia_visual_triage
```

打开 http://127.0.0.1:7866/ 。六个文字差异字段下方新增“AI原图转录草案”，保留两侧OCR、差异高亮和原图。大腹皮的页脚排除记录可展开查看。下载的人工模板仍有36个空白参考位置，不会把AI草案预填为人工答案。

**AI转录草案6份，完整人工参考0份，批准字段0项。** 草案为读图候选，不代表药典来源真实性、内容科学正确性或临床验证。拉丁词间空白和括号排版在草案备注中说明，原始OCR没有改写。

## 本地文件与可复现边界

- `var/pharmacopoeia_dual_review_margin_fixed/`：页脚修复后的字段比较及原图。
- `var/pharmacopoeia_visual_transcription_input.json`：本轮AI读图后手工录入的完整转录及图片指纹，不由OCR、预录片段或其他数据库自动补全。
- `var/pharmacopoeia_visual_triage/`：独立新版页面、双方OCR、六份草案及空白人工模板。
- [诊断清单](../eval/pharmacopoeia_visual_triage_v1.json)：只记录诊断、短词、备注与来源指纹，不包含完整草案或原图。

这些var文件含原文或图片，保持忽略，不上传公开仓库或镜像。完整转录未进入golden，原21项片段检查分母未改变；不得用本轮事后补写的草案宣称准确率提升。

生成过程：

```bash
# 先保留历史结果，生成单独的新比较目录（需要PyMuPDF）
python -m scripts.build_pharmacopoeia_comparison --out var/pharmacopoeia_dual_review_margin_fixed
# 原图核对后准备proposal，再生成独立新版；输出目录必须尚不存在
python -m scripts.build_pharmacopoeia_visual_drafts
```

打包器要求proposal中的`actor_type=ai`、`human_signoff=false`，比较文件SHA及六个字段的全部图片指纹匹配，且没有重复或遗漏目标。已有人工参考或审核信息时拒绝覆盖。重建比较文件可能改变SHA；不能自动重绑旧草案，必须重新核对对应原图。输出目录已存在时使用新的`--out`，不要覆盖下载后填写的人工审核文件。

## 验证

- 159/159离线回归，19模块；比较模块12项，新增覆盖页脚误吞、裸数字与剂量保留、草案输入陈旧/篡改、AI冒充人工签核、覆盖已有人工参考、草案重复/缺失及原始OCR不变。
- Chromium 10项通过；新增验证六份AI草案与人工参考分开、来源字段去页脚而原始记录可见，并验证草案标签文本不执行。
- 首次浏览器检查在未展开details时读取可见文字失败，修正测试先展开后完整通过；未为测试改写生产字段，失败记录保留。
- 两份原PDF及ETCM数据库SHA保持不变。模板仍为空，人工签核与正式检索导入未完成。

证据：[草案统计](../audits/pharmacopoeia_visual_triage/summary.json)、[页脚修复统计](../audits/pharmacopoeia_visual_triage/fixed_comparison_summary.json)、[浏览器](../audits/pharmacopoeia_visual_triage/browser.json)、[首次失败](../audits/pharmacopoeia_visual_triage/browser_initial_failure.json)、[离线回归](../audits/pharmacopoeia_visual_triage/offline_ci.json)、[来源完整性](../audits/pharmacopoeia_visual_triage/source_integrity.json)。

下一步补齐其余字段的原图转录，并回收针对具体版本的人工核对结果；来源不清、文字不可辨认或审核未完成的字段继续保留待审状态。
