# 聊天入口的药材来源查询

2026-10-16 更新：已改为本地SQLite成功轮次快照，可在保留Cookie和数据库后重启恢复，并可删除/过期。下方进程内状态描述为首轮交付记录，当前实现以 [持久记忆说明](PERSISTENT_MEMORY.md) 为准。

2026-10-15，M1-05 的有限聊天集成已实现。随后已补充 [药材会话状态与字段追问](TCM_CHAT_CONTEXT.md)，下方首轮验收记录保留。原医疗主页面可以用明确指令查询同一 SQLite 快照，结构化结果不经过模型改写。它是来源资料查询，尚不是完整的中草药研究 Agent。

## 使用

在主应用聊天框输入：

```text
/herb BaiBu
/herb HuaShi
/herb etcm2-local:<完整的64位来源编号>
```

支持数据库已收录的拼音、拉丁名、英文名精确查询（沿用已有大小写、空白规范）。`HuaShi` 返回两个来源，点击“选择此来源”会以完整来源 ID 发起新查询，不自动合并实体。每条资料卡显示字段值、缺失状态及可展开的原值、JSON Pointer、文件 SHA-256、数据版本和未核验警告。PMID 仅为关联标识，不表示已经读过或核验文献。

`/herb 百部` 明确返回中文名称未映射；未找到、空指令、异常来源编号有各自状态。数据库缺失、Schema 不兼容或指定版本未导入时，返回可显示的结构化错误；不可用数据库/版本的 `/chat` HTTP 状态为 503，不回退到模型生成药材资料。

## 配置与部署

- `TCM_DB`：主应用进程可读的 SQLite 文件；默认指向仓库 `var/tcm/herbs.sqlite3`，不依赖启动目录。
- `TCM_DATASET_VERSION`：可选，建议部署时固定 `local-2026-10-10`；未设置时沿用查询模块的最近导入批次选择。
- 主应用启动仍需要已有聊天/嵌入配置；明确的 `/herb` 分支每次查询不调用聊天、嵌入、Tavily 或模型护栏，因此不需要 Tavily 密钥。
- 查询模块以 SQLite 只读连接打开文件；原始 JSON 和数据库不打进公开镜像。

本轮修改的是仓库源码，没有重建或发布 Docker 镜像。独立轻量服务 `compose.tcm.yaml` 的 7863 页面不变。要在原医疗主应用容器使用此功能，需用本轮代码重建主应用镜像，并给该容器挂载数据库及传入两个配置，例如在已有 `docker run` 参数中添加：

```text
--mount type=bind,src=/绝对路径/var/tcm/herbs.sqlite3,dst=/data/herbs.sqlite3,readonly
-e TCM_DB=/data/herbs.sqlite3
-e TCM_DATASET_VERSION=local-2026-10-10
```

主应用容器仍监听 8000；宿主端口取决于实际 `-p` 参数。本轮未执行容器验收，浏览器验收使用临时本地 Uvicorn 服务。

## 实现与信任边界

`tcm/chat.py` 只识别 `/herb` 指令，普通聊天和图片请求沿用原路由。LangGraph 在输入护栏前分流明确指令，直接调用只读 repository；这一分支只展示来源事实，不经过模型护栏或生成链。

`/chat` 增加可选 `tcm` 字段；下轮初始化时清空 `tcm_request/tcm_result`，普通聊天不携带上一轮资料卡。完整工具结果仍保存在进程内 checkpoint 状态中，但仅固定状态说明进入模型使用的消息历史；来源字段不作为对话上下文。页面对来源值和当前用户输入使用 DOM `textContent`，来源中的 HTML 不能执行。原普通模型回答的 Markdown 渲染未在本轮重构。

最多展示 20 条歧义来源，并保留总数和截断标志。来源 ID 不是经过整理的统一药材实体 ID。资料卡只保留规范字段和必要出处，省去重复的原始整段对象。

## 验证与限制

- 本地离线回归 **109/109**（14 模块），新增 9 项解析/来源/故障/真实图与 `/chat` 测试。测试阻断网络，显式查询如果调用模型或模型护栏会失败；同会话普通聊天回归和不同会话隔离通过。
- 真实 Chromium：合成数据 **6 项**、真实本地快照 **5 项**。验证名称查询、来源展开、同名选择、中文降级、未找到和空指令；合成数据额外验证来源及用户 HTML 仅显示文本。CDN 请求被阻断，TCM 页面流程仍通过。
- 原冻结验收 **30/30**，人类签核状态仍为 `source_reconciled_pending_human_signoff`；SQLite SHA 保持不变。
- 报告：[离线回归](../audits/tcm_chat/offline_ci.json)、[合成浏览器](../audits/tcm_chat/browser_fixture.json)、[真实快照浏览器](../audits/tcm_chat/browser_snapshot.json)、[原验收集](../audits/tcm_chat/golden.json)。自动检查不代表科研语义正确或业务完成率。

可重复命令（浏览器额外需要 Playwright/Chromium；`--server-python` 指向已安装主应用 CI 依赖的 Python）：

```bash
python -m scripts.run_offline_ci --out /tmp/tcm-chat-ci
python -m scripts.eval_tcm_golden --out /tmp/tcm-chat-golden.json
python -m scripts.verify_tcm_chat_ui --server-python /path/to/server/python --out /tmp/tcm-chat-fixture.json
python -m scripts.verify_tcm_chat_ui --server-python /path/to/server/python --database var/tcm/herbs.sqlite3 --out /tmp/tcm-chat-snapshot.json
```

浏览器脚本会用临时服务、测试用模型配置和禁止调用的模型替身，不读取真实 `.env` 凭据；不会启动付费模型。浏览器测试是默认离线 CI 之外的可选验收，不要求公共 CI 下载私有快照。

首轮尚不支持跨轮追问；后续已实现显式药材槽位及受限字段追问，并实际执行那两条候选，见 [后续交付](TCM_CHAT_CONTEXT.md)。自然语言工具规划、中文别名、会话持久化及聊天内筛选/比较仍未实现；模型规划是否有收益仍需真实任务对照。
