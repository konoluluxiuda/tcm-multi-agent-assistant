# M0-06 / EV-01 离线回归验收

日期：2026-10-13。工作流与入口已实现，本地验收通过。2026-10-14 补充：远程 CI 已通过，M0-06 完成。

## 环境与结果

主机新建 `/tmp/tcm-ci-20261013` 虚拟环境，Python 3.13.5，安装 `requirements-ci.txt`，`pip check` 无冲突。GitHub Actions 目标为 Python 3.11，本次没有等价的 Python 3.11 本地环境；本机 Docker 命令报告 WSL 集成不可用，未借此声称容器或远程工作流通过。

本地统一入口 10 个模块、75 项全部通过，零跳过、零测试进程网络尝试；55 个 Python 文件语法解析通过。依赖解析记录见 [resolved_dependencies.txt](resolved_dependencies.txt)，最终逐模块结果见 [report_local.json](report_local.json)。

| 模块 | 通过数 |
|---|---:|
| 会话 | 3 |
| RAG 评测契约 | 6 |
| 离线入口故障检测 | 4 |
| 可选功能与实际图/API | 13 |
| runtime | 5 |
| 来源访问 | 7 |
| 药材 API | 12 |
| 冻结评测器故障/契约 | 7 |
| 药材 repository/import | 14 |
| 上传隔离 | 4 |

这 75 项是工程回归，不是 75 条真实用户问题，也不是业务效果准确率。入口故障测试有意制造跳过、断言失败和被捕获的网络异常，验证子运行返回失败；这类有意失败不混入业务失败率。

## 真实失败与修正

1. 本地受限沙箱下 TestClient 启动阻塞；未修改的最小 FastAPI 示例在 5 秒栈转储时同样停在 AnyIO portal 启动。首次统一运行因模块超时失败，随后清理临时进程。在沙箱外运行时保留 Python socket 阻断与假配置，HTTP 测试正常执行。入口现按模块保存实时日志，每 60 秒输出阻塞栈，120 秒超时明确失败。
2. 干净环境首轮 75 项中 3 项上传测试报 `ModuleNotFoundError: werkzeug`；原医疗大镜像已有该依赖，精简环境暴露了遗漏。保留 [report_initial.json](report_initial.json)。将项目原有的 `Werkzeug==3.1.3` 纳入测试依赖，未削弱上传测试，也未修改生产端点；完整复测通过。

## CI 交付与边界

- [工作流](../../.github/workflows/offline-regression.yml)：push/PR/手动触发，只读权限，安装轻量依赖、pip check、离线回归、保存 artifact；不依赖 GitHub Secrets。
- [入口](../../scripts/run_offline_ci.py)：逐模块进程隔离，清理继承凭据、关闭 dotenv/tracing、socket 阻断；跳过、空套件和预期失败均不允许冒充成功。该补丁不是 OS 网络沙箱，额外子进程不自动继承 socket 补丁。
- 当前全量 2,076 份药材的冻结集验收及真实模型/工具调用在 summary 中明确 `not_run`；现有 30 题冻结文件与人工待签核状态未修改。
- 本轮未执行浏览器、镜像发布、生产容量或中文检索对照。直接依赖固定、传递依赖仍解析；记录本次版本不等于完整依赖锁。
- 2026-10-14 已上传提交 `5f251ed`，GitHub Python 3.11.17 的 10 模块、75 项回归全部通过，pip check 成功，artifact 11836340383 已上传。见 [远程运行](https://github.com/konoluluxiuda/tcm-multi-agent-assistant/actions/runs/38441261550)、[结果证据](report_remote.json)。这补齐了此前待验收项；全快照、真实服务和人工签核状态不变。
