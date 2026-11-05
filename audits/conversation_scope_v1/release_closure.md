# 五类预设问题：版本与部署交付

日期：2026-11-05。用户确认继续完成版本提交、远程CI和部署对齐。本次沿用五类资料问题，不扩展通用Agent或增加模型实验。

## 实现与部署

- 实现提交：`954776b10019cee1f43598ebe0c695c70ff71a59`，已推送至`origin/main`。包含连续对话、可选结果反馈、CI依赖及旧测试契约修复。
- 本地镜像：`tcm-research-chat:closure-954776b`，从干净提交构建，入口为 http://127.0.0.1:7934/ 。容器健康，227个运行文件逐文件哈希与构建输入一致。
- 原数据挂载与配置保留；显式添加`ENABLE_DATAOPS_RESULT_FEEDBACK=0`。需求模型保持关闭，默认仍为`local_then_one_model_v1`。
- 原28条会话、28条交付记账及设置内容在重建后保留。停止期间的状态备份位于`var/release_closure_20261105/state-backup/`，旧容器`tcm-requirements-clean-completion-v2-before-closure-954776b`保留且停止。
- 执行后反馈代码已随镜像部署，默认关闭；未证明优于本地规则，因此不作为默认能力开启。此前“源码尚未部署”仅描述前一阶段。

见[部署与源码核对](closure_deployment.json)。交付文档提交不改变上述运行源码；实现提交与文档提交分开追溯。

## 验收

- [预设问题](closure_presets.json)：10条业务＋3条边界，13/13通过；逐字段核对只读来源、分页原始行及两份实际下载XLSX。
- [四轮连续对话](closure_conversation.json)：按用户原句完成澄清→性味查询→澄清→指代追问，4/4通过；先在7942隔离实例验收，再验证7934。临时实例已停止。
- 本轮模型调用0；未修改题目、资料或参考值，不将已见开发例子称为泛化验证。
- 本地离线验证仍保留[全量首轮及局部复测](ci_verification.json)，不覆盖两处旧断言失败。

GitHub实现提交`954776b`的[Offline regression](https://github.com/konoluluxiuda/tcm-multi-agent-assistant/actions/runs/42012672920)已通过：Python 3.11.17，215模块1862项全部通过，0跳过、0网络尝试；下载并核验artifact摘要，见[完整报告](closure_ci_remote.json)。[Docker Image CI](https://github.com/konoluluxiuda/tcm-multi-agent-assistant/actions/runs/42012672852)也已成功。提交、run与artifact的对应关系见[CI回执](closure_ci_runs.json)。这是完整远程运行的通过结果，与先前本地“首轮＋局部复测”分别保存。

## 完成范围

五类预设问题、连续追问、来源可查、文件交付与会话恢复构成本轮验收范围。实现已提交、推送，远程CI已通过，部署源码与实现一致，业务复验已通过，本轮版本交付闭环。后续按真实使用发现的问题维护，不另设泛化评测、模型收益或通用自主任务作为完成门槛。历史模型失败及未证明事项原样保留。

运行私有证据保留在`var/release_closure_20261105/`与`var/preset_questions_v1/closure_954776b/`，未上传会话、原始资料、部署环境文件或状态备份。
