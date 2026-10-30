# 新聊天计划：后台执行、进度、取消与恢复

2026-10-29。P0-3将已校验的多步骤聊天交给既有`ResearchWorker`，复用SQLite队列、SSE、租约和同一A循环journal。未增加框架或外部模型请求。验收与开发失败保留在[本轮审计](../audits/research_plan_lifecycle_v1/README.md)，当前排期见[统一大纲](TCM_PROJECT_ROADMAP.md)。

## 使用与调用链

按README启动7896本地预览后，发送：

> 比较当归与川芎的药典功效和用量，核对全部共同靶点，保留双方原始记录的位置

聊天先返回“研究任务已排队”，随后显示进度和最终表格。关页或进度连接断开不会取消任务；同一浏览器刷新可回到同一个任务。点击“取消任务”才请求停止。worker退出或租约到期后显示中断，可点击“恢复已保存步骤”；无法确认上次工具是否执行时会保留部分结果并停止，不把重新查询称为恢复。

`/chat → 原规则/单次问句提案 → 分句/对象/字段校验 → 来源选择 → 固定RunScope和代码/来源版本 → 既有JobStore.enqueue → ResearchWorker领取 → 私有RunJournal → 原A循环与只读子进程 → queue保存结果 → SSE通知 → 聊天表格/证据回读`。

歧义来源仍在入队前选择，原问题保留。疾病名称检索后才发现的待选项，随任务结果保存，继续选择时核对该任务的`next_focus`。会话只保存计划、版本和任务引用，不保存完整证据；最终结果仍在任务库，外置证据仍在私有证据库。

## 状态、预算与恢复边界

| 场景 | 实际行为 |
|---|---|
| 重复提交相同`request_key` | 同owner、同问题和固定计划返回同一任务；同key换内容返回冲突 |
| 关页、SSE断网 | worker继续；按`Last-Event-ID`重放已保存事件，终态游标仍收到done，不重新查资料 |
| 取消排队任务 | 直接取消，不派发工具 |
| 取消执行中任务 | 心跳监视取消执行协程并清理子进程；终态提交再检查取消，取消优先于并发完成 |
| 已领取但尚无journal时退出 | 租约回收为interrupted；明确恢复后可开始，前提是代码/来源契约仍一致 |
| 结果或下一动作已保存 | 复用原journal、证据、动作与预算，不重复已保存读取 |
| 工具派发pending时退出 | 恢复为partial/uncertain_tool_dispatch，不自动重发；复合报告内部仍不逐个恢复读取 |
| 代码、框架、资料或任务版本改变 | 拒绝旧恢复/回看成功；不改签旧journal继续执行 |
| 双worker争抢、旧租约失效 | 队列事务只允许一个领取者；journal文件锁互斥；每次journal保存前检查租约/取消，失效worker不能继续派发 |

同owner最多1个排队/执行任务，队列最多20个，最多4个running。默认租约30秒；正常运行由心跳续租。强杀后可能先显示执行中，租约到期才转中断；不无条件重试。恢复需当前任务revision，取消/已完成任务不能转为恢复队列。

沿用A循环最多16次工具读取和8个声明驱动步骤；它们不是8次真实模型请求。全量两药动作仍先预留最多14次读取，实际按内部派发计量。原总时间预算在journal创建时固定，停机时间也计入，恢复不会重置预算；超时保留partial。当前默认声明式执行，不向外部模型发送工具观察。

## 配置、接口与文件

- 既有`ENABLE_RESEARCH_TASKS=1`加`ENABLE_RESEARCH_PLAN_JOBS=1`启用后台聊天。预览脚本已开启；不设置新开关时保留原请求内执行，便于部署兼容。关闭开关不等于旧队列任务已被排空。
- `RESEARCH_JOB_DB`优先指定队列；否则取`RESEARCH_RUN_DB`或会话库所在目录下的`research_jobs.sqlite3`。原运行历史库继续保留，后台任务不额外复制一份结果。
- 新聊天journal固定在运行历史库同目录的`research_plan_journal.sqlite3`，与合成循环journal分开；证据仍在`evidence.sqlite3`。这些都是运行状态，不能作为缓存删除。
- `POST /api/research/jobs/plan`接受本地可识别的`question`与`request_key`，不调用模型、不接受客户端scope/路径/来源ID。口语提案和歧义选择继续走聊天。
- 复用`GET /api/research/jobs/{id}`、`/events`、`POST /cancel`及`POST /resume`。新聊天恢复正文为`{"revision": 当前整数版本}`。结果的按需读取及完整下载复用`/api/research/runs/{id}/evidence/...`，服务端核对实际任务owner。
- 所有写接口保留同站检查与签名Cookie鉴权；其他已初始化会话访问返回404，未初始化返回401。旧独立报告、合成演示和新聊天共享队列设施，但保留各自执行契约。

本轮闭环验收使用单机SQLite队列和本地文件锁。既有可选PostgreSQL队列/独立报告checkpoint保留；未新增该聊天链路的PostgreSQL、多机或负载验收，不能据此宣布分布式恢复已完成。

## 备份与回滚

停机并处理queued/running任务后，原备份命令增加`--plans /实际目录/research_plan_journal.sqlite3`和`--evidence /实际目录/evidence.sqlite3`，保留原memory/projects/jobs参数。SQLite队列中的已发布外置引用也接受完整性检查；缺证据失败。备份中有interrupted新聊天任务却遗漏plans时直接失败。

备份归档名称为`plans.sqlite3`；恢复部署须核对原配置与文件名。数据快照恢复不等于跨路径续跑，journal仍受原执行路径、代码、来源和到期时间绑定。新聊天的PostgreSQL备份组合未独立验收。

删除终态任务时同时清理该owner/job的journal与外置证据；仍持有执行锁或证据活动锁时拒绝删除。多个SQLite文件之间没有联合原子事务；删除或发布中途故障可能留下未引用状态，不自动据此猜测任务完成。保留原备份与人工排查入口。

回滚前先停止新提交与worker，保存任务、journal和证据，再部署对应代码。新代码指纹会阻止旧journal强行续跑；关闭后台开关可让新问句回到请求内执行，不能让旧任务自动迁移或重跑。没有来源库Schema迁移、依赖升级、Git提交或Docker发布。

## 复现和验收范围

```bash
.venv/bin/python -m scripts.run_offline_ci --out var/plan-lifecycle-ci
.venv/bin/python -m scripts.check_research_plan_job_recovery --out var/plan-lifecycle-recovery
.venv/bin/python -m uvicorn tests.research_plan_job_http_fixture:app --host 127.0.0.1 --port 7904
```

浏览器在另一个终端使用本机已安装Playwright的Python及Chromium执行：

```bash
/path/to/browser-python -m scripts.check_research_plan_job_browser \
  --url http://127.0.0.1:7904/ --browser /path/to/chromium \
  --out var/plan-lifecycle-browser.json
```

HTTP fixture的延迟、暂停和中断控制接口只存在于测试模块，不被正式应用导入。四种SIGKILL场景只强杀脚本自身的合成worker；租约时间只在其退出后由测试时钟推进，未伪装成真实等待30秒。浏览器验证关页后完成、刷新同任务、SSE终态重放、取消、网页恢复不重读及证据下载。真实模型质量、独立业务集、费用、生产负载与机器断电不在本轮结果范围内。
