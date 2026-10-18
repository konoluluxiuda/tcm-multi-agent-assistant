# 四味药九类目标字段的转录覆盖

后续：[自动质检与研究检索](PHARMACOPOEIA_AUTOMATED_QC.md)已实现机器放行、异常复核和分层抽样，不再要求全字段人工签核后才能开展研究预览。本页保留转录阶段记录。

2026-10-17。接续[六个差异字段的原图诊断](PHARMACOPOEIA_VISUAL_TRIAGE.md)，本轮实际查看其余目标字段的证据裁图与完整选定区域，手工录入AI转录草案。没有重跑OCR，没有把两套OCR一致当作人工核实，也没有转录药典所有性状、鉴别、检查或含量测定段落。

## 已完成范围

| 药材 | AI转录草案 | 有限区域缺失观察 | 人工参考 / 批准 |
|---|---:|---:|---:|
| 一枝黄花 | 8 | 1（注意） | 0 / 0 |
| 丁公藤 | 9 | 0 | 0 / 0 |
| 大腹皮 | 8 | 1（注意） | 0 / 0 |
| 山麦冬 | 8 | 1（注意） | 0 / 0 |
| 合计 | 33 | 3 | 0 / 0 |

目标字段为来源、药用部位、拼音、拉丁药材名、性味归经、功能主治、用法用量、注意、贮藏。药用部位与来源证据会重叠，33不是33条独立药材或真实用户任务。

在新增查看的字段中，暂未发现与双方一致文本冲突的新字词。数字、单位、丁公藤的给药方式和“孕妇禁用”按图照录；拼音保留印刷形式，拉丁药材名与植物学名分开；拉丁词间空白、换行与范围符号的转录排版不改写原始OCR。原六个差异字段沿用已读图草案及备注。

## 缺失观察的含义

一枝黄花、大腹皮和山麦冬在人工选定区域中未观察到注意项，分别登记`not_observed_in_selected_region`。每条观察绑定该药全部选定区域图片的SHA，不生成空白转录或“无禁忌”答案。原字段仍是`not_observed_in_selected_ocr`，人工参考仍为null。

这是AI对有限区域的观察，不证明整部药典没有该信息，也不构成安全性结论。脚本拒绝`no_contraindications`等不同finding，拒绝把缺失观察写成draft_text，并核对观察范围和图片指纹。

## 查看本地新版

```bash
python -m http.server 7867 --bind 127.0.0.1 --directory var/pharmacopoeia_full_review
```

打开 http://127.0.0.1:7867/ 。默认显示9个字段位置，33个位置有“AI原图转录草案”；3个缺失位置显示“AI原图观察”。可过滤差异、展开原始OCR与坐标、查看原图和已排除的页脚。下载的人工参考模板仍为36个空白位置，绑定当前comparison.json的SHA。

完整转录输入：`var/pharmacopoeia_full_transcription_input.json`；独立页面：`var/pharmacopoeia_full_review/`。所有完整原文、草案和图片留在忽略目录。旧版六份草案、原始OCR及历史审计保持不变。

## 打包与覆盖契约

```bash
python -m scripts.build_pharmacopoeia_visual_drafts \
  --proposal var/pharmacopoeia_full_transcription_input.json \
  --out var/pharmacopoeia_full_review
```

输出目录必须尚不存在；已存在时使用新的--out。本轮proposal固定`scope=all_available_fields`，要求所有有候选的字段都有草案，双方均未观察到的字段都有完整区域观察。原`scope=disagreements`（默认）继续兼容上一轮六字段打包。

打包器检查基准比较文件SHA、字段对应、来源PDF/图片SHA、重复/漏项、已有人工信息和AI身份；AI草案不会覆盖OCR或human reference。草案未经过完整人工转录签核，因此不纳入正式黄金集，不用作模型准确率成绩，也没有导入正式检索或聊天。

## 验证与下一依赖

- 162/162离线回归，19模块；比较模块15项，新增测试覆盖全字段/缺失观察完整性、空白人工模板保留、禁止缺失观察冒充无禁忌，以及错误区域图片指纹。
- Chromium 11项通过：四药36位置、33草案及原值保留、3条缺失观察不变成参考文本、页脚排除、差异高亮、跨页出处、模板、移动端、HTML仅作为文字、加载失败无旧卡。
- 两份原PDF和ETCM数据库SHA保持不变；没有发布镜像，没有留下运行中的复核服务。

证据：[统计](../audits/pharmacopoeia_full_review/summary.json)、[逐字段覆盖与转录哈希](../audits/pharmacopoeia_full_review/coverage.json)、[浏览器](../audits/pharmacopoeia_full_review/browser.json)、[离线回归](../audits/pharmacopoeia_full_review/offline_ci.json)、[来源完整性](../audits/pharmacopoeia_full_review/source_integrity.json)。逐字段统计不包含完整转录或原图。

下一依赖是回收绑定当前版本的人工核对结果。审核前可用于标记清楚的本地研究预览；不能把AI草案当作正式已审核药典知识。M2-01正式索引与人工签核仍未完成。
