# 跨来源资料报告：本地交付与检索对照

2026-10-24。交付的是可操作的资料报告闭环，尚不是自主研究/疗效推理系统。无新增外部模型调用，真实资料留在本地。

## 用户入口与实际调用链

聊天页展开“本地研究”，研究类型选择“药典 + ETCM 报告”。输入药材名（例如当归）和药典查询内容（例如性味与归经），直接提交。无需核对请求清单。

`POST /api/research/jobs/report` → 带类型及参数的持久幂等队列 → worker → LangGraph 并行 text_research / structured_research → assemble → 保存结果 → SSE 通知 → 页面及历史回看。

- 文本分支：沿用药名、炮制范围、字段缺失保护；实体限定 BM25 + 字段检索，配置本地 BGE 时加入 dense，使用 RRF（常数60）融合。候选最多每路20、展示最多6。无词法/字段证据时不使用向量强行兜底。模型、缓存与索引继续校验指纹。
- 结构化分支：复用 ETCM 只读查询和本地字段翻译，展示单一候选的基础属性及 JSON pointer、文件哈希；多个候选不自动选取。
- 两路各自运行在可取消、禁网、无模型凭据的子进程；分支超时45秒。查询前后检查源文件身份/大小/修改时间，不将变化期间结果作为稳定快照。这不是跨库事务一致性保证。
- 一个来源缺失或失败，另一个来源结果仍保留。缺项/炮制歧义按部分结果呈现，不补造答案。状态 complete 仅代表两路资料取得，并不证明问题全部回答或实体对齐正确。
- 段落及属性分别保留出处，默认折叠技术明细。报告不合并两库实体，不把关联写成治疗结论。

这是**固定职责的 LangGraph 并行证据流程**，不是两个自主 LLM Agent。RRF 是排名融合，不是 CrossEncoder 重排。当前使用本地 numpy 向量缓存，未将新入口切到 Qdrant。

## 同题对照与决策

沿用44道开发题：35道有指定目标锚点，9道拒答题；不是独立用户集，也不是完整相关性标注，因此不报 Recall。

| 方案 | 目标锚点 Hit@5 | MRR@5 | 拒答正确 | 错药材题数 | 均时 |
|---|---:|---:|---:|---:|---:|
| 名称/字段 | 25/35 | 0.7143 | 9/9 | 0 | 1.01ms |
| 名称限定 BM25 | 29/35 | 0.8286 | 9/9 | 0 | 4.97ms |
| 有保护的本地 RRF | 29/35 | 0.8143 | 9/9 | 0 | 182.32ms |

均时包含每题模型/索引初始化，且与回归并发运行，只是本机开发诊断，不是服务p95。**目前没有证据证明融合优于 BM25**，所以向量仅通过显式配置开启，不能宣传为准确率提升。下一轮应先补独立问题与重排对照，再决定生产默认方案。

首次实现有重复药名拼接导致保护误触发，44题中融合目标命中25/35、拒答6/9；保留 `retrieval_comparison.json` 原始失败记录。修复后结果另存 `retrieval_comparison_v2.json`，未覆盖失败证据。

## 验证与复现

全仓109模块953项离线回归通过，无跳过；运行期间完成了最后的检索修复，随后对跨来源/队列/API的15项受影响测试补验全部通过。并非冻结工作树下全量重跑。

本地5题：当归性味、黄芪功效完成；白术功效及注意事项缺字段，正确部分完成；未知药无证据；对象白术但问川芎时拒绝文本串源并保留白术独立属性。另有并行屏障、分支超时/异常、取消、幂等payload、API校验及会话隔离测试。真实 Chromium 已验证提交、结果、折叠出处、刷新后历史回看且无 JS 错误。

当前机器启动：

```bash
/tmp/tcm-ci-mail-env/bin/python -m scripts.preview_local_tasks \
  --port 7879 --state var/research_run_chat_v1/state \
  --diseases var/disease_catalog_v1/build/diseases.sqlite3 \
  --dense-python /tmp/tcm-dense-20261019/bin/python
```

访问 http://127.0.0.1:7879/ 。其他机器用自己的项目 Python 与 ONNX Python 路径；移除 `--dense-python` 则不加载向量模型。此脚本沿用现有研究入口，因此当前还需配置疾病目录，即使只演示跨来源报告。

```bash
python -m scripts.check_cross_source_report --out var/cross_source_milestone/local_acceptance.json \
  --dense-python /path/to/local-onnx-python
# 下列命令使用含 numpy/onnxruntime/tokenizers 的本地环境
python -m scripts.eval_cross_source_retrieval --out var/cross_source_milestone/retrieval_comparison_new.json
python -m scripts.run_offline_ci --out var/cross_source_milestone/full_regression_new
# 可选浏览器验收：在安装 Playwright 的环境执行
python -m scripts.check_cross_source_browser --browser /path/to/chromium \
  --out var/cross_source_milestone/browser_check
```

本地验收原件在 `var/cross_source_milestone/`；可提交的指标摘要在 `audits/cross_source_milestone/`，不包含整段药典正文。浏览器截图受本机中文字体缺失影响，仅作为 DOM 功能验收辅助。

## 仍未实现与后续范围

J02/J03 本次交付到“并行资料报告”，不将整个升级计划勾成完成：

1. 学习式中文重排、Qdrant 接入和独立保留集仍未完成；现有对照不支持盲目增加推理成本。
2. 多药材比较、图路径与文本联合解释、冲突自动判定、自由问题任务拆解及模型最终写作仍未接入本报告。后续应扩充证据协议与受限工具协作，先用合成资料验证。
3. PostgreSQL/checkpointer 中间节点续跑、跨会话语义记忆、MCP、统一 trace 仍按技术大纲继续。

队列 schema 未变化，新参数保存在 document 中；旧疾病接口及历史保持兼容。回滚时先取消/结束新类型任务，再回滚 worker/API/UI：旧 worker 不认识新 payload，不能让旧版继续领取遗留报告任务。回滚会丢失新报告的页面渲染能力，但无需删除源数据。
