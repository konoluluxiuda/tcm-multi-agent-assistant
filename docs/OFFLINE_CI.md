# 离线回归与 CI

**最新远程验收（2026-11-05）：** 实现提交`954776b`的[GitHub回归](https://github.com/konoluluxiuda/tcm-multi-agent-assistant/actions/runs/42012672920)已完整通过215模块1862项，0跳过、0网络尝试；[下载报告](../audits/conversation_scope_v1/closure_ci_remote.json)与[版本交付](../audits/conversation_scope_v1/release_closure.md)可复查。下面本地首轮和局部复测描述保留当时状态。

此前本地验证（2026-11-05）：使用干净Python 3.11.17环境覆盖215模块1862项、661份Python语法检查；全量首轮仅两个旧记忆断言模块失败，按新需求更新后14项针对性复测通过，无剩余失败、跳过或网络尝试。见[首轮、复测与验证范围](../audits/conversation_scope_v1/README.md#ci依赖修复与全仓离线验证2026-11-05)。该次仅为本地全量首轮加局部复测，当时尚未提交/push；随后远程完整通过的结果见上方。运行规则以当前脚本和工作流为准，末尾保留首次远程验收记录。

工程回归与业务效果分开：历史24题作者筛查中18道资料任务仅8道完整完成，见[首轮结果](BUSINESS_CLOSURE_REVIEW.md)；最新业务范围与各轮成绩见[统一大纲](TCM_PROJECT_ROADMAP.md)。评分器测试通过只说明口径可执行。

## 运行方式

在独立虚拟环境中安装轻量依赖，再运行与 GitHub Actions 相同的入口：

```bash
python -m venv /tmp/tcm-ci
/tmp/tcm-ci/bin/python -m pip install -r requirements-ci.txt
/tmp/tcm-ci/bin/python -m pip check
/tmp/tcm-ci/bin/python -m scripts.run_offline_ci
```

报告默认保存到忽略的 `var/ci/`，可用 `--out /tmp/tcm-ci-report` 指定位置。`summary.json` 汇总各模块状态、测试数、跳过数和未运行范围；每个模块附有 JSON 与日志。退出码 0 表示本轮离线范围全部通过，1 表示测试失败、跳过、预期失败、空套件或检测到网络尝试。安装/语法异常也以非零退出阻断运行。

## 测试范围与隔离

- 自动发现 `tests/test_*.py`，逐模块启动独立 Python 进程，避免共享配置、模块缓存和导入阻断器互相影响；每模块限时 120 秒。
- 校验仍存在的Git跟踪Python文件，以及`agents/`、`tcm/`、`scripts/`、`tests/`、`experiments/`内新增Python文件的语法；已删除文件不作为待解析输入。不导入CV/OCR/RAG权重模块。
- 使用临时 JSON、SQLite、PDF/PNG 内容和确定性模型/工具替身；覆盖药材数据/API、评测器故障检测、会话、可选功能、上传隔离、来源接口及离线入口自身。
- 测试子进程不继承服务密钥或 tracing 配置，模型地址固定为本机无服务端口，禁用 `.env` 加载。测试进程阻断 socket 连接、发送与 DNS 解析；即使应用捕获网络异常，也会将尝试计入失败。此机制是回归防误调用措施，不是操作系统级网络沙箱；新建子进程不自动继承 Python 的 socket 补丁。
- 缺少 LangGraph 等依赖而跳过测试会失败，不能显示“绿色但没测到”。现有测试文件仍可单独运行；严格策略由统一 CI 入口执行。

安装依赖需要访问包源；“离线”指测试阶段不调用外部模型、文献或搜索服务。`requirements-ci.txt` 固定直接依赖并沿用现有版本，传递依赖仍由 pip 解析；这不是全量依赖锁，M0-01 的完整锁定工作仍需继续。

CI依赖还包含NumPy与Qdrant客户端，用于合成向量的本地SDK一致性测试，以及`psycopg[binary]`，用于备份连接参数解析。无需启动Qdrant或PostgreSQL服务，也不下载嵌入模型。新增测试若直接使用可选包，应同步加入CI依赖，并在未继承系统包的Python 3.11虚拟环境中检查，避免本机已有依赖掩盖遗漏。

## 工作流

[offline-regression.yml](../.github/workflows/offline-regression.yml) 在 main 的 push、PR 和手动触发时运行，使用 Python 3.11、只读仓库权限，安装依赖后执行 `pip check` 与回归入口。无仓库密钥要求；报告/日志通过 Actions artifact 保存。原 Docker 构建工作流继续独立运行，不把镜像构建成功当测试通过。

本地成功不等于远程 CI 已通过；工作流须提交并推送后，在 GitHub Actions 查看实际运行结果。首次成功后才能记录对应提交的远程 CI 链接。此变更不自动配置仓库分支保护规则。

## 必须另行验收的范围

全量 ETCM 原始资料与 SQLite 不上传到 CI。冻结 30 题全快照验收在本地运行：

```bash
python -m scripts.eval_tcm_golden \
  --source /home/zry/workspace/HDCTI/dataset/ETCM2.0
```

详情见 [冻结集运行说明](../eval/README.md)。CI 只测试评测器在临时快照上的正确性，不宣称已重新验证全量 2,076 份记录。`summary.json` 将全快照和真实服务评测明确标为 `not_run`，不计入离线通过数。

真实模型效果、付费工具、浏览器交互、Docker 无源码挂载验收及容量测试仍单独执行。当前没有自动的付费评测任务；今后新增须独立入口和明确的调用预算，不混入默认回归。

## 首次远程验收

2026-10-14：提交 `5f251ed` 在 GitHub Python 3.11.17 上 75/75 通过，报告 artifact 已上传。见 [运行记录](https://github.com/konoluluxiuda/tcm-multi-agent-assistant/actions/runs/38441261550) 与 [验收证据](../audits/offline_ci/report_remote.json)。M0-06 已完成；全量私有快照与真实服务仍不在默认 CI 范围。
