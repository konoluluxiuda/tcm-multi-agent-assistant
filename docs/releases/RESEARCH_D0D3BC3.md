# 自然语言与三页签研究镜像：d0d3bc3

2026-10-19，发布版本为 `8n1eld/tcm-research-assistant:d0d3bc3`，linux/amd64、约408 MB。相比b227f35，新增无需指令前缀的受限自然语言路由、实时数据概览和数据概览／药材与药典／现代文献三个页签，继续共用任务记录。

```bash
docker pull 8n1eld/tcm-research-assistant:d0d3bc3
```

## 使用

镜像仍须挂载[部署说明](../RESEARCH_DOCKER.md)中的四项本地资料，不包含药典、索引、ETCM数据库、会话或真实密钥。在项目根目录执行：

```bash
RESEARCH_IMAGE=8n1eld/tcm-research-assistant:d0d3bc3 RESEARCH_PORT=7873 \
RESEARCH_COOKIE_NAME=research_chat_next_session ENABLE_PUBMED=1 ENABLE_NATURAL_ROUTING=1 \
docker compose -p tcm-research-next -f compose.research.yaml up -d --no-build chat
```

访问 **http://127.0.0.1:7873/**。当前该入口已经运行同一镜像内容，无须为了标签名称变化重启。原7860、7863、7872服务保留；当前命名卷和Cookie沿用原7873，不会自动迁入7872会话。常规停止使用 `docker compose -p tcm-research-next -f compose.research.yaml stop chat`，不要用down -v删除卷。

自然语言入口开启后，可直接问“你能做什么”“收录了多少药材”“有哪些功效分类”“黄芪有什么作用”，选定后追问“用量呢”“出处呢”。页签提供明确的来源、字段和PubMed检索式表单；切换不执行工具、不清焦点，也不丢页面内草稿。刷新后恢复已提交任务，未发送草稿不持久化。PubMed仍须显式启用，只获取摘要和元数据。

## 构建与验收

镜像源自干净提交 `d0d3bc30e3182ce12eab95774d2b260bf9f82d9b`，后续cb16678仅更新文档和宿主验收脚本，已逐文件比对运行指纹一致。构建依赖和基础镜像沿用锁定版本，运行文件树SHA256为 `93c130374f3b6a3191b15182c6912c262b2a6725d350ccabffd501af4c2a5794`。

本地42模块415项禁网回归通过，11组实际容器浏览器验收与3组部署/恢复检查通过，包含真实PubMed、组合清单下载、键盘与移动端、草稿保留、会话恢复和恶意分类标签的纯文本展示。浏览器阻断CDN，不视为完整外部样式验收。完整证据见[本地记录](../../audits/research_workspaces_v1/report.json)。

## 边界与后续工作

当前自然语言是确定性意图路由和会话补全，不是任意语义理解、text-to-SQL或模型自主规划。概览将来源记录与去重药材种数分开，原始分类不自动翻译或合并；两个数据库数量不相加。三个页签共享一个任务，不提供多任务归档或账户管理。

通用术语解释、药典与文献的研究对象核验、临床质量评议、ETCM中文跨库映射和正式多用户准入仍未完成。下一业务任务按M4-04b推进研究对象范围与来源核验；自然语言覆盖以收集真实失败问法和独立意图评测为后续依据。

## 远程发布复核

Git已推送至cb16678；该提交的[离线回归](https://github.com/konoluluxiuda/tcm-multi-agent-assistant/actions/runs/39145489787)和[Docker构建](https://github.com/konoluluxiuda/tcm-multi-agent-assistant/actions/runs/39145489730)均成功。远程Hub清单、按摘要拉取和非root/依赖/新功能模块/私有资料缺失检查通过：

```text
sha256:488329638faa83f3d5aec1a75cff8ad4601ef42dbdb231c117c55a391e930578
```

固定镜像内容时，将启动参数中的RESEARCH_IMAGE改为 `8n1eld/tcm-research-assistant@sha256:488329638faa83f3d5aec1a75cff8ad4601ef42dbdb231c117c55a391e930578`。本次保留旧标签b227f35和45f3c8d的原摘要，未写latest；没有切换或重启任何在用容器。

[发布审计](../../audits/docker_release_d0d3bc3/release.json)、[远程CI回执](../../audits/docker_release_d0d3bc3/remote_ci.json)和[清单](../../audits/docker_release_d0d3bc3/manifest.json)只含元数据，不上传会话、Cookie、密钥、药典正文、截图或实际下载报告。本次验证证明工程交付，不代表药典全文正确率、临床证据质量或人工签核。
