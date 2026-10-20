# 本地Docker交付：c4de8bc

2026-10-20。已将已提交功能装入本地7876容器并验收；没有Git push或Docker Hub发布，原7873等服务不变。

## 当前可用版本

- 地址：**http://127.0.0.1:7876/**。
- 镜像：`tcm-research-chat:c4de8bc`，约408 MB。
- 干净源码提交：`c4de8bc04ad4c97244657b2e3af02e2a58df1d32`。
- 镜像ID：`sha256:42c34951a22ce155ba779b72ee35313826c2898edf7eee6a91318ec7169eefd6`。
- 源码树SHA256：`52a9a8144332866ca75d675ac7b9851ef101d9e230530e1504055acc4a7583d7`。
- Compose项目/容器：`tcm-research-candidate` / `tcm-research-candidate-chat-1`。

新增[请求失败定位](../CHAT_REQUEST_DIAGNOSTICS.md)、[靶点反查药材来源](../TARGET_HERB_LOOKUP.md)和[双药对比证据导出](../COMPARISON_EVIDENCE_EXPORT.md)。可依次尝试：

1. `CYP24A1关联哪些药材`：Q07973当前返回19条药材来源，展开原始见证，或点击具体来源查看其靶点/候选路径。
2. `P04278关联哪些药材`：下一页仍固定同一UniProt，可保存第2页为收藏。
3. `比较白术和黄芪的功效与用量`：下载双药Markdown原文/出处清单；再查“功效与注意”，观察缺失状态保留。
4. 遇到失败时保留页面请求编号，在容器日志中查对应的chat_request记录。

本配置仍是确定性研究工具，AI整理、自动模型规划、PubMed、网页搜索、影像和语音均关闭。此次验收模型/外网调用0，未发送药典或ETCM片段；它不是完整LLM质量或临床验收。

## 数据与持久化

继续使用原命名卷`tcm-research-candidate_research-memory`及Cookie名`research_chat_candidate_session`；没有替换、迁移或删除原会话/收藏库。更新前在旧镜像保存两项测试收藏，重建后恢复原查询、页码与笔记，验证完成后仅删除这些测试项。

资料继续只读挂载，ETCM使用此前校验的独立SQLite运行快照，原目录权限和资料不变。镜像仅含构建白名单代码，不携带原PDF、SQLite、会话、Cookie、权重或.env。实际容器以非root运行、根文件系统只读。

原7873、7872、7863、7860容器ID均未变。旧本地`local-20261020`镜像保留，远程已发布`8n1eld/tcm-research-assistant:d0d3bc3`未改变。

## 管理当前实例

在项目根目录执行，使用这台机器已经存在且校验过的快照：

```bash
RESEARCH_IMAGE=tcm-research-chat:c4de8bc \
RESEARCH_PORT=7876 RESEARCH_COOKIE_NAME=research_chat_candidate_session \
ENABLE_PUBMED=0 \
ETCM_RUNTIME_DIR="$PWD/var/research_update_20261020/runtime_catalogs/catalogs" \
ETCM_SOURCE_HOST=/home/zry/workspace/HDCTI/dataset/ETCM2.0 \
docker compose -p tcm-research-candidate \
  -f compose.research.yaml -f compose.research.etcm.yaml up -d --no-build chat
```

原快照不存在时先按[运行快照说明](RESEARCH_LOCAL_20261020.md)准备新的输出目录，不能新建空库或复制在线WAL主文件代替。新机器需自行准备资料和索引。

回滚代码可将上方RESEARCH_IMAGE改为`tcm-research-chat:local-20261020`，仍使用相同项目和卷，不执行down -v。旧版不认识新增target_herb收藏类型，可能无法回看/导入这些项目；回滚代码不等于回滚或迁移数据，不自动删除新类型记录。

## 验收

构建源为干净提交，74个实际运行文件SHA与构建回执一致。页面、健康、药典/药材就绪接口正常；固定分类日志与请求编号对应，未知客户端编号不复用。

26项跨版本/容器浏览器检查通过：旧镜像更新前6项、新镜像更新后6项、新靶点链路8项、双药导出6项。原收藏跨容器重建恢复，来源ID/页码稳定，预览和导出不改聊天，错误按钮恢复。双药浏览器409分支为合成响应，不谎称篡改了真实来源；来源失效的后端验证已有离线测试。临时Cookie文件、测试收藏及下载已清理。

复用已完成的73模块623项离线回归；本次未重复全量跑源码测试，新增验证针对构建产物和真实容器。数量不是医学准确率、独立任务完成率或生产容量。

见[可提交验收摘要](../../audits/research_delivery_c4de8bc/summary.json)。本机构建和完整验收记录在`var/research_delivery_c4de8bc/`，不提交私有运行数据。
