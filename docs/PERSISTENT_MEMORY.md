# 可恢复的本地会话记忆

2026-10-16：聊天历史和药材选择状态默认写入本地 SQLite，不再依赖进程内 MemorySaver。服务重启后，同一浏览器保留有效会话 Cookie 即可恢复；页面重新打开会读取服务器历史，并重新核对最近药材的来源。

## 保存什么

- 每轮成功完成后的最近消息，沿用 `Config.max_conversation_history`，默认20条消息（不是20轮）。
- 药材的来源ID、版本、文件SHA和最近查询字段。
- 签名 Cookie 的服务器密钥及会话修订号。数据库中的会话键是凭证的SHA-256，不保存可直接重放的Cookie明文。

不保存每个图节点的中间状态，也不保存完整工具结果或历史checkpoint。关闭时正在运行且尚未提交的一轮需要重试；上一轮成功状态仍在。它是会话持久化，不是自动提炼个人画像、无限聊天档案或长期语义记忆。超过最近20条的文本不会进入之后的恢复上下文；药材槽位独立保留，直到按既有规则清除。

模型回复和用户输入通过 LangChain 的消息字典转换写入JSON，不使用pickle或动态类型反序列化。LangGraph仍负责单轮路由；末轮快照由 `agents/conversation_store.py` 管理，事务提交后才返回成功。每次数据库操作关闭连接，无需依赖程序退出时再保存。

## 本地使用

保持原有模型配置，启动主应用后自动建立：

```text
var/memory/conversations.sqlite3
```

`var/`、SQLite文件及辅助文件已从Git和Docker构建上下文排除。原始药材库保持独立和只读，不与会话表混用；若误指向其他SQLite数据库，存储层拒绝初始化。

可在 `.env` 配置：

```dotenv
CHAT_MEMORY_DB=/绝对路径/private-memory/conversations.sqlite3
CHAT_MEMORY_TTL_DAYS=30
CHAT_COOKIE_SECURE=0
CHAT_COOKIE_NAME=session_id
```

默认闲置30天后过期，期限以最后一次成功对话写入为准。访问过期会话时清除其内容；启动时也清理过期内容。长时间不重启的服务可定期执行：

```bash
python -m scripts.manage_memory
```

该命令仅打印清理数量，不输出聊天或凭证。HTTPS部署设置 `CHAT_COOKIE_SECURE=1`；本地HTTP保持0。改变数据库路径会切换到另一份会话与签名密钥，请勿把它当作迁移手段。

浏览器凭证使用HttpOnly、SameSite=Lax及明确Max-Age，关闭浏览器后仍可保留。无痕模式、清除浏览器Cookie或换设备后，不能自动找到原会话。该版本仍为本地匿名使用，没有账号登录、跨设备同步或用户级长期档案；有效Cookie持有者可以访问对应会话。

`CHAT_COOKIE_NAME` 默认仍为 `session_id`；同一主机的独立部署可使用不同名称，避免不同端口覆盖 Cookie。名称在启动时读取，须为字母开头、最多64位的ASCII字母/数字/下划线。改变名称不会迁移旧浏览器凭证；已运行的旧部署应保留原名称。

## Docker持久化

新增 [compose.chat.yaml](../compose.chat.yaml)，主应用宿主端口7860，容器端口8000。它使用独立命名卷 `chat-memory` 保存 `/app-memory`，并以只读方式挂载本地药材库。确保 `.env` 和 `var/tcm/herbs.sqlite3` 已准备，且7860未被旧主应用占用，再运行：

```bash
docker compose -f compose.chat.yaml up -d --build
```

之后关闭或重建容器，只要重新挂载同一命名卷，就能恢复该卷中的会话和签名密钥。`docker compose down` 保留卷；`down -v` 会删除卷。备份需同时包含签名密钥所在的整个会话数据库；运行中的SQLite应使用backup API或停机后备份，不能只复制主文件而忽略WAL。

不使用Compose时，也必须将 `CHAT_MEMORY_DB` 指向已挂载的可写目录，不能只把数据库放在容器可写层。现有独立查询服务 `compose.tcm.yaml` 不变。

**原始验收边界（2026-10-16）：** 当时WSL中Docker集成未启用，该轮只验证了临时Uvicorn/Chromium进程重启及Compose静态结构，未执行容器验收。

**2026-10-18补充：** 已在独立7872研究部署以命名卷 `tcm-research_research-memory` 实测两次容器重启和一次删除重建；签名Cookie及研究焦点均恢复，清除后重启保持为空。新部署使用 `research_chat_session`，没有替换原7860容器或其存储。10组容器检查及337项离线回归通过，未发布镜像；见[精简研究部署](RESEARCH_DOCKER.md)。这仍是成功轮次快照，不是原生checkpoint中间节点续跑。

## 恢复、清除与一致性

- `GET /conversation`：按签名Cookie返回近期消息和经当前药材库重查的资料卡，设置 `Cache-Control: private, no-store`；新会话返回空历史。
- `DELETE /conversation`：清除数据库中的消息和药材状态，更新会话凭证。页面“清除会话与已保存记忆”调用此接口，已不再只是清空页面。
- `/herb-clear`：仅清除药材焦点，仍保留对话历史；不同于删除整个会话。
- 普通聊天仍按原规则清除药材焦点。药材库来源变化后不会直接信任历史原值，仍要求重选。
- 数据库不可读写、损坏或Schema不兼容返回503，拒绝回退到临时内存，避免错误地显示“已保存”。页面恢复失败时禁止发送，并提示检查存储后刷新。
- 同会话请求在本进程串行执行；跨进程的旧修订写入返回409，避免覆盖已提交轮次。清除保留无聊天内容的删除标记，阻止仍在执行的旧请求重新写回。没有实现多工作进程的自动排队/重试。

删除后无法从本实现的旧checkpoint恢复，因为没有保存旧checkpoint；历史备份、文件系统快照不在该接口的删除范围内。现有影像临时文件/10分钟私有快照的生命周期不变，不作为持久聊天附件保存。恢复的文字按纯文本显示，最后一张有效药材卡从只读库重新生成。

## 验证

- 本地离线工程回归 **129/129**，16模块、零跳过。新增7项存储测试及3项真实图/API测试：文件重开恢复、Cookie签名、并发修订、删除竞争、过期、损坏、历史接口与失败轮次不提交等。
- 真实浏览器：合成来源 **13项**、实际快照 **12项**。包括真正结束/重启服务和Chromium，用保留的浏览器存储恢复历史及药材槽位，再清除、重启并确认不恢复。
- 原2条聊天候选再执行通过机器检查，原冻结集 **30/30**；药材数据库未变，人工签核及真实业务效果仍待完成。
- 证据：[工程回归](../audits/persistent_memory/offline_ci.json)、[合成浏览器](../audits/persistent_memory/browser_fixture.json)、[实际快照浏览器](../audits/persistent_memory/browser_snapshot.json)、[聊天候选](../audits/persistent_memory/candidates.json)、[冻结集](../audits/persistent_memory/golden.json)。

```bash
python -m scripts.run_offline_ci --out /tmp/tcm-memory-ci
python -m scripts.eval_tcm_chat_tasks --out /tmp/tcm-memory-candidates.json
python -m scripts.verify_tcm_chat_ui --server-python /path/to/server/python --out /tmp/tcm-memory-browser.json
```

浏览器依赖与实际快照参数沿用 [聊天查询说明](TCM_CHAT_LOOKUP.md)。测试使用隔离数据库和测试凭据，不读取或写入实际用户会话，不调用付费模型。

架构取舍见 [ADR-0002](decisions/0002-completed-turn-memory.md)。M3仍有账号所有权、原生checkpointer/框架迁移和数据库升级迁移等待办，本次不将整个M3标为完成。

## 2026-10-18：在线备份与隔离恢复

已新增标准库在线备份/检查/恢复命令，保留数据库内签名密钥、删除标记、原更新时间和TTL；恢复仅写新路径，拒绝覆盖原库或残余WAL。运行中7872容器的真实备份已在隔离新进程中验证旧Cookie恢复消息和药典焦点，已删除会话保持删除；原服务未重启或替换数据库。新增13项测试，完整376项通过。

默认包有效期7天，超期拒绝恢复，物理清理仍由运维执行；删除前旧备份仍可能含消息，不能保证恢复任意旧备份都遵守之后的删除。会话库备份不包含药典资料或PubMed独立台账。操作、保留与删除规则见[备份恢复手册](MEMORY_BACKUP.md)，公开[验收](../audits/memory_backup_v1/report.json)仅含元数据。
