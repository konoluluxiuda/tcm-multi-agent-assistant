# PubMed文献工具：第一阶段

本阶段交付可独立调用的官方接口适配器、关系台账与CLI。后续已在源码中接入默认关闭的[主聊天显式工具](PUBMED_CHAT.md)，已发布Docker镜像尚未更新；不自动把中文药名翻译成拉丁名，不将复方试验结论归因于单味药，不生成疗效结论。

## 使用

无需Tavily密钥、聊天模型或嵌入模型。使用Python标准库；默认把数据保存在被Git忽略的 `var/literature/pubmed.sqlite3`。

```bash
python -m scripts.search_pubmed --query 'Astragalus membranaceus[Title/Abstract] AND randomized controlled trial[Publication Type]' --limit 3
python -m scripts.search_pubmed --run-id <上一次输出的run_id>
```

第二条回放本地批次，不重新请求网络；重开进程仍可回放。已有v1文献库首次访问会先备份并迁移为v2，详见[升级说明](PUBMED_EVIDENCE.md)。可用 `--db` 指定独立库。成功/合法零结果返回退出码0，接口失败、部分获取和批次不存在返回2。存储故障会直接报错，不宣称已保存。检索原式可能含研究隐私，台账及备份按私有数据管理；CLI不提供共享下载/API。

## 契约与边界

- 固定访问NCBI官方ESearch和EFetch；按relevance排序，每次1–20篇，不自动分页、不调用LLM或抓取全文。最多两次请求，每次网络超时15秒，响应体最多4MiB；没有自动重试或多进程共享限流。
- 同一进程共享锁，发起请求间隔至少0.4秒。NCBI无密钥通常限制为每IP每秒3次；CLI多进程/其他客户端共享出口时仍可能超限，部署前需统一限流。429返回`rate_limited`，不会自动加大并发。
- 保存原检索式、NCBI解释后的检索式、UTC起止时间、耗时、总命中数、本次PMID序列及响应SHA256。哈希用于来源比对，不是完整原始响应档案，也不保证日后实时检索排序不变。
- `ok`表示本次请求PMID全部取得；`no_results`仅表示有效接口响应零命中。检索词被拒绝、网络/HTTP失败和解析失败为`error`；缺少记录为`partial_fetch`并列出缺失PMID。无效检索词不能据此判断“没有研究”。
- 文献保存标题、期刊、出版日期原字段、PMID、可空DOI/PMC标识、PubMed链接、分段摘要与出版类型。`evidence_scope`仅为`abstract`或`metadata_only`；`full_text_retrieved`始终为false。有PMC标识不代表已读全文，出版类型不代表已经过人工证据质量评议。
- 外部文本仅作为数据输出。CLI不执行其中指令，不输出模型总结、推荐用药或全文页码；后续进入HTML/Markdown或模型上下文时仍须分别做转义和指令隔离。

接口依据：[NLM E-utilities说明](https://www.nlm.nih.gov/dataguide/eutilities/utilities.html)、[NCBI调用限制](https://eutilities.github.io/site/API_Key/usageandkey/)。使用PubMed数据需同时查看[NCBI免责声明与版权说明](https://www.ncbi.nlm.nih.gov/About/disclaimer.html)，摘要不因可以检索就自动获得任意再分发授权。

## 数据库设计

| 表 | 作用与约束 |
|---|---|
| metadata | 数据库用途标记，拒绝误用其他应用的v1数据库 |
| articles | PMID主键；保留原DOI，v2新增doi_key索引用于保守查重，避免错误合并不同PMID |
| search_runs | 每次检索独立ID，保存成功/零结果/失败状态及完整本次解析快照；时间索引 |
| run_articles | 外键关联批次和文献、批次内PMID与排名唯一，保存当时文献内容 |

每次保存使用同一事务，任一记录写入失败则整批回滚。重复检索可以更新文献目录，但历史批次内容保留。第一阶段为v1，后续新库直接初始化v2，合法v1先在线备份再事务迁移；非空未标记库、其他应用库与未来版本拒绝使用。该升级仅针对文献库，不是会话库升级，也不引入PostgreSQL/ORM，详见[来源关系与迁移](PUBMED_EVIDENCE.md)。

## 验证与后续

离线测试覆盖结构化摘要、缺摘要、部分获取、响应错配/重复、超时/限流/坏响应、参数限制、排序保持、持久回放、PMID去重、历史不变、失败事务回滚和数据库身份保护。真实接口验证与离线结果见 [验收记录](../audits/pubmed_v1/report.json)。真实命中只证明接口与数据链路工作，不证明相关性、临床有效性或证据质量。

M4-02b聊天集成与M3-07会话备份已在后续完成；M4-03已补[来源关系/保守查重](PUBMED_EVIDENCE.md)。后续为证据清单/综合报告、人工判重与证据质量评议，暂不迁移现有只读药材/药典数据库。
