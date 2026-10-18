# 主Agent显式药典研究工具

2026-10-18完成主应用接入。本机预览 <http://127.0.0.1:7871/>，独立研究对话 <http://127.0.0.1:7869/chat> 保留。

## 使用与实际能力

在主聊天依次输入：

```text
/research 甘草的功效
/research 它的用量
/research 出处呢
```

`/research-clear`清除研究焦点；主聊天“清除会话”删除完整会话。空命令和过长问题给出用法提示，不落入通用模型。

入口进入现有LangGraph中的`PHARMACOPOEIA_RESEARCH_AGENT`节点，复用从独立研究对话提取的`ResearchTool`。节点只读检索、返回固定状态说明和证据卡，然后结束该轮；不调用路由模型、生成模型或模型护栏改写原文。**这是显式工具分支，不是模型自主选择工具或新多Agent规划器。** 普通聊天仍沿用原路径，不会自动触发药典研究。

页面支持OCR原文/AI修订对照、字段/标题原页、未完成片段和相关母条目核对。主应用证据图片使用同域`/api/research/page`，仍以索引中的chunk/引用序号定位，不接受客户端文件路径或自定PDF页码。

现可点击“导出本轮证据 Markdown”保存本次查询的单药字段、双版本原文、出处和未完成状态。不是整味药材全部信息或综合文献报告，见 [导出说明](RESEARCH_EVIDENCE_EXPORT.md)。

炮制范围、空标题、未展开引用、未知名称与来源版本保护均保留。未审核字段不成为模型指令，也不会进入后续普通聊天的源正文上下文；历史只保存用户输入与固定状态说明。原书内容未导入ETCM正式库。

## 焦点与持久会话

主应用使用原`session_id`与主聊天SQLite完成轮次存储；独立7869研究对话仍使用另一数据库和`research_session`，两种入口不共享会话凭证。

ETCM焦点与研究焦点分开存储，研究槽位绑定索引、研究视图及检索/保护代码SHA。主图每轮重新加载完成状态，追问重新检索来源。旧ETCM快照无需迁移即可读取；研究焦点在原JSON上下文字段中使用`agent_contexts_v1`封装，不改会话数据库Schema版本。

- 显式`/research`轮次清除ETCM焦点；切回`/herb`或普通聊天清除研究焦点。
- ETCM的“它的药用部位”不会读取研究焦点；研究追问必须携带`/research`前缀。
- 查询失败/歧义清除旧焦点，不能默默沿用上一味药。例外是已选药名但尚未选字段时要求补字段，按原工具语义保留该药名。
- 数据/策略版本变化时，研究追问要求重新明确药名；源文件发生变化则提示维护者校验重启，不自动重建或补答。
- 刷新及重启恢复时，`GET /conversation`可重新检索上次研究证据；不从已存助手说明解析药名。

这是完成轮次恢复，不支持图中间节点继续运行。较早代码不认识新研究上下文封装；回滚前应备份会话库，并在新代码中清除研究焦点或改用独立旧会话库，不能把研究槽位当作旧ETCM槽位读取。

## 启用与配置

默认关闭，不要求原有部署必须拥有药典PDF或研究索引。显式启用：

```bash
ENABLE_PHARMACOPOEIA_RESEARCH=1 \
python -m uvicorn app:app --host 127.0.0.1 --port 7871
```

本机验证使用 `/tmp/tcm-ci-20261013/bin/python`。当前7871预览会话单独存于 `var/agent_research_preview/conversations.sqlite3`，不修改现有Docker的会话卷。

| 环境变量 | 默认位置/用途 |
|---|---|
| `ENABLE_PHARMACOPOEIA_RESEARCH` | 默认false，控制主图研究工具 |
| `PHARMACOPOEIA_INDEX` | `var/mineru_retrieval_v2` |
| `PHARMACOPOEIA_PDF_HIGH` | `data/《中国药典》2025年版 一部.pdf` |
| `PHARMACOPOEIA_PDF_FULL` | `data/《中国药典》2025年版 一部 全本.pdf` |
| `CHAT_MEMORY_DB` | 沿用主应用会话配置 |

源PDF与索引在第一次工具调用时加载并验证SHA，缓存服务只保留来源定位和只读连接创建逻辑；每个查询另开只读SQLite连接。未启用返回`research_disabled`，来源/索引不可用返回`research_unavailable`，API HTTP状态为503，均不回退模型或联网搜索。其它研究状态通过`research`结构返回，不能仅凭HTTP 200判断查询完成。

原页预览额外需要可选依赖：

```bash
python -m pip install -r requirements-research-preview.txt
```

该文件使用已验证的PyMuPDF 1.28.2及主项目锁定的Pillow 11.1.0，不安装OCR模型。未安装预览依赖时检索仍可运行，图片接口返回明确的不可用提示。本轮未修改Docker镜像或容器；容器启用还需自行在新镜像安装可选依赖、挂载两份PDF及索引为只读，并保留原会话卷。原始资料仍被Git和Docker构建忽略。

## 验证与复现

- 新增11项命令/真实LangGraph/API测试：研究分支零provider调用、工具/普通聊天/ETCM焦点隔离、旧快照兼容、重启恢复、源不可用不补答、禁用503、删除和状态序列化。离线测试记录网络尝试为0。
- 原会话存储8项、图会话3项、ETCM聊天19项兼容测试通过。
- 研究侧120项回归通过，含独立聊天共享工具抽取后的16项测试。以上不同测试文件合计161项，不重复计数已在研究侧包含的16项。
- 6组新主聊天浏览器检查通过：真实图节点/证据原页、会话隔离、范围/引用保护、实际主服务和浏览器重启后追问、删除/非法证据参数、文本安全与移动端。
- 原ETCM主聊天13组、独立研究聊天8组浏览器检查通过；独立研究服务在验证中实际重启一次。CDN资源在主页面验证中主动阻断，研究路径仍工作，页面脚本错误为0。
- 本机7871实际主应用实测三条示例命令成功，研究分支返回`llm_calls: 0`。这不构成模型能力、临床正确率或Docker发布验证。

冻结测试摘要与代码SHA见 [工程回归](../audits/agent_research_v1/tests.json)、[主页面](../audits/agent_research_v1/browser.json)、[ETCM页面回归](../audits/agent_research_v1/herb_ui.json)、[独立研究页面回归](../audits/agent_research_v1/standalone_ui.json)、[来源与代码校验](../audits/agent_research_v1/validation.json)。截图、凭证、会话库与完整输出只保留在`var/`。

主页面验收复现：

```bash
FONTCONFIG_FILE=/tmp/tcm-fontconfig.xml \
/tmp/tcm-api-check-20261012/bin/python -m scripts.verify_agent_research_ui \
  --server-python /tmp/tcm-ci-20261013/bin/python \
  --executable /tmp/tcm-playwright-20261012/chromium-1243/chrome-linux64/chrome \
  --out var/agent_research_ui_next
```

后续以具体研究需求决定功能：例如补齐已暴露的字段改述/拼音查找，或输出带出处的证据清单；保留当前工具路径作为对照。没有必要继续增加框架层或重复扩充测试题来替代实际功能，全文质量与人工签核仍未完成。
