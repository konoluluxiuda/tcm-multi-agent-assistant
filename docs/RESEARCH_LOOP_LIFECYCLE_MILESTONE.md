# 合成循环的会话生命周期与备份接入

2026-10-27。本轮把上一阶段的持久化循环接入显式启用的HTTP接口与聊天页演示面板，并扩展核心备份工具。默认关闭，模型与工具仍为固定合成实现，无外部调用。

## 使用

本机预览：http://127.0.0.1:7893/ 。打开“合成 Agent 循环演示”：

1. “运行演示”执行固定的虚构疾病查询与工具调用。
2. “演示暂停与恢复”在第一个工具结果保存后暂停。刷新页面，再点“继续执行”，已保存的调用不重复执行。
3. “删除记录”删除当前会话的journal记录。运行中的记录不能删除，旧revision也不能删除更新后的记录。
4. 模型或工具调用结果不确定时，“核对恢复状态”只将任务置为停止状态，不重新发出调用。

暂停后的恢复仍受原120秒总时间预算约束，停机时间计入其中。超时不会获得新预算。演示是同步HTTP执行入口，不是新后台队列，也未接入真实DeepSeek自动恢复。

```bash
/tmp/tcm-ci-mail-env/bin/python -m scripts.preview_local_tasks \
  --port 7893 --state var/research_loop_lifecycle_v1/preview_state \
  --diseases var/disease_catalog_v1/build/diseases.sqlite3 \
  --synthetic-loop
```

新环境使用已装项目依赖的Python。其他部署需显式设置`ENABLE_SYNTHETIC_LOOP=1`；journal默认在会话库旁的`research_loop.sqlite3`，可用`RESEARCH_LOOP_DB`指定。本机其他预览没有自动开启此入口。

## 鉴权与存储边界

- 服务端校验已有签名cookie，计算owner；不接受客户端自报owner、任意提示词、模型或工具配置。
- GET列表和详情只返回id、revision、阶段、计数及原因，不返回私有消息、模型正文、工具参数、原始证据或执行绑定。
- POST恢复、DELETE删除都要求当前revision；执行和删除共用同一进程文件锁。活跃操作冲突返回409，不尝试强制覆盖。
- 同一owner最多100条循环记录，列表覆盖这100条；需删除旧记录后才能继续创建。每个HTTP进程最多同时执行4个循环，同一owner在单进程内只能运行一个；尚无跨进程owner全局并发限额。
- 删除使用SQLite secure_delete，保留空的锁文件inode以避免并发锁分裂。它不是备份/存储介质上的历史数据擦除承诺。
- 清除聊天会轮换会话cookie，页面立即移除旧列表；旧owner的循环记录不会被自动删除，新会话不能读取它们。需要保留访问时先管理记录，不要把清除聊天视为删除全部业务数据。
- 没有自动保留期清理、跨设备登录或管理员接管恢复，仍是单机合成验收入口。

页面首次加载会等待已有会话初始化完成，不再提前查询导致401后一直隐藏。会话轮换会使旧列表请求失效，避免异步响应填回旧会话数据。

## HTTP契约

| 方法与路径 | 行为 |
|---|---|
| GET `/api/research/loops` | 当前会话的脱敏列表 |
| POST `/api/research/loops` | 创建合成循环；可传`pause_after_observation` |
| GET `/api/research/loops/{id}` | 当前版本与摘要 |
| POST `/api/research/loops/{id}/resume` | 传整数revision，恢复或核对不确定状态 |
| DELETE `/api/research/loops/{id}` | JSON体传revision，锁定后删除 |

写入接口拒绝跨站Origin/Sec-Fetch-Site；默认关闭时返回404。重复提交创建会生成不同run，前端防止重复点击，但没有请求级幂等键，不能据此声称支付/发信类副作用可安全重试。

## 备份覆盖

核心备份CLI新增可选参数：

```bash
python -m scripts.backup_runtime backup \
  --memory /实际路径/会话.sqlite3 \
  --projects /实际路径/项目.sqlite3 \
  --jobs /实际路径/队列.sqlite3 \
  --loops /实际路径/research_loop.sqlite3 \
  --cookie-name 实际会话cookie名称 --workers-stopped --out /新备份目录
```

PostgreSQL队列可继续用`--dsn-env`替换`--jobs`，loop journal仍是额外SQLite组件。包含journal时manifest增加`loops.sqlite3`、SHA与行数；未传`--loops`时明确列为未备份。旧格式的无journal备份仍可读取。

恢复到新目录后设置`RESEARCH_LOOP_DB=/恢复目录/loops.sqlite3`，同时恢复原会话库和cookie名称。锁目录由首次操作重新创建，不需要备份。执行绑定和到期时间保持原值：版本改变、已到期或pending调用不会被恢复工具绕过。新循环仍需启动前重新选择实际资料/模型契约，本轮不自动启用外部模型。

验收已将暂停循环、会话库、项目库和SQLite队列一起备份到新目录，原cookie通过HTTP读取并续跑恢复后的循环，原目录仍保持暂停状态。本轮没有重新做PostgreSQL全库演练，原PG验收见[核心备份](RUNTIME_BACKUP_MILESTONE.md)。

## 验收与限制

43项回归通过：生命周期API5、持久化循环10、核心备份8、原循环12、部署8。包含会话隔离、401/403/404、输入契约、过期revision、执行锁、删除后不可恢复、pending不调用模型、恢复后的签名和状态连续。

真实浏览器验证暂停→刷新→继续→删除、另一有效会话不能读取记录，以及清除会话后旧列表消失；无JavaScript错误。首个完成的循环为4次合成模型、3次工具；会话轮换检查另创建了一个暂停循环。外部模型调用0次。

首次页面测试暴露cookie初始化时序问题，已修复；第二次测试的另一浏览器未初始化cookie，实际返回401，修正测试为有效的其他会话后验证404。两类失败均未作为通过数字计入。摘要见`audits/research_loop_lifecycle_v1/`。

主要实现：`research_loop_api.py`、`research_loop_store.py`、`research_loop.js`及既有durable/backup模块。未新增框架或数据库。

## 下一阶段

整理可复现的求职演示与工程故障证据：一条真实本地两药报告、一条持久化合成工具循环、一次恢复和权限隔离演练，分别标明真实模型/合成模型、真实资料/合成资料及未完成能力。先完成技术说明与项目叙述，不继续堆叠框架名词。真实模型循环恢复上线仍需提供方幂等/状态查询策略与单独的在线验收范围。
