# PubMed研究镜像本地验收：b227f35

2026-10-18：从干净提交 `b227f35e2f2b92e76aadaeb17989c8842194f320` 构建本地镜像 `tcm-research-chat:b227f35`，407,827,706字节（约408 MB），linux/amd64。新功能包括PubMed显式检索、来源关系与DOI提示、文献库v1→v2备份迁移、药典＋文献并列清单，以及会话备份CLI。没有新增模型或Tavily密钥要求。

## 当前入口

**http://127.0.0.1:7873/** 已启动且健康，PubMed已开启。新容器为 `tcm-research-next-chat-1`，独立Cookie为 `research_chat_next_session`，独立数据卷为 `tcm-research-next_research-memory`。原7860、7863、7872容器的ID、启动时间和健康状态验收前后不变；新端口不会读取原7872会话。

先输入 `/pubmed Astragalus membranaceus[Title/Abstract] AND randomized controlled trial[Publication Type]`。出现文献卡后，点击“导出药典＋文献证据清单”，填写“黄芪的功效”并下载。药典单独查询仍用 `/research 黄芪的功效`。

镜像未包含药典、索引、ETCM数据或会话库，仍需项目原有四项只读来源挂载。新卷只保存新入口的数据，不是旧卷迁移。使用者选择并列资料不代表系统已经核验药典对象与文献研究对象一致。

## 启停

在项目根目录启动或恢复本地验收版本：

```bash
RESEARCH_IMAGE=tcm-research-chat:b227f35 RESEARCH_PORT=7873 \
RESEARCH_COOKIE_NAME=research_chat_next_session ENABLE_PUBMED=1 \
docker compose -p tcm-research-next -f compose.research.yaml up -d --no-build chat
```

停止并保留卷：

```bash
docker compose -p tcm-research-next -f compose.research.yaml stop chat
```

不要使用 `down -v` 作为普通停止命令，它会删除新入口的会话和文献台账。默认Compose仍是7872、原Cookie及PubMed关闭；操作新版须带上面的项目名和环境参数，避免操作旧服务。

## 验证与来源

- 本地主机41模块401项严格离线回归通过，无跳过或网络尝试；不是新提交的远程CI结论。
- 12组真实容器验收通过：默认关闭且不建文献库、显式开启真实PubMed、原药典/ETCM功能、v1备份迁移及索引、组合清单授权与下载、浏览器及移动宽度、Cookie隔离、2次实际重启、2次实际重建、删除后不复活、模拟数据清理、原服务不变。
- 验收用真实PubMed请求返回5条记录；仅验证链路与来源约束，不评价临床质量。浏览器阻断外部CDN，未作完整外部样式验收。
- 首次验收在模拟数据清理时因宿主脚本引号错误失败；修复后仅重置本轮专用测试卷，完整重跑通过。失败记录保留在忽略的var目录。后续脚本/文档修正不在镜像运行文件白名单内；已重新比对当前全部运行文件与构建回执一致，无需重建镜像。
- 构建树SHA256：`74c005662185ef22c3cf5284aadb2021c07fa5b222b7acf5547900bc08622c3f`。镜像ID：`sha256:4e30ccfcc06141321df9fb68470119c48e904905bf762fc65d13e2505c972d4d`。

公开[验收元数据](../../audits/docker_research_update_v1/report.json)、[构建回执](../../audits/docker_research_update_v1/build.json)、[容器检查](../../audits/docker_research_update_v1/container_acceptance.json)和[离线回归](../../audits/docker_research_update_v1/offline_ci.json)不包含药典正文、摘要、Cookie或密钥。实际下载和截图只存于忽略的 `var/docker_research_update_v1/acceptance_verified/`。

## 发布边界与下一步

这是本地镜像验收，尚未push Git或发布新Hub版本。Hub既有 `8n1eld/tcm-research-assistant:45f3c8d` 仍为旧功能；没有覆盖该标签或latest。后续先将新提交推送并检查远程CI，再以独立版本标签发布、核对Hub摘要并拉取复验。

正式多用户鉴权、临床质量评议、跨库对象核验、ETCM接入组合报告、长期语义记忆及容量指标仍未完成。本次完成可部署性验证，不代表完整M4综合报告或生产准入。
