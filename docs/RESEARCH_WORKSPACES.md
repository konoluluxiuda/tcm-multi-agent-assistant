# 三个功能页签与共用任务记录

当前源码在精简研究部署开启自然语言入口时提供三个功能页签，普通医疗部署保持原界面。初始页签为数据概览；鼠标、左右方向键、Home和End均可切换，不依赖Bootstrap脚本。

| 页签 | 可执行操作 | 来源和边界 |
|---|---|---|
| 数据概览 | 主动更新本地统计、查看能力说明；在面板内查看数量和原始分类 | 复用只读统计工具；不自动联网，不把记录数当作去重药材种数 |
| 药材与药典 | 明确选择中国药典或ETCM，填写名称；药典可选择功效、用量、来源、性味与归经、注意、炮制或贮藏 | 药典使用已收录中文名；ETCM用拼音/拉丁/英文名或来源ID，返回来源记录，不将药典字段用于ETCM |
| 现代文献 | 输入明确PubMed检索式、回看当前批次；通过结果卡导出组合清单 | 仍受PubMed开关控制，只取摘要/元数据；关闭时保留不可用说明并禁用操作 |

下方保留统一聊天和全部任务记录。页签用于选择操作面板，不限制统一聊天路由。资料查询表单选择具体来源，提交时才调用原来的工具；不会新增自主模型规划或SQL生成。查询结果与原页/引用卡仍出现在共用任务记录中，概览另在面板显示。

## 状态与会话

- 切换页签不发起/chat、不更改服务器会话、不清除当前工具焦点。
- 页面内各表单草稿随页签切换保留；通过面板提交不会覆盖统一聊天框里的未发送草稿。
- 只有主动执行工具时才沿用现有焦点切换规则。当前模型仍是一个会话的当前工具焦点；不同页签不是互相独立的会话或多任务列表。
- 药典、ETCM与文献对象之间没有自动翻译、映射或复制；概览/能力查询继续保留工具焦点。
- 已提交消息、焦点和文献引用保存在原SQLite命名卷；刷新后从Cookie恢复。未发送的表单草稿只在页面内存，刷新后不保留；页签初始回到概览。概览列表须主动更新，不重放旧统计作为实时结果。
- “清除会话与已保存记忆”仍删除本会话，额外清空页签表单、概览面板和状态提示；不会删除独立文献台账。该按钮不是归档，也不是可回切的“新建任务”。

## 交互和显示

查询进行中禁用表单控件避免重复提交；页签仍可切换。请求失败保留输入并提示查看任务记录。数量、标签和焦点名称用textContent显示，不执行资料中的HTML。工具兼容指令和示例收进可展开区域；研究界面使用中文标题和本地“发送”文字，字体图标或外部样式失效时仍能操作。

当前临时7874预览通过11组真实浏览器验证，含真实文献与清单下载、显式ETCM/药典、草稿/焦点、刷新、键盘、移动端、合成恶意标签、失败恢复和清除。42模块415项严格离线回归通过。后续干净提交d0d3bc3构建约408 MB本地镜像，已更新7873，并在实际容器完成11组浏览器验收和3组部署/恢复检查；原卷、Cookie与提交消息保留，原三服务未改变。后续已push并发布[Hub版本d0d3bc3](releases/RESEARCH_D0D3BC3.md)，远程CI及摘要/拉取复验通过。公开[浏览器记录](../audits/research_workspaces_v1/host_browser.json)和[离线回归](../audits/research_workspaces_v1/offline_ci.json)只含元数据，截图/下载保留忽略的var目录。浏览器阻断外部CDN，不等同于完整外部样式验收。

后续按实际任务需要增加多任务归档、所有权或多用户服务，而不是将页签数量作为Agent能力指标。通用术语解释、研究对象核验与独立意图效果评测仍需各自的数据和验收。

## 当前本地部署

**http://127.0.0.1:7873/** 已运行 `tcm-research-chat:d0d3bc3`，可刷新页面使用新页签。命名卷仍为 `tcm-research-next_research-memory`，Cookie仍为 `research_chat_next_session`。临时7874主机预览验收后停止。

在仓库根目录恢复当前本地版本：

```bash
RESEARCH_IMAGE=8n1eld/tcm-research-assistant:d0d3bc3 RESEARCH_PORT=7873 \
RESEARCH_COOKIE_NAME=research_chat_next_session ENABLE_PUBMED=1 ENABLE_NATURAL_ROUTING=1 \
docker compose -p tcm-research-next -f compose.research.yaml up -d --no-build chat
```

常规停止用 `docker compose -p tcm-research-next -f compose.research.yaml stop chat`，不要使用 `down -v` 删除会话及文献卷。需要退回上版自然语言页面时，保留其余参数，将镜像改回本地 `tcm-research-chat:03238f8`；不需要删卷。

公开[总验收](../audits/research_workspaces_v1/report.json)、[构建](../audits/research_workspaces_v1/build.json)、[容器部署恢复](../audits/research_workspaces_v1/container_deployment.json)和[容器浏览器](../audits/research_workspaces_v1/container_browser.json)只含元数据。截图和实际下载保留在忽略的var目录。2026-10-19后续已发布Hub版本 `8n1eld/tcm-research-assistant:d0d3bc3`，包含自然语言/概览/页签增量，Git推送和远程CI已完成，见[发布记录](releases/RESEARCH_D0D3BC3.md)。运行文件指纹与干净构建一致，后续文档及宿主验收脚本修正不改变镜像运行内容。
