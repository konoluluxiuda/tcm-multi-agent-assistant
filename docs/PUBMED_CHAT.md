# 主聊天PubMed显式工具

本轮接入现有LangGraph主图，直接执行文献工具，不经过模型路由、输入/输出模型护栏或Tavily。默认关闭。输出来源元数据与可用摘要，不生成临床结论，不把药典传统记载和现代研究合并。

## 配置与使用

在现有主应用的启动环境设置 `ENABLE_PUBMED=1`；`PUBMED_DB` 默认 `var/literature/pubmed.sqlite3`。这两个变量与 `ENABLE_WEB_SEARCH` / `TAVILY_API_KEY` 无关。原应用初始化仍沿用现有Chat/Embedding配置，但这个工具分支不调用它们。

```text
/pubmed Astragalus membranaceus[Title/Abstract] AND randomized controlled trial[Publication Type]
/pubmed-last
/pubmed-clear
```

每次最多取5篇，显式检索式最多2000字符；不自动翻译中文药名、不把“它的现代研究”猜成新的检索式。`/pubmed-last`只回放当前会话焦点批次，不重新联网；`/pubmed-clear`清除焦点。切换到普通聊天、药材或药典任务也结束文献焦点，避免跨任务误用来源；历史批次仍在台账中。

证据卡显示原检索式、解释式、UTC时间、状态、标题、期刊、出版类型、PMID/DOI链接及分段摘要。无摘要显示“仅元数据”，所有记录均标记未读全文。出版类型是PubMed来源标注，不是系统完成的证据质量评级。复方研究不能直接当作单味药的临床效力证据。

后续已补充[撤稿/更正/版本关系、来源类型与DOI候选](PUBMED_EVIDENCE.md)。提示均来自本次返回的元数据；未发现提示不能判断文章没有问题，旧批次缺少关系记录时不自动重算。

同时启用药典工具后，当前文献卡提供[药典＋文献证据清单下载](COMBINED_EVIDENCE_REPORT.md)。手工填写明确药名/字段，与本会话批次并列导出，研究对象对应关系不自动确认；该导出不增加消息或切换工具焦点。

## 持久化与隔离

- 文献台账保存完整解析结果；会话快照只保存随机批次ID和内容SHA256，不复制标题/摘要或工具正文进模型消息历史。
- 重启后，`/conversation`从该会话快照引用恢复证据卡；不接受客户端指定任意批次ID。另一Cookie没有此焦点，不能用`/pubmed-last`读取他人的批次。
- 批次丢失或内容校验不一致时拒绝回放，不自动重搜或改用模型。旧药材/药典快照保持兼容，无需修改会话数据库Schema。
- 清除会话删除消息和批次焦点，**不删除独立文献台账**。原检索式可能含研究隐私，台账暂为本机私有存储；未实现用户级台账所有权、批次删除/TTL、共享收藏或公开查询API。多用户上线前需补权限和保留/删除策略。

## 失败处理与预算

空指令返回400，工具关闭/繁忙/存储故障返回503，失效快照409，上游失败502；均携带结构化`pubmed`结果，页面显示错误卡。部分获取以200加`status=partial`及缺失PMID显示；合法零命中也不代表“没有相关研究”。错误和零命中可保留为批次供回放。

每次最多两次官方请求、单次网络超时15秒、响应最多4MiB；每进程最多一个活跃PubMed工具操作，忙时直接拒绝，不排队调用模型。同进程请求间隔至少0.4秒。多进程/多实例共享出口仍须部署统一限流；没有自动重试或硬性整体墙钟截止。

## Docker范围

源码Compose已增加 `ENABLE_PUBMED=${ENABLE_PUBMED:-0}`，台账写入现有持久卷的 `/app-memory/pubmed.sqlite3`。Dockerfile也设置默认关闭和卷内路径。已发布的 `45f3c8d` 镜像不包含新功能，单独给旧镜像添加变量不会生效。

需要使用当前源码先构建新本地镜像，再由维护者切换运行：

```bash
python -m scripts.build_research_image --tag tcm-research-chat:pubmed-local --receipt var/pubmed_chat_build.json
RESEARCH_IMAGE=tcm-research-chat:pubmed-local ENABLE_PUBMED=1 docker compose -f compose.research.yaml up -d --no-build chat
```

本轮没有执行镜像切换或Hub发布；后续镜像验收须覆盖出站网络、卷内文献台账及原有药典/会话功能。

## 验证与下一步

工程测试、真实浏览器和实际网络结果见 [本轮验收](../audits/pubmed_chat_v1/report.json)。浏览器使用合成文献检验HTML文本转义、摘要显示、来源链接、会话隔离、真实进程重启、清除和移动端；另用真实主图HTTP服务请求NCBI，验证实际文献与回放链路。浏览器测试屏蔽CDN，不作为完整外部样式的视觉验收。

文献接口/数据模型详见 [第一阶段](PUBMED_LITERATURE.md)。M3-07[会话在线备份与隔离恢复](MEMORY_BACKUP.md)已完成本机手动运维范围，但不包含本独立文献台账。文献库首次打开v1会先备份再升级为v2，旧批次内容保持不变，详见[迁移手册](PUBMED_EVIDENCE.md)。M4-04a受限组合清单已交付，下一步新版Docker整体验收；研究对象核验、人工判重、临床质量评议和综合解释仍待完成。

2026-10-18部署增量：b227f35本地镜像已通过12组容器验收，7873已启用PubMed、迁移和组合下载；原7872与Hub45f3c8d保持不变。当前部署状态及启停方法以[新版本地记录](releases/RESEARCH_B227F35_LOCAL.md)为准。
