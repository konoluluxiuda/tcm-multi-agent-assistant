# 研究工具镜像发布：45f3c8d

2026-10-18，已发布到 [Docker Hub：8n1eld/tcm-research-assistant](https://hub.docker.com/r/8n1eld/tcm-research-assistant)。本次发布为 `linux/amd64`，约408 MB；使用提交标签，不覆盖旧医疗镜像或发布 `latest`。

```bash
docker pull 8n1eld/tcm-research-assistant:45f3c8d
```

来源提交：`45f3c8d9d19d3833d9d4018ff3b1ece927a863ae`。远程清单摘要、按摘要拉取和镜像内容检查均已验证：

```text
sha256:aa143dff36c6baa7a513fd4e8c237e201c288fb9826535b1a01ab8d0697214ff
```

## 运行

在该仓库根目录使用现有 Compose，先准备 [部署说明](../RESEARCH_DOCKER.md) 中的四项本地只读资料：

```bash
RESEARCH_IMAGE=8n1eld/tcm-research-assistant:45f3c8d \
docker compose -f compose.research.yaml up -d --no-build
```

访问 `http://127.0.0.1:7872/`，容器内部端口为8000。原7860、7863服务保持独立。需要固定摘要时，可将 `RESEARCH_IMAGE` 设置为 `8n1eld/tcm-research-assistant@sha256:aa143dff36c6baa7a513fd4e8c237e201c288fb9826535b1a01ab8d0697214ff`。

镜像只包含程序与依赖，**不包含 PDF、研究索引、ETCM数据库、聊天记忆或真实密钥**。没有挂载所需资料时，不能仅凭拉取镜像就开始研究查询；`/ready/research` 会明确返回未就绪。会话保存在独立命名卷，删除容器不等于删除该卷。

## 已交付与验收

- 主LangGraph显式 `/research`、结构化 `/herb`、药材焦点追问与来源回查。
- 字段出处与原页红框、OCR/研究视图对照、本轮单药Markdown证据清单。
- 有限字段改述和四组字形对应；炮制品范围、未知名称与未展开引用保护。
- SQLite成功轮次记忆、独立Cookie名称、重启/容器重建恢复及清除后不复活。
- 干净提交构建、固定基础镜像/依赖、代码白名单、非root/只读挂载与就绪检查。

[远程离线回归](https://github.com/konoluluxiuda/tcm-multi-agent-assistant/actions/runs/39111270540) 337/337通过；[精简镜像CI](https://github.com/konoluluxiuda/tcm-multi-agent-assistant/actions/runs/39111270656)构建及内容检查通过。本机10组完整容器检查通过，并验证干净提交镜像与原验收运行文件指纹一致、切换版本后签名会话与焦点恢复正常。发布后按摘要从Hub拉取，再检查非root用户、预览依赖和私有文件缺失。

这些是工程和来源定位验收，不是药典全文正确率或人工签核。该镜像不提供通用聊天、旧医学RAG/CV/语音、拼音自动映射、现代文献综述或患者诊疗决策。

发布和构建验收证据见 [release.json](../../audits/docker_release_45f3c8d/release.json)。下一功能优先推进 M4-02 PubMed文献工具，保留本地传统记载与现代研究证据的来源区分；正式多用户上线前仍需完成账户权限、备份恢复与服务指标。
