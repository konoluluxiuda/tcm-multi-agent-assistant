# 药典 PDF 解析器对照实验

2026-10-17。目标是比较同一批扫描页在现有 RapidOCR 分栏方案与 PP-StructureV3 下的识别、阅读顺序和证据定位。仅为离线工程实验，不导入正式知识库，不修改 ETCM 数据或聊天依赖。

## 固定输入

清单：[20页样本](../eval/pharmacopoeia_pdf_benchmark_v1.json)。引用既有来源清单的文件SHA，统一240dpi渲染PNG，再校验页面图像SHA。20是实际输入页数，包含同内容的不同来源页面，不等于20份独立内容。

- 前5页保留既有四味药的跨栏、跨页及补缺用例。
- 两版本的印刷3、30、199、415页用于同内容来源对照。
- 加入缺页前后、章节分隔、空白页、后续章节和拉丁索引。
- 未核验的印刷页号保留null，不根据固定偏移推断全书页码。采样是有目的的工程覆盖，不是随机抽样。

## 对照约束

本轮RapidOCR明确将PNG的RGB转为该引擎使用的BGR数组，旧试验直接传入RGB；因此本轮是重新测量的基线，不直接复用旧OCR原文。RapidOCR采用固定中线分栏，左栏到右栏、栏内按y排序；PP-StructureV3输入整页并保留其版面块顺序。两者比较的是完整处理方案，不能把差异全部归因于OCR模型。表格、公式、图表、印章和页面矫正先关闭，因此本轮不能评价这些能力。CPU、2线程，页面耗时包含读图、预处理、推理、后处理与结果适配，不含首次下载、初始化与写文件。Paddle固定使用PP-OCRv5 server检测/识别模型，检测长边上限960、识别批量4、关闭MKL-DNN；识别裁剪仍来自原始渲染图，不是把整页先缩小后丢弃原图。

原始文字、坐标、版面块和阅读文本存入忽略目录var/。仓库只保存清单、代码与脱敏统计。初始化允许的下载操作与推理分离；`--download-models`不打开PDF/页面图像，正式推理阻断Python socket连接。该措施不是操作系统级网络隔离保证。

保留原21个片段检查，按既有人工区域选行后排序，**仅评价局部识别，不评价解析器阅读顺序**。全文字符准确率、阅读顺序准确率、业务完成率保持null，待完整参考转录与人工核对。空白控制页的输出行数只是观察值，不能直接当作假阳性率。

耗时为单次观察，含空白页、两套不同处理方案，未控制所有后台负载，不能作为平均用户延迟、P95或严格的吞吐基准。首轮RapidOCR报告未采集峰值RSS，保持未记录，不事后补成零；两者推理均执行了socket阻断，记录的Python网络阻断尝试为0。

## 复现

在独立Python 3.12环境安装可选依赖。RapidOCR使用[既有依赖](../requirements-ocr-pilot.txt)；Paddle使用[独立冻结依赖](../requirements-ocr-paddle-benchmark.txt)，核心为`paddleocr[doc-parser]==3.7.0`、`paddlepaddle==3.3.0`和`pymupdf==1.28.2`，不要混入主应用环境。

```bash
# 用包含PyMuPDF的环境准备固定输入
python -m scripts.benchmark_pharmacopoeia_pdf --engine prepare
# 用Paddle独立环境下载本地模型；不读取PDF
python -m scripts.benchmark_pharmacopoeia_pdf --engine paddle --download-models
# 分别用对应环境执行，输出目录必须是新的
python -m scripts.benchmark_pharmacopoeia_pdf --engine rapidocr
python -m scripts.benchmark_pharmacopoeia_pdf --engine paddle
# 两者完整结束后，在任意Python环境生成无原文的统计对照
python -m scripts.compare_pharmacopoeia_pdf
```

`--limit 5`可先做既有药材的小样本连通性检查；不足20页时报告为partial，不能表示完整对照完成。推理错误记录failed及已完成页数，不把失败当零耗时或通过。

## 已遇到的运行问题

- 首轮关闭MKL-DNN但没有限制检测长边，第一页文字检测申请28,962,717,696字节内存失败，已完成0页。峰值RSS约8.5GiB，不能把申请失败的27GiB当实际常驻内存。
- 限制检测尺寸后尝试MKL-DNN，在版面检测阶段遇到`ConvertPirAttribute2RuntimeAttribute`未实现错误，已完成0页。因此当前配置关闭该加速路径。
- 两份失败记录保留在[audits](../audits/pharmacopoeia_pdf_benchmark/)，不代表所有硬件、版本或Paddle配置都会失败。当前系统无法访问GPU，只验证CPU路径。

## 实测结果

| 方案 | 已完成输入页 | 原参考片段命中 | 页面处理总耗时 | 进程峰值RSS |
|---|---:|---:|---:|---:|
| RapidOCR 1.4.4，固定分栏 | 20/20 | 21/21 | 154.171秒 | 未采集 |
| PP-StructureV3，资源受限配置 | 20/20 | 20/21 | 1025.665秒 | 4535.8MiB |

同一批页面图像及源PDF指纹核对通过。20/21未命中项为大腹皮性味归经句末句号缺失，按冻结规则判失败；不能由此推断医学内容错误率。Paddle识别到`spicata`和“脘”，但“短葶”和“短亭”在同一条目中并存，仍有“疮疗”及新增的`mucari`。本轮RapidOCR已识别出`Dafupi`，所以不能把这一项称为Paddle相对本轮基线的提升。前两页的Paddle正文/段标题块按左右栏分组后只有一次栏位切换，此项是坐标观察，不是完整阅读顺序准确率。

**当前决策：保留RapidOCR基线，Paddle作为独立离线复核候选；暂不全量替换，不导入正式知识库。** 尚未完成20页完整字段转录与人工签核，因此不宣布任一方案达到全文准确率目标。下一步优先整理四味药的完整字段参考与双引擎差异，人工复核后再推进条目边界和检索导入。

统计：[对照](../audits/pharmacopoeia_pdf_benchmark/comparison.json)、[RapidOCR运行](../audits/pharmacopoeia_pdf_benchmark/rapidocr_run.json)、[Paddle运行](../audits/pharmacopoeia_pdf_benchmark/paddle_run.json)、[固定页面元数据](../audits/pharmacopoeia_pdf_benchmark/prepared.json)。

## 后验失败诊断

除原21项检查外，新增[失败观察](../eval/pharmacopoeia_pdf_posthoc_v1.json)，仅作AI视觉待审核诊断，不改变原检查分母：本轮RapidOCR将“疮疖肿毒”识别为“疮肿毒”，Paddle仍识别出“疮疗”；另将原图中的`muscari`识别为`mucari`，已裁图核对并保存行坐标。改善部分旧错字不意味着没有新增错误。

## 后续选择依据

先人工核对完整字段，再衡量药材条目串接、否定词/数字/单位/拉丁词错误、出处框与阅读顺序。人工参考应逐字段标明起止坐标、跨页续文及不可辨认字符；不可辨认处保持未知，不根据其他药材或模型答案补写。原始参考片段只做NFKC/空白归一化，句号缺失仍按既有规则判失败；可单独解释失败类型，不能事后放宽规则来提高分数。只有确认收益后才选定主方案，并开展自动条目边界与正式索引。高模型分数、Markdown整洁或指定片段全部命中都不能替代这一步。后续若使用双引擎，差异进入待复核队列，不以多数票、置信度或ETCM字段自动覆盖药典原文；两套引擎一致也不能直接认定正确。

技术依据：[PP-StructureV3官方使用说明](https://www.paddleocr.ai/latest/en/version3.x/pipeline_usage/PP-StructureV3.html)。公开榜单不视为本药典实测结论。

## 验证与状态

最终离线回归147/147通过，18模块，包含7项新增评测保护测试；既有结构化药材冻结验收30/30通过，人类签核仍pending。两份原PDF及ETCM数据库哈希保持不变。见[工程回归](../audits/pharmacopoeia_pdf_benchmark/offline_ci.json)、[冻结验收](../audits/pharmacopoeia_pdf_benchmark/golden.json)、[源完整性检查](../audits/pharmacopoeia_pdf_benchmark/source_integrity.json)。本轮没有浏览器/聊天功能修改，也未发布镜像。
