# 问句契约与资料路由改进

2026-10-29，P0-4b。本轮交付对象/字段/来源的统一校验和实际聊天回归；沿用八业务工具、既有关系查询和A执行核心，不增加框架。原首轮8/18保留，新的结果是已知题修复回归，不能当作独立准确率。

## 改了什么

`/chat → 数据库能力识别/问句跨度 → 字段与分析任务契约 → 完整要求校验 → 本地来源绑定 → 原队列/A循环 → 证据/页面`。

- `tcm/question_contract.py`保存分段的原文位置；英文名称中的逗号、版本小数点、括号和双引号内标点不拆任务。疾病来源名保留原样，来源ID仍由宿主校验。
- 字段契约按对象、资料类型和字段编译。ETCM专属属性不再因包含药典关键词而错库；同一句两个对象的字段分别生成动作。按每句完整字段任务集合校验，漏一个对象或重复动作都不算覆盖。
- 规则和问句模型提案复用`AnalysisPlan/BusinessCall`及上述校验。**单对象字段提案**不再要求匹配本地固定语序；必须有原句对象、明确来源和完整字段，去掉这些槽位后只能剩受限表达词。未知字段、额外任务、来源/对象/数量篡改继续停止。
- “看一下”等前缀与药名分开；相似度、前几项是参数；共同靶点附原始记录位置直接复用关系查询。两药上下文、药典字段和共同靶点可编译成原报告动作；疾病ID的全部原始行用已有分页任务执行。
- 数据概览与“药典的数据能查到哪些字段”作为数据库能力问题处理，不要求药材名称。保留原药典连续追问与出处回看入口。

这仍是**有边界的契约编译器**。陌生语义、复杂指代、未支持字段可能要求澄清；两药报告提案仍需通过受限两药/字段语法校验，没有宣称自由科研规划已实现。生产聊天仍按已校验声明计划执行，真实资料不回传模型自主决策。

## 真实资料回归

先校验原冻结题库、参考资料、源码归档，再复制原题/参考的原始字节并冻结新源码。`refreeze`核对`Judge/assess/summarize`的评分AST未变；输出目录必须不存在，不覆盖原批次。

| 同一批已知题 | 首轮 | 本轮 |
|---|---:|---:|
| 资料任务完整通过，沿用固定评分器 | 8/18 | **17/18** |
| 边界拦截/澄清 | 5 | 5 |
| PubMed关闭 | 1 | 1 |
| 外部模型调用 | 0 | 0 |

BC03现在返回白术药用部位和科名两项及原始出处。原评分器只识别“科”，属性worker使用“科名”，因此仍判`requirements_not_met`。单独核对两项原始值、文件SHA和JSON指针与原参考一致；见[标签差异复核](../audits/question_contract_v1/field_label_reconciliation.json)。**没有改评分器、改答案标签或把该题加进17的分子；也没有人工签核或药学正确性结论。**

详见[逐题回归](../audits/question_contract_v1/replay.json)、[源码与原题一致性](../audits/question_contract_v1/source_manifest.json)。完整来源/回答只保存在私有`var/question_contract_v1/`。

## 工程验收与兼容性

新增12项问句契约/聊天测试和2项评测重放保护测试，涵盖模型替身的非固定语序、当前问句唯一输入、字段漏项、对象遗漏、未知字段和来源约束。合成资料缺字段时保留`partial`，不会为了完成率补值。

[浏览器验收](../audits/question_contract_v1/browser.json)覆盖多字段ETCM、上下文两药、同句跨资料三条问法：表格、默认折叠、展开出处、证据下载、刷新同run，JavaScript错误0；没有字体版式审核。[四种强杀恢复](../audits/question_contract_v1/recovery.json)保留同job，不重读已保存步骤；内部派发不确定时停为partial。全量回归结果见[本轮审计](../audits/question_contract_v1/README.md)。

不迁移来源数据库或队列表。新的代码绑定会使旧版本运行/待选来源的校验失效，不能强行恢复旧checkpoint；保留旧状态和旧源码以便回看。需要回退时恢复整套同版本代码并重启预览，不手改journal/签名。Git工作区还包含此前未提交改动，不能用全目录重置来回滚本轮。

## 使用与下一大步

用户本地服务仍是`http://127.0.0.1:7896/`，状态目录`var/candidate_a_chat_preview`。本轮已重启加载新代码；默认模型关闭。可直接问：

```text
查看白术的药用部位和科名
哪些药材与川芎的靶点最接近？按Jaccard列前5项
核对黄芪和白术的共同靶点，给出双方原始记录位置
我正在整理白术与黄芪的资料，请分别对照药典中的功效、用量，然后给出两者共同的靶点和原始出处
```

复现修复回归（先独立启动无模型7906预览；下面两个输出目录应不存在）：

```bash
.venv/bin/python -m scripts.preview_local_tasks --port 7906 \
  --state var/question_contract_repro/runtime \
  --diseases var/disease_catalog_v1/build/diseases.sqlite3
.venv/bin/python -m scripts.eval_business_closure refreeze \
  --baseline var/business_closure_v1/frozen --out var/question_contract_repro/frozen
.venv/bin/python -m scripts.eval_business_closure run \
  --frozen var/question_contract_repro/frozen --url http://127.0.0.1:7906 \
  --out var/question_contract_repro/replay
```

下一大步是P0-4c：取得未参与开发者的新题与预期，先冻结，再比较规则与一次问句模型提案的完整率、漏项、引用与代价。独立题实际0、目标40，可先10–20。新增真实模型批次需要明确范围和授权；本轮替身测试不代替真实模型效果。随后完成镜像交付与演示。检索/记忆收益、OCR抽查、实体对齐和新聊天多机能力仍待验证。
