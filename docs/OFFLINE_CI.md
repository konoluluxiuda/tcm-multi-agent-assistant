# 离线回归与 CI

日期：2026-10-13。对应 M0-06 / EV-01。

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
- 校验 Git 跟踪的 Python 文件及 scripts 下新增文件的语法，不导入 CV/OCR/RAG 权重模块。
- 使用临时 JSON、SQLite、PDF/PNG 内容和确定性模型/工具替身；覆盖药材数据/API、评测器故障检测、会话、可选功能、上传隔离、来源接口及离线入口自身。
- 测试子进程不继承服务密钥或 tracing 配置，模型地址固定为本机无服务端口，禁用 `.env` 加载。测试进程阻断 socket 连接、发送与 DNS 解析；即使应用捕获网络异常，也会将尝试计入失败。此机制是回归防误调用措施，不是操作系统级网络沙箱；新建子进程不自动继承 Python 的 socket 补丁。
- 缺少 LangGraph 等依赖而跳过测试会失败，不能显示“绿色但没测到”。现有测试文件仍可单独运行；严格策略由统一 CI 入口执行。

安装依赖需要访问包源；“离线”指测试阶段不调用外部模型、文献或搜索服务。`requirements-ci.txt` 固定直接依赖并沿用现有版本，传递依赖仍由 pip 解析；这不是全量依赖锁，M0-01 的完整锁定工作仍需继续。

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
