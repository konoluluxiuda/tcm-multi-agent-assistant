# 两味药联合比较报告

交付日期：2026-10-25。面向“比较当归与川芎的药典记载、基础属性和共同靶点，并保留出处”的资料整理任务。真实资料只在本地读取，无新增模型调用。

## 使用

打开 http://127.0.0.1:7879/ ，展开“本地研究”，选择“两味药联合比较”，分别输入“当归”“川芎”，查询内容输入“功能与主治”。结果包含字段比较表、共同 UniProt 首页及双方原始记录，可下载报告与出处 JSON，刷新后从研究历史回看。

查询内容填写双方都要查询的药典字段，不是任意复杂任务指令。当前仅支持两味药，共同靶点展示前20项；后续页按钮转入聊天按原来源 ID 重新查询，不属于原报告的历史快照。

## 实际架构

`POST /api/research/jobs/compare` → 幂等持久队列 → LangGraph 左/右研究子图并行（各自药典与ETCM两分支）→ 来源与版本检查 → 共同靶点工具 → 证据校验与比较表 → SSE完成通知/持久历史。

- 正常路径共5次本地查询。共同靶点工具只接收子图取得的双方来源ID、文件哈希和数据版本，不重新按中文名猜测来源。
- 来源不明确、同一来源或不同数据版本时跳过交集；单侧失败保留另一侧资料，返回部分完成。
- 复用原共同靶点查询的只读事务与哈希检查；汇总层再检查目标标识去重、双方来源顺序、见证原行及JSON位置，失败结果不进入报告。
- 子图与工具沿用可取消禁网进程、45秒分支超时和125秒任务预算。没有新增自动重试。
- 比较表按“来源类型+字段标签”对齐。相同显示值不是实体同一性证明，文本不同不是医学矛盾，重复字段不自动合并。
- 保留药典提取待审、ETCM中文名对应未核验的边界；共同标识不是相同机制或药效证据。

这是带数据依赖的固定子图协作，不是自主LLM规划、多专家推理或CoT输出。Qdrant新入口、学习式重排、自主模型协作仍未完成。

## 验收与发现

本地真实数据：

| 问题 | 结果 | 出处 |
|---|---|---|
| 当归/川芎，功能与主治 | 13行比较项，172个共同UniProt，首页20项 | 每项有双方见证 |
| 白术/黄芪，功能与主治 | 13行比较项，151个共同UniProt，首页20项 | 每项有双方见证 |
| 当归/不存在的合成药 | 保留当归13行，部分完成 | 未执行交集 |

首次真实验收发现当前LangGraph 0.3在嵌套场景强制使用values流，而旧单药实现按updates解析，导致子图结果丢失。修复为无进度回调时通过ainvoke取得最终状态；新增真实嵌套图测试，避免只用模拟子图掩盖集成问题。失败记录保留于 `var/pair_report_milestone/local_acceptance*.json`，通过结果为 `local_acceptance_v3.json`。

离线测试覆盖并行屏障、依赖顺序、取消传递、分支超时、同源/异版本拒绝、双侧见证校验、实际子图嵌套、API输入校验、幂等参数顺序及会话隔离。全仓110模块961项离线回归通过，跳过0项。浏览器已验证实际比较表、双方出处、JSON导出、刷新回看且无JS错误；352份源码指纹在记录后至验收结束未变。完整回归和浏览器验收摘要见 `audits/pair_report_milestone/`。

```bash
# 当前机器已有依赖环境；其他机器请替换解释器路径
/tmp/tcm-ci-mail-env/bin/python -m scripts.preview_local_tasks \
  --port 7879 --state var/research_run_chat_v1/state \
  --diseases var/disease_catalog_v1/build/diseases.sqlite3

python -m scripts.check_pair_research_report --out var/pair_report_check.json
python -m scripts.run_offline_ci --out var/pair_report_regression_new
# Playwright环境下，针对已启动预览
python -m scripts.check_pair_research_browser --browser /path/to/chromium --out var/pair_browser_new
```

本次预览使用默认词法配置：前轮开发评测没有证明dense融合优于BM25，因此不默认加载向量模型。没有升级依赖、迁移数据库、构建Docker或提交Git。

## 回滚与后续

队列schema不变，新增pair_report类型保存在任务document。回滚worker前应先结束或取消新类型任务，避免旧worker误按疾病任务处理；新报告历史渲染依赖新版页面。原始数据不受影响。

本轮补齐两味药比较和来源绑定的工具依赖。后续仍按技术主线推进：检索重排与独立评测、受限模型规划/证据写作、PostgreSQL/checkpointer恢复、语义记忆及MCP。当前证据校验只检查结构和来源绑定，不代替医学事实审核。
