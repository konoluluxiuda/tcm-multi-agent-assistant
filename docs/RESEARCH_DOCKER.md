# 精简药典研究聊天 Docker 部署

当前精简部署面向 `/research`、`/herb` 与显式 `/pubmed` 工具，保留主 LangGraph 的确定性工具分支、证据卡、原页预览、Markdown 导出和 SQLite 完成轮次记忆。不是旧医学 RAG、影像、语音或模型自主规划的完整镜像。

2026-10-18增量：新版 **`8n1eld/tcm-research-assistant:b227f35`** 已发布，远程CI、12组容器验收及Hub摘要/拉取复验通过。**http://127.0.0.1:7873/** 已启用PubMed和组合清单；新版启停、Cookie和卷见[发布记录](releases/RESEARCH_B227F35.md)。以下默认7872命令保留旧部署含义。

本机后续增量：7873已更新为本地 `tcm-research-chat:03238f8`，提供自然语言入口、实时数据概览与轻量导航；新能力尚未发布Hub，不能用Hub b227f35替代此版本。当前本机启停以[自然语言入口说明](NATURAL_ROUTING.md)为准，旧版发布记录保留历史含义。

## 当前入口

| 服务 | 本机地址 | 状态 |
|---|---|---|
| 新PubMed研究容器 | http://127.0.0.1:7873/ | 03238f8本地增量，独立卷与Cookie |
| 原研究聊天容器 | http://127.0.0.1:7872/ | 新增的隔离部署 |
| 原医疗容器 | http://127.0.0.1:7860/ | 保留 |
| 原药材查询容器 | http://127.0.0.1:7863/ | 保留 |
| 主应用本机预览 | http://127.0.0.1:7871/ | 不属于 Docker 端口 |

容器内部端口是 **8000**，本机通过 **7872** 访问。已从干净提交构建并发布 **`8n1eld/tcm-research-assistant:45f3c8d`**，约408 MB，平台为 `linux/amd64`；已核对远程摘要并按摘要拉取复验。最初的工作树验收镜像保留在历史审计中，不作为本次发布来源，见 [发布记录](releases/RESEARCH_45F3C8D.md)。

## 构建与启动

在项目根目录执行，需要 Docker Desktop/Engine 正常运行：

使用已发布镜像：

```bash
docker pull 8n1eld/tcm-research-assistant:45f3c8d
RESEARCH_IMAGE=8n1eld/tcm-research-assistant:45f3c8d \
docker compose -f compose.research.yaml up -d --no-build
```

从工作区自行构建：

```bash
python -m scripts.build_research_image --tag tcm-research-chat:local
docker compose -f compose.research.yaml up -d --no-build
docker compose -f compose.research.yaml ps
```

构建脚本只向临时目录复制指定运行文件，再以该目录构建。不会扫描或上传 `var/`、PDF、`.env`、模型权重、会话库和审计输出；也不需要改变旧会话目录权限。不要直接将整个工作区作为此镜像的构建上下文。本轮直接构建曾因 `var/memory` 权限失败，已由上述方式解决。

`Dockerfile.research` 固定基础镜像摘要，`requirements-research-chat.txt` 与 `constraints-research-chat.txt` 固定本轮 Python 3.11/Linux 依赖。来源标签放在依赖层之后，修改应用代码可复用依赖缓存。构建回执默认保存于 `var/docker_research_v1/build.json`，记录实际镜像 ID、源码指纹、父提交和工作树状态。

Compose 不读取 `.env` 作为服务环境文件，不需要模型或 Tavily 密钥。SDK 初始化使用无效的本机配置占位值；`RESEARCH_TOOLS_ONLY=1` 在创建会话和调用图之前拒绝通用聊天，已有明确工具请求和药材追问保留。该开关在原主应用默认关闭，旧部署行为不变。

页面仅展示此部署可用能力。关闭研究工具、缺少/改变来源时，`/ready/research` 返回 503；`/health` 仍只代表 HTTP 进程存活。镜像健康检查使用研究就绪接口，不把进程启动等同于数据可用，也不把来源哈希验证当作内容质量批准。

## 来源与记忆

以下本地文件必须存在，均为只读挂载，缺少时不自动创建空文件：

| 本地位置 | 容器位置 |
|---|---|
| `var/mineru_retrieval_v2/` | `/research-index` |
| `data/《中国药典》2025年版 一部.pdf` | `/research-sources/high.pdf` |
| `data/《中国药典》2025年版 一部 全本.pdf` | `/research-sources/full.pdf` |
| `var/tcm/herbs.sqlite3` | `/herb-data/herbs.sqlite3` |

会话使用独立命名卷 **`tcm-research_research-memory`**，数据库位于 `/app-memory/conversations.sqlite3`；该卷与原医疗容器、本机预览和独立研究台的会话库不同。签名 Cookie 的密钥随库保存，服务重启和容器重建后仍可恢复原会话，保留最近 20 条消息、默认闲置 30 天过期。浏览器需保留 Cookie。新部署使用独立的 `research_chat_session` 名称，与原主应用的 `session_id`、独立研究台的 `research_session` 分开，避免同一主机不同端口覆盖凭证；原部署默认名称保持不变。

```bash
docker compose -f compose.research.yaml restart chat
docker compose -f compose.research.yaml up -d --no-build --force-recreate chat
docker compose -f compose.research.yaml down
```

上述停止/重建不会删除命名卷。**`down -v` 会删除该部署的会话卷**，不要将其当作普通停止命令。页面“清除会话”删除当前会话；已验证删除后重启不会恢复旧消息或焦点。

容器以 UID 10001 运行，根文件系统只读；上传临时目录使用 tmpfs，不挂载源码。网络为普通桥接，仅发布 `127.0.0.1:7872`。本机 Docker Desktop 在 `internal: true` 网络下未实际发布端口，故未使用内部网络，也不声称这是网络层的出站隔离。

## 使用与验收

```text
/research 黃芪的服用剂量
/research 它每次多少克
/research 出处呢
/herb BaiBu
```

研究结果可查看原页、下载 Markdown。炮制品范围、未展开引用、拼音未解析与未人工审核状态均保留；普通聊天明确返回 403，不落入未安装的旧 RAG/CV 模块或模型调用。

完整容器验收脚本需要主机有 httpx、Playwright 和 Chromium；脚本仅允许带 `tcm-research/chat` 标签的新容器，实际执行两次重启及一次容器重建，使用专门创建的测试会话并清除：

```bash
python -m scripts.verify_research_container \
  --url http://127.0.0.1:7872 \
  --container tcm-research-chat-1 \
  --out var/docker_research_next \
  --executable /path/to/chrome
```

验收覆盖非 root/只读挂载、镜像不含私有载荷、来源不可用时拒绝就绪、主图查询、会话与 Cookie 名称隔离、原页图像、下载、ETCM 兼容、范围/引用保护、真实重启与重建恢复、删除后重启及浏览器移动端；10 组容器检查通过，摘要见 [容器验收](../audits/docker_research_v1/validation.json)。新增 6 项部署契约测试，完整离线回归 337 项通过。

Docker Image CI 已通过同一白名单脚本构建精简研究镜像，检查预览依赖和私有载荷缺失，并保存构建指纹；提交45f3c8d的构建及337项离线回归均成功。该工作流只构建、不发布，本次Hub上传另按用户指令执行。私有资料挂载和重启验收只在本机执行，不伪装成 CI 已验证。

本次仅发布精简研究镜像，未迁移旧容器和用户会话；账户权限、备份恢复、服务容量及药典人工质量验收仍未完成，M5 不因此整体勾选完成。发布源提交、镜像摘要和拉取复核见 [发布记录](releases/RESEARCH_45F3C8D.md)。

后续源码已新增默认关闭的PubMed聊天工具及持久台账配置，见[启用和重建说明](PUBMED_CHAT.md)。上述已发布45f3c8d镜像没有此功能；本轮没有重建、切换或重新发布镜像。

会话卷已完成不停服在线备份与新路径/新API恢复演练，旧签名Cookie及药典焦点恢复、删除标记保留，原7872未重启。命令和私有数据保留规则见[在线备份手册](MEMORY_BACKUP.md)。这不包含卷内其他数据库或药典挂载资料的备份，也不代表自动灾难切换完成。

源码还新增[药典＋文献组合清单](COMBINED_EVIDENCE_REPORT.md)，复用已有两个工具开关，不新增依赖或卷。需要新版镜像方可使用；当前45f3c8d仍只有原研究工具范围，下一步构建新版并验证PubMed开/关、迁移/持久化、组合下载和旧药典流程。
