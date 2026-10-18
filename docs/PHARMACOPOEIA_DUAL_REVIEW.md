# 四味药的双引擎字段复核

后续：[六个字段的视觉诊断与AI草案](PHARMACOPOEIA_VISUAL_TRIAGE.md)已修复页脚混入问题，并提供独立新版目录。本文保留上一轮记录。

2026-10-17。接续[20页解析对照](PHARMACOPOEIA_PDF_BENCHMARK.md)，复用已经保存的RapidOCR与Paddle结果，不重新下载或运行模型。新增独立本地复核页、双方证据框和空白人工转录模板。没有接入正式聊天或知识库。

## 当前结果

4味药 × 9字段 = 36个字段位置，每个位置展示两套候选：

| 比较状态 | 字段数 | 含义 |
|---|---:|---|
| 原始文字一致 | 20 | 两侧原文字符串一致，仍待审 |
| 归一化后一致 | 7 | NFKC及去空白后一致，不改写原值 |
| 文字有差异 | 6 | 保留双方原值和字符差异 |
| 双方均未提取到 | 3 | 所选区域未观察到注意项，不代表无禁忌 |

33张字段证据图合并展示红色RapidOCR和蓝色Paddle行框；重叠处颜色可能覆盖。另有8张完整人工选定区域图，用于检查字段抽取遗漏。没有把旧试验的`dnpa`等值当作本轮输出；所有候选来自20页实验的新结果。

六个文字差异位置：一枝黄花功能主治；丁公藤来源；大腹皮来源、性味归经、功能主治；山麦冬来源。差异不是错误计数，部分是句号等格式问题；一致也不表示原文正确。

## 本地使用

```bash
# 需要PyMuPDF，可用既有独立OCR环境；不初始化模型
python -m scripts.build_pharmacopoeia_comparison
# 只提供生成目录，避免提供整个仓库
python -m http.server 7865 --bind 127.0.0.1 --directory var/pharmacopoeia_dual_review
```

打开 http://127.0.0.1:7865/ ，选择药材，按需勾选“只看差异或缺失”。每个字段显示双方候选、字符高亮、原始候选次数和坐标证据，展开出处查看原图；遇到双方缺失时展开完整人工选定区域。

生成目录默认为`var/pharmacopoeia_dual_review/`，包含`comparison.json`、`review_template.json`、`index.html`及`assets/`。这些文件含原文/原图，均留在忽略目录。页面无需远程资源，OCR文字通过textContent展示。

## 人工转录的工作方式与边界

下载页面上的空白模板，按6个差异字段、3个缺失位置、其余一致字段的顺序核对。每一项都保留来源行和坐标，参考文本`reference_text`、审核人`reviewer`、时间`reviewed_at`初始为null。先核对完整字段，再填写参考原文和备注；看不清的字保留未知并注明位置，不用另一引擎、ETCM或预录片段补写。注意药材拉丁名与基原植物学名分别核对，数字、单位及否定词不能用格式归一化跳过。

本页只展示和下载，不保存编辑、导入审核结果或批准字段。**当前完整人工参考0条、批准字段0条。** 模板与comparison.json的SHA绑定，后续回收审核文件时须验证该绑定和来源，不能把模板生成视为人工审核完成。

现有提取器仍依赖四味药的手工区域和有限部位词表；末字段有`selected_region_end`待审提示。对比时按手工区域与y顺序整理字段，不评价Paddle原始块的阅读顺序。原始21参考片段不用于生成或修正字段，没有自动选赢家、自动纠错、实体合并或生产写入。

## 验证与证据

- 7项新增单元测试：字段缺失/重复、邻栏干扰、否定词/数字/学名/标点差异、HTML原值、空白参考、输入指纹及坐标/NaN越界。提取器不使用预期答案。
- 8项真实Chromium检查：四味药36位置、差异过滤与高亮、跨页注意项原图、缺失提示与完整区域、绑定来源的模板下载、手机无横向溢出、OCR标签不执行、加载失败无旧卡。
- 最终离线回归154/154，19模块；两份原PDF校验和不变。原模型实验与既有四药候选文件未改动。

报告：[统计](../audits/pharmacopoeia_dual_review/summary.json)、[浏览器](../audits/pharmacopoeia_dual_review/browser.json)、[离线回归](../audits/pharmacopoeia_dual_review/offline_ci.json)。

浏览器复现：`python -m scripts.verify_pharmacopoeia_comparison --executable /path/to/chromium`；需独立安装Playwright和Chromium。临时回环服务随测试结束关闭。当前没有留下运行中的复核服务，没有发布镜像。
