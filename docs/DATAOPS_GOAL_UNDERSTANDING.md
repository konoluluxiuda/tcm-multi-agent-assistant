# 单次模型目标提案：开发与实验说明

状态：2026-10-30，G V1适配器和对照已完成；12道合成开发题R/G各8/12。G补回3道自然表达，也错了3道规则能完成的规范题，**尚未接入聊天/工作台，不是生产功能**。详见[完整结果](../audits/dataops_goal_v1/README.md)。

## 实际调用链

```text
相同问句＋有界目录＋字段契约＋父版本字段摘要
 ├─ R：goal_proposal.propose（0模型）
 └─ G：goal_model.request_proposal（最多1次原生tool call）
      → GoalDraft严格类型 → 本地名称/父记录/范围校验 → Goal或needs_input
 → 相同固定F图和工具 → 来源参考＋XLSX回读验证
```

模型提案只有字段和选择条件，不含文件路径、SQL、药学值、来源候选选择或身份批准。追加/替换字段时父版本对象由宿主绑定；严格策略不能降级，ETCM旧列不能删除。schema只限制结构和范围，无法机械证明没有遗漏语义；G V1的字段错误已证明这一点。

## 使用与复现

```bash
# 只输出协议，不访问提供方
.venv/bin/python -m scripts.eval_dataops_goals

# 只运行规则分支；G明确记为not_run
.venv/bin/python -m scripts.eval_dataops_goals \
  --local-only --out var/dataops_goal_local_new

# 显式执行一轮新的合成对照：最多12次，每题一次，无重试
# 使用现有CHAT_API_KEY，只向固定DeepSeek端点发送协议中的合成资料
.venv/bin/python -m scripts.eval_dataops_goals \
  --execute-synthetic --out var/dataops_goal_live_new
```

每次使用新目录。原始实测位于`var/dataops_goal_live_v1_20261030`，不可覆盖、重放或续跑以改变分数。成功执行脚本不等于模型通过全部业务契约，查看`summary.json`逐题结果；模型有失败/未运行时真实执行命令返回非零。

适配器为`request_proposal(chat_model, question, catalog, parent=None, authorized=False)`。调用方必须显式授权并配置`max_retries=0`、输出不超过1200 token；实验脚本还在HTTP传输层约束接收端、每题一次与整体12次预算。仅设置`authorized=True`不是通用网络预算器，其他接入不得绕过派发记录和调用上限。

目录最多128个别名，输入上下文最多16KB，选择最多50条。大目录目前拒绝，不静默丢弃记录；尚未实现真实全库候选召回。父版本摘要不发送旧字段值、证据正文或完整会话。

## 观测与失败

`dataops.goal_proposal`只记录一次实际模型适配器调用；`dataops.goal_evaluation`关联case/strategy/experiment、实际transport派发数及开发契约分数。跟踪仍需显式配置本地Langfuse，不配置不会导出。G V1已回读24条评测轨迹、12次generation和24个分数。

超时/断连未知派发、HTTP错误停止后续模型题；格式或本地契约拒绝单题失败，不自动改写提示再调用。失败不转成“正确澄清”。目录、父对象或字段越界在构建Goal前拒绝；来源值、出处及文件完整性仍由原工具和验证器负责。

当前网页继续`local_bounded_grammar_v1`。主线为[数据/benchmark→检索→RAG→任务整合](DATA_RAG_BENCHMARK_PLAN.md)，R1研究数据/参考已交付，下一项R2。G2出处/来源、默认列/指定列语义及规则优先接入暂缓并入R4，不阻塞检索/RAG；原G V1及事后回放11/12保留，不能作为上线依据。
