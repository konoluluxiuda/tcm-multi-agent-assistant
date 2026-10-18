# PubMed研究镜像发布：b227f35

2026-10-18，已发布 [8n1eld/tcm-research-assistant:b227f35](https://hub.docker.com/r/8n1eld/tcm-research-assistant)，约408 MB，linux/amd64。包含显式PubMed检索与持久文献台账、来源关系与DOI提示、文献库v1→v2备份迁移、药典＋文献并列清单及会话备份CLI。

```bash
docker pull 8n1eld/tcm-research-assistant:b227f35
```

## 来源与验证

源镜像从干净提交 `b227f35e2f2b92e76aadaeb17989c8842194f320` 构建；随后 `6e6e8ce6558ef9ee5955af2c86f67398f25382d5` 只修正宿主验收脚本和记录，运行文件指纹保持一致。该提交的[离线回归](https://github.com/konoluluxiuda/tcm-multi-agent-assistant/actions/runs/39140105794)及[Docker构建](https://github.com/konoluluxiuda/tcm-multi-agent-assistant/actions/runs/39140105795)均成功，检查依赖、构建和私有文件缺失步骤通过；本地主机回归401项、真实容器验收12组通过，包含2次重启、2次重建。

Hub远程摘要与本地镜像一致，已按摘要拉取并检查非root UID10001、PDF预览依赖、新功能模块和私有资料缺失：

```text
sha256:4e30ccfcc06141321df9fb68470119c48e904905bf762fc65d13e2505c972d4d
```

运行文件树SHA256：`74c005662185ef22c3cf5284aadb2021c07fa5b222b7acf5547900bc08622c3f`。公开[发布审计](../../audits/docker_release_b227f35/release.json)、[远程CI回执](../../audits/docker_release_b227f35/remote_ci.json)及[本地验收](../../audits/docker_research_update_v1/report.json)仅包含元数据。

## 运行与持久化

仍须准备[部署说明](../RESEARCH_DOCKER.md)中的四项本地只读资料；镜像不携带药典、索引、ETCM数据库、会话或真实密钥。在仓库根目录执行：

```bash
RESEARCH_IMAGE=8n1eld/tcm-research-assistant:b227f35 RESEARCH_PORT=7873 \
RESEARCH_COOKIE_NAME=research_chat_next_session ENABLE_PUBMED=1 \
docker compose -p tcm-research-next -f compose.research.yaml up -d --no-build chat
```

访问 **http://127.0.0.1:7873/**，容器内部端口8000。当前7873已经运行同一镜像内容，无须为了镜像改名重启。原7860、7863、7872容器保持不变；旧版45f3c8d远程摘要复核未变，未写latest。

新入口使用独立卷 `tcm-research-next_research-memory` 和Cookie `research_chat_next_session`；会话及文献台账在卷中，原7872会话不会自动迁入。常规停止用 `docker compose -p tcm-research-next -f compose.research.yaml stop chat`，不要使用 `down -v` 删除卷。固定镜像内容时可将RESEARCH_IMAGE改为 `8n1eld/tcm-research-assistant@sha256:4e30ccfcc06141321df9fb68470119c48e904905bf762fc65d13e2505c972d4d`。PubMed默认关闭，上面的ENABLE_PUBMED=1为显式启用；此配置不要求Tavily或模型密钥。

## 试用与边界

输入 `/pubmed Astragalus membranaceus[Title/Abstract] AND randomized controlled trial[Publication Type]`，再在文献卡点击“导出药典＋文献证据清单”，填写“黄芪的功效”并下载。单独药典查询可用 `/research 黄芪的功效`。刷新、重启或重建后保留Cookie即可恢复当前会话；清除会话不等于清除独立文献台账。

本版本只提供摘要/元数据，不获取文献全文或评议临床质量；并列清单未核验研究对象一致性，不生成疗效结论。药典全文质量、人审签核、中文跨库映射、完整综合报告、正式多用户鉴权、长期语义记忆及容量指标仍未完成。

下一业务任务为M4-04b：明确药材、炮制品、复方、提取物和成分的对象范围及依据，未核验时保留提示，避免把复方或成分研究直接归到单药。对象核验通过后再推进比较与综合解释。
