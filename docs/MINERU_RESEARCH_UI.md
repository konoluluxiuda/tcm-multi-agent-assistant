# MinerU 只读研究页面

2026-10-17 完成新版研究页面，使用 `var/mineru_retrieval_v2` 的616候选、8654字段块。旧的 `serve_pharmacopoeia_research` 小批次页面仍保留，两者索引和审核语义独立。

## 启动与使用

在仓库根目录运行：

```bash
/tmp/tcm-ocr-sample-20261016/bin/python -m scripts.serve_mineru_research --port 7869
```

打开 <http://127.0.0.1:7869/>。该命令使用本机已有的Python 3.12临时环境，环境删除后需重建；页面服务使用标准库，原页渲染额外需要 `pymupdf` 和 `pillow`，当前验证版本分别为1.28.2与12.3.0。无需安装整套OCR模型。服务仅绑定本机，当前未配置容器启动或端口发布。

2026-10-18新增 <http://127.0.0.1:7869/chat> [独立研究对话](MINERU_RESEARCH_CHAT.md)，支持追问、出处回查及独立SQLite会话恢复。原研究台仍可直接使用。

1. 输入“木棉花的用量”，使用“药名＋字段定位”。
2. 对照“原文”和“研究视图”，查看药名修订、字段修订数量和原始检查标记。
3. 展开“字段原页证据”或“标题原页证据”，点击“查看原页”。红框为来源块范围，可能大于实际引用文字，不等于逐字定位。
4. 使用BM25探索全文，但须核对命中药名和字段；目前没有相似度拒答阈值，不能把首条结果当成答案。

两份PDF默认位置为用户提供的 `data/` 文件。可通过 `--directory`、`--high`、`--full` 更换文件位置，但启动时必须与索引中的来源SHA一致。数据、PDF、截图均不通过页面任意文件下载接口暴露。

## API与来源约束

| 路径 | 行为 |
|---|---|
| `/api/info` | 返回索引收据及未审核状态 |
| `/api/search?q=...&method=name_field&k=5` | 只读检索；method支持name_field/bm25，k为1–20 |
| `/api/page?chunk=...&kind=evidence&index=0` | 从真实索引字段解析对应原页；kind也支持title_evidence |

预览不接受客户端文件路径、页码、坐标或缩放倍率。服务从索引读取证据，以原PDF SHA、**物理页码**、0–1000坐标渲染原页。分册和全本分别处理，补页不使用固定页码偏移；预期印刷页与尚未核验的识别印刷页均明确标注。

启动验证索引SHA及所有字段/标题引用的PDF SHA。每次查询使用独立、只读SQLite连接；查询前后检查索引、清单和PDF的文件签名，文件变化返回409并要求校验重启。这里的签名检查用于本地快照变更检测，不是对恶意文件系统修改的完整防护。每次连接仍会验证索引SHA，页面耗时还包括文件读取、HTTP、图片渲染，不能引用离线纯查询耗时作为页面响应时间。

原页渲染串行执行以限制同时解码的内存使用。未知/重复参数、非法坐标、无效证据索引和任意文件访问被拒绝。服务没有写入、审核通过或实体合并接口。

## 验收

```bash
/tmp/tcm-ocr-sample-20261016/bin/python -m unittest \
  tests.test_mineru_research tests.test_mineru_pharmacopoeia \
  tests.test_mineru_heading_recovery tests.test_mineru_review_packet \
  tests.test_mineru_captions tests.test_mineru_revisions \
  tests.test_mineru_retrieval tests.test_pharmacopoeia_segmentation -q

# 另一个终端保持服务运行；复用本机已有浏览器测试环境。
FONTCONFIG_FILE=/tmp/tcm-fontconfig.xml \
/tmp/tcm-api-check-20261012/bin/python -m scripts.verify_mineru_research \
  --executable /tmp/tcm-playwright-20261012/chromium-1243/chrome-linux64/chrome
```

- 86项相关工程测试通过，其中8项新测试覆盖物理页/红框、标题证据、并发只读、错误来源、文件变化和非法证据选择。未安装PDF依赖时这8项会跳过，应使用上述完整环境验收。
- 9组Chromium检查通过：快照状态、恢复标题、分册原页、全本补页、AI药名/正文对照、失败查询清空结果、BM25、非法请求和移动端布局等；无页面脚本错误或外部网络请求。
- 浏览器报告见 [验收记录](../audits/mineru_research_ui/browser.json)，截图保留在被忽略的 `var/mineru_research_ui/`，不提交整页药典内容。
- 来源PDF、研究索引与正式ETCM库校验和保持原值。UI测试不是医疗正确率、药典真实性证明或新增盲测评估。

## 后续工作

2026-10-18已完成[50题新问题挑战](MINERU_CHALLENGE_V1.md)，并增加[炮制范围、名称上下文与引用保护](MINERU_RETRIEVAL_GUARDS.md)。未完成片段单独展示，不作为有效命中。下一步可转向独立研究聊天入口，按检索状态展示证据或澄清；不自动补缺项。全部条目仍未人工审核，尚未接入正式聊天、没有发布新Docker镜像，M2全文质量验收尚未完成。
