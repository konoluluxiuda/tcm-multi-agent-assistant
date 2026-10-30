# 两药报告的持久化执行与恢复

2026-10-27。本轮把现有PostgreSQL队列和LangGraph checkpoint扩展到两药报告。正常业务仍为双方药典字段、ETCM属性和共同UniProt首页，未新增疗效推断或模型调用。

## 执行与恢复

启用checkpoint时，四个资料查询是独立节点：左药药典、左药ETCM、右药药典、右药ETCM。每侧复用单药报告的组装函数；双方来源核验通过后查询共同靶点，最后组装比较表。把持久化边界放在实际工具步骤，避免只保存整份子报告时重做已完成查询。

进程中断后，用户在原会话历史中点击“继续执行”。已有pending writes的步骤被复用，未完成步骤重新执行。已完成终态可直接读出。来源、请求或代码指纹改变时拒绝混用旧状态。owner/job哈希绑定线程、数据库会话锁、任务租约和revision沿用原实现；取消任务不支持恢复。

这是只读工具的恢复，不是外部副作用的exactly-once：工具已返回但结果尚未持久化时仍可能重跑。超时/不可用作为结果保存并显示部分报告，不会无限重试。共同靶点也只保留当前报告支持的首页，非全量靶点分析。

未启用checkpoint时仍走原两药子图，不要求PostgreSQL。曾经使用checkpoint的任务在关闭后不会静默从头重跑。

## 真实验收

- 39项相关回归通过：新两药持久化5项，原两药7项、单药6项、恢复权限3项、队列6项、任务API4项、MCP契约8项。
- 新测试覆盖资料节点中断、共同靶点阶段中断、终态不再调用、输出一致性、部分结果及worker分流。
- 独立PostgreSQL库强杀worker后跨进程恢复：左药文本1次、左药属性1次、右药文本1次、右药属性2次、共同靶点1次。验证其他owner不能恢复、旧worker不能提交、来源变更在调用工具前拒绝、事件revision连续、终态删除同时清理checkpoint。
- 真实HTTP进程被SIGKILL，等待真实31秒租约过期，保留cookie后从网页历史点“继续执行”；同样得到1/1/1/2/1次调用，JS错误0。
- 原单药强杀恢复再次通过，文本1次、属性2次。
- 真实本地当归/川芎报告页面通过：172个共同UniProt、双方原始出处、JSON下载和刷新回看均验证。该检查验证数据与展示，不证明药材身份或生物学作用。
- 本轮外部模型调用0次。首次开发验证出现LangGraph节点名与状态键冲突，已改为独立查询节点名；失败输出保留本地，最终通过记录见`audits/pair_checkpoint_v1/`。

## 运行

本机已配置独立实际资料库`tcm_pair_runtime`，预览：http://127.0.0.1:7892/ 。合成测试使用`tcm_pair_recovery`，不与实际队列混用。

```bash
export RESEARCH_JOB_DSN='host=/home/zry/workspace/Multi-Agent-Medical-Assistant/var/postgres_checkpoint_v1/socket port=55432 user=zry dbname=tcm_pair_runtime'
export RESEARCH_CHECKPOINT_DSN="$RESEARCH_JOB_DSN"
/tmp/tcm-ci-mail-env/bin/python -m scripts.preview_local_tasks \
  --port 7892 --state var/pair_checkpoint_v1/preview_state \
  --diseases var/disease_catalog_v1/build/diseases.sqlite3
```

打开“本地研究”选择两药比较，输入当归、川芎和“功能与主治”。普通关闭网页不算任务中断，后台会继续执行。不要为了演示强杀承载其他任务的服务。

新环境沿用[PostgreSQL部署说明](POSTGRES_CHECKPOINT_MILESTONE.md)，安装既有依赖并执行显式迁移脚本。本轮无新增依赖或数据库结构迁移。`/tmp`运行环境只用于本机验证，不是镜像发布。

## 回滚与边界

回滚前停止worker并保留原数据库。原SQLite模式不能恢复PostgreSQL中间状态；旧代码也不支持新增两药图。代码整体参与指纹，升级后旧未完成任务可能被拒绝恢复，需要用新任务重新查询，不能承诺滚动升级续跑。模型工具循环、外部写操作、多机容灾、PostgreSQL备份恢复演练尚未完成。

主要文件：`tcm/pair_research_report.py`、`cross_source_report.py`、`report_checkpoint.py`、`research_jobs.py`；测试与验收脚本覆盖图、队列和网页。

## 下一主任务

完成持久化运行数据的备份恢复演练：明确项目记忆、会话身份、研究任务和checkpoint之间的恢复边界，在隔离目录/数据库恢复后核验owner、项目revision、任务结果和出处。保留现有技术栈，不再为技术名词新增无业务需求的框架。模型工具循环的恢复与外部调用幂等列为后续独立任务。
