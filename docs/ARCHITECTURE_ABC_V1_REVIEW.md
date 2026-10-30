# LangGraph 1.x后的A/B/C选型续审

> 历史阶段记录（2026-10-29标记）：A/B/C比较的阶段设置，后续已有V2结果与A方案选择；本页准备项和候选顺序不是当前待办。架构结论见[架构决策](ARCHITECTURE_DECISION_REVIEW.md)。 当前进度与后续任务统一见[大纲](TCM_PROJECT_ROADMAP.md)。

2026-10-28。补齐Deep Agents在两药复合任务上的真实运行时适配。未修改路线图或生产入口，未新增外部模型调用。

后续执行记录：已获得授权并完成首轮9次真实请求，A完成，B/C因模型批量tool_calls与单动作协议不兼容而停止，见[真实结果及诊断](ARCHITECTURE_ABC_LIVE_V1_RESULT.md)。下文“尚未授权”部分保留为原执行前计划；V2及当前状态见下一段。

V2后续：又经明确授权执行19次请求，B/C完成，A仍因批量响应被单动作协议拦截。现已完成受控批次的本地适配，见[V2结果与当前结论](ARCHITECTURE_ABC_LIVE_V2_RESULT.md)。V3仅本地准备，未新增外部调用。

## 当前判断

保留A作为默认接入基线；B/C继续保留候选资格。当前没有证据支持整体迁移到B，也没有证据证明C在所有任务上更好。本轮填补的是“B能否执行相同业务及满足相同约束”，不是把离线替身结果包装成真实规划质量比较。

| 项目 | A：单循环协议 | B：Deep Agents原生图 | C：主管＋子流程 |
| --- | --- | --- | --- |
| 本轮执行器 | 隔离LangGraph工具循环 | 实际create_deep_agent及原生工具节点 | 隔离主管图及两个独立消息上下文 |
| 全部共同靶点任务 | 23项、双方48条见证核验通过 | 相同 | 相同 |
| 同任务替身模型步骤 | 7 | 7 | 11 |
| 底层资料查询 | 6 | 6 | 6 |
| 缺字段/来源不可用 | partial | partial | partial |
| 成熟运行时新增收益 | 本题未作额外收益判断 | 本题没有新增任务覆盖 | 隔离子上下文，已有历史单题token减少观察 |
| 主要待验证项 | 通用聊天业务接入、复杂要求提取 | 自主规划、依赖成本、恢复策略适配 | 长任务收益、委派选择、额外时延 |

上表是固定SDK动作，在同一Python3.12、LangGraph1.2.18、Core1.7.0、OpenAI适配器1.7.3环境中执行。C委派仍顺序执行，未测并行速度。本轮没有把它们并入生产模型循环。

## 依赖核验的新发现

升级主项目消除了旧Core0.3.x与Deep Agents的版本冲突，但B仍不是直接装进主环境即可使用：Deep Agents0.7.32默认依赖Google模型适配器，后者传递依赖要求Pydantic至少2.12.5、Tenacity低于9.2。

本轮第一次按主依赖安装失败；随后将A/B/C统一放入独立环境，固定Pydantic2.14.1、Tenacity9.1.4，其余核心版本与主项目对齐。主项目仍保留Pydantic2.10.6、Tenacity9.2.1，未受实验污染。没有使用`--no-deps`绕过冲突，隔离环境`pip check`通过。

环境由[requirements](../requirements-architecture-candidates.txt)与[锁定约束](../constraints-architecture-candidates.txt)复现。比较时三个候选必须使用该同一环境，不能拿主环境A与隔离环境B直接做严格性能比较。

## B的实际适配范围

[B适配器](../experiments/architecture_candidates/deepagents_pair.py)调用真实Deep Agents创建函数；复用A/C的宿主来源、分页、引用账本及最终报告结构。

Deep Agents默认向绑定层提供11个工具，其中包含文件操作和task。本轮通过应用层模型网关只暴露read_source、shared_targets、finish三个业务工具，并拒绝非业务工具调用。没有执行文件、shell或通用子Agent工具。

还需应用层提供：单动作/参数校验、共同的模型及工具预算、证据结束条件、失败后停止策略。finish使用原生工具的return_direct结束，正常任务无额外模型结束请求。不能说成熟框架自动提供了本项目的资料一致性与业务完整性保证。

默认运行时工具和中间件仍存在于B原生图内；本轮针对该业务范围加网关，不是宣称改造了Deep Agents源码或所有内建能力均已通过权限审计。没有启用本轮checkpoint，本轮B结果不证明未知dispatch恢复已满足项目策略；之前独立恢复实验的限制仍需单独处理。

初步调试还发现，无checkpointer时设置durability没有意义且在该实验栈出现退出错误，已撤去该参数。本轮没有持久化恢复对照，不因此比较框架可靠性高低。

## 验收证据

4个固定开发变体×3个候选，共12次执行；6次正常任务完成，6次故障注入按预期partial，独立验收器未发现错误complete。并非12道独立真实用户问题。

- 正常变体：共同靶点首页、全部共同靶点。
- 故障变体：一侧注意字段缺失、一侧结构化来源不可用。
- B新增8项测试覆盖原生工具结束、提前结束、默认文件工具越权、药材范围、参数校验不重试、模型/工具预算及来源失败。
- 新A/B/C真实SDK＋MockTransport3项测试，加上述8项及已有16项回归，共27项通过。模拟HTTP分别7/7/11次，共25次；usage是模拟值，不是实际模型用量。

离线输入体积使用LangChain消息与已暴露Schema的UTF-8序列化计数，不是token：A累计123242字节、B125043字节、C94238字节。B的默认提示及上下文策略也参与消息构建，不能把差异归结于工具名字数量；不按这些数字推算账单。

[业务轨迹与实现哈希](../audits/architecture_abc_v1/summary.json) · [环境与准备记录](../audits/architecture_abc_v1/preparation.json)

旧A/C真实模型结果（7/11请求，42689/36550 token）保留在[原记录](PAIR_LIVE_SYNTHETIC_RESULT.md)。其LangGraph和SDK基线旧于当前，不能直接与新增B做严格配对成本比较。

## 已准备、尚未授权的新真实模型对照

[执行脚本](../scripts/eval_architecture_abc_live_synthetic.py)默认只预览；新版同环境下，同一固定合成问题分别运行A、B、C一次。DeepSeek deepseek-flash；每候选含子流程最多12次HTTP请求，总计36次，每次输出650 token、正文最多96KiB；每候选180秒、单次30秒，不重试。

仅发送固定合成问题、公共协议说明、框架默认提示、已暴露业务Schema及本次合成观察。不发送真实药典、ETCM、PDF、用户历史或提示中的密钥正文。密钥只用于请求鉴权。提供方错误停止后续执行，不能用剩余预算自动追加重测。

保存逐候选覆盖、实际请求、provider usage、时间与失败；费用未知记null。只有一题、固定A/B/C顺序，不能据此计算泛化成功率或统计显著性。此轮用于排除协议/兼容性障碍及观察成本，再决定是否值得为某个候选投入更复杂独立任务。

准备完成后单独申请新增调用授权；原24次A/C授权已执行18次，未使用上限不延用。预览范围见[固定预览](../audits/architecture_abc_v1/live_preview.json)。

## 复现

```bash
# 独立环境安装，生产.venv不安装候选包
python -m pip install -r requirements-architecture-candidates.txt
python -m scripts.check_architecture_abc --out var/architecture_abc_new
python -m unittest \
  experiments.architecture_candidates.test_deepagents_pair \
  experiments.architecture_candidates.test_abc_live_preparation \
  tests.test_pair_protocol tests.test_pair_live_synthetic -q

# 仅预览，不联网
python -m scripts.eval_architecture_abc_live_synthetic
```

原16题架构开发集尚未执行，不更新其状态。大纲保持原样；阶段建议和候选的未验证项都保留，不把某个框架直接判为落选。
