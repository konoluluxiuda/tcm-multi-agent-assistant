# 工具反馈理解回归：执行 Agent 操作手册

> **已完成，现仅作历史操作记录。** live_regression_v1已执行23次，16组合落盘，成绩G4/8、W5/8。之后开始单来源表格与澄清契约修复，当前源码将不匹配本文旧manifest。不要照抄本文execute/resume/score命令；新实验需另建冻结协议。见[真实结果与审核](../audits/requirements_feedback_v1/live_regression_result.md)。

编写日期：2026-11-04。适用于同一台主机、同一工作区的执行交接。目标是执行已冻结的 G/W 小样本回归，保存事实并交回审核，不要求执行 Agent 设计策略或修改代码。

**编写本文时，真实调用尚未授权、尚未开始。用户要求“撰写操作文档”不等于授权调用模型。** 后续若用户明确批准本手册的36次上限，授权在该任务中持续有效，不必每一步重新确认。若只收到不含上下文的“继续”，先核实交接中是否已有这份预算的批准记录。

## 1. 接手时必须知道的状态

| 项目 | 固定值或事实 |
|---|---|
| 工作目录 | `/home/zry/workspace/Multi-Agent-Medical-Assistant` |
| Python | 项目 `.venv/bin/python`，不得更新依赖 |
| 实现基线 | Git `74f2521`；后续纯文档提交可以存在，以冻结校验为准 |
| 运行目录 | `var/requirements_feedback_v1/live_regression_v1`，已经prepare，不再prepare |
| Manifest SHA256 | `64907979d098bad5f3884d06fb738aa4ca89b389eb893f783ce775d542d75884` |
| 数据发布版 | `var/data_releases/r1_names_20261101` |
| 场景 | HV2-01、07、13、20、29、32、35、36；8场景9轮，16个场景×策略组合 |
| 策略 | G=`local_then_one_model_v1`，W=`requirements_feedback_v1` |
| 请求上限 | G共9次，W共27次，合计36次；每轮G最多1次、W最多3次 |
| 模型 | `https://api.deepseek.com` / `deepseek-flash`，thinking disabled，temperature 0 |
| 单次限制 | 输出最多1200 token，请求最多65536 bytes，超时30秒，不自动重试 |
| 已有验证 | 1835项离线测试通过；合成开发样例G3/5、W5/5，不是真实模型成绩 |
| 部署 | 默认策略未改；7934旧镜像不含W，本次无需启动网页或部署容器 |

W流程：提取需求 → 本地机械预检及有界资料状态探测 → 必要时修正一次 → 不看前提案的语义复核 → 原fixed执行。模型收到的是能力、名称、字段及可用状态，不能追加上传原始语料。工具反馈不能保证理解正确。

题目均来自已经看过的live_v2，仅split改为dev，问句与期望未变。报告应称“已见题故障回归”，不能称“独立保留题”“真实用户准确率”或“泛化已达标”。旧live_v2的118次调用、旧预算及中断记录不属于本次运行。

## 2. 工作范围与交回条件

执行 Agent 可以检查文件、校验冻结、读取账本、在授权后运行一次既定命令、整理结果及补充公开验收文档。无需重跑全仓测试或合成样例。不要改实现、题目、参考答案、评分逻辑、模型配置、数据、默认策略或部署。

遇到下列情况，保存已有证据并交回负责判断的 Agent：冻结失效、依赖/资料缺失、模型配置不符、账本已有执行、进程中断、HTTP错误、未知派发、评分程序异常。不要用重新prepare、更换目录、清空账本、修改manifest或增加预算绕过。

业务未通过是合法实验结果，不是自动重试理由。执行 Agent 可以记录观察到的失败码及维度，但“根因已确定”“应该修改评分/提示词”“切换默认策略”由审核者判断。本文不授权创建子Agent、提交Git、push或部署；这些操作按用户另行指示处理。

## 3. 阶段A：零模型调用的接手检查

以下操作无需模型预算。先进入工作目录：

```sh
cd /home/zry/workspace/Multi-Agent-Medical-Assistant
git status --short
git log -1 --oneline
```

不要执行 `git reset`、`git clean` 或切分支。允许本手册及相关导航文档尚未提交；其他修改先报告，冻结涉及的文件必须通过下面校验。无需把HEAD强制恢复到74f2521。

检查冻结、公开协议一致性与首次执行条件：

```sh
.venv/bin/python - <<'PY'
import json
from pathlib import Path
from scripts.eval_requirements_generalization import validate, digest
run = Path('var/requirements_feedback_v1/live_regression_v1')
expected = '64907979d098bad5f3884d06fb738aa4ca89b389eb893f783ce775d542d75884'
assert digest(run / 'manifest.json') == expected, 'Manifest SHA differs'
protocol, scenarios = validate(run)
public = json.loads(Path('audits/requirements_feedback_v1/pending_live_protocol.json').read_text())
assert public['manifest_sha256'] == expected
assert protocol == public['protocol'], 'Public protocol differs'
assert protocol['budget'] == {'G': 9, 'W': 27, 'total': 36, 'turns': 9, 'scenarios': 8}
assert [s.id for s in scenarios] == public['scenario_ids']
assert not (run / 'ledger.sqlite3').exists(), 'Existing ledger: inspect and hand back; do not restart'
assert not (run / 'cases').exists(), 'Existing case artifacts: hand back'
print('PASS: frozen code/runtime/data/protocol; 8 scenarios, 9 turns; execution not started')
PY
```

预期退出0。`validate`会检查源码、依赖、题集和真实数据摘要，耗时可能超过十秒。失败即停止，不修正hash。这里不运行 `score`，因为评分会创建/打开账本并写入新报告。

检查配置是否齐全且符合协议，仅输出通过标志，不输出密钥：

```sh
.venv/bin/python - <<'PY'
import json, os
from pathlib import Path
from dotenv import dotenv_values
p = json.loads(Path('var/requirements_feedback_v1/live_regression_v1/protocol.json').read_text())
c = dotenv_values('.env')
v = {key: os.getenv('RAG_' + key) or c.get('RAG_' + key) or c.get('CHAT_' + key)
     for key in ('MODEL', 'BASE_URL', 'API_KEY')}
assert all(v.values()), 'Missing configured model/base URL/key; do not print values'
assert v['MODEL'] == p['model'], 'Configured model differs'
assert v['BASE_URL'].rstrip('/') in (p['endpoint'], p['endpoint'] + '/v1'), 'Configured endpoint differs'
print('PASS: required configuration present and provider matches; no network call')
PY
```

不要 `cat .env`，不要打印环境变量全集，不做“测试连通性”模型调用。缺少配置时仅报告缺项类别，不创建或更改密钥。

## 4. 阶段B：确认授权，再执行一次

授权记录应能明确对应：**上述manifest、8场景9轮、G/W合计最多36次真实模型请求**。可接受用户在看过该方案后明确说“同意执行这36次以内回归”。尚无记录时只汇报阶段A结果并等待；不要把命令行的hash参数误认为用户授权。

授权存在且阶段A通过后，在同一工作区执行下面命令一次。日志写在忽略目录，避免终端展示私有问句或返回内容。`NO_CLOBBER`防止覆盖旧日志。

```sh
(
  umask 077
  set -o noclobber
  .venv/bin/python -m scripts.eval_requirements_generalization execute \
    --out var/requirements_feedback_v1/live_regression_v1 \
    --authorize-execute 64907979d098bad5f3884d06fb738aa4ca89b389eb893f783ce775d542d75884 \
    > var/requirements_feedback_v1/live_regression_v1-execute.private.log 2>&1
)
```

必须记录工具返回的进程会话ID和最终退出码。命令运行超过一次工具等待时，继续等待**同一个进程**，不要重新执行命令；有会话轮询工具时每次等待不超过60秒，并给用户简短进度。

执行器串行运行真实ASGI/本地工具和文件交付，无需手动访问7934。不要给execute附加 `--policies` 或 `--suite` 企图更换冻结输入；策略与题目从运行目录读取。

退出码解释：

| 观察 | 含义与下一步 |
|---|---|
| 0且有评分报告 | 所有组合评分通过，继续整理事实 |
| 1且有评分报告 | 至少一个组合未通过或未运行；先查看账本及评分，不能直接称为脚本崩溃 |
| 非0且无评分报告 | 查看私有日志的异常类型，保留文件并交回；不要自动重跑 |
| provider_stopped=true或未知派发 | 停止后续模型调用，整理部分结果，交回 |

即使全HTTP200，也可能只是理解完成而业务失败。预算是上限，不需要把36次用满。本地命中、边界拦截或提前失败可以使调用更少。

## 5. 运行中或结束后：只读查看账本

此命令不创建账本、不调用模型，也不展示问句、返回正文或密钥。可在同一运行进行时查看；运行中的临时unknown是“已预留但尚未收回执”，需等当前进程结束后再判断最终未知。

```sh
.venv/bin/python - <<'PY'
import json, sqlite3
from pathlib import Path
p = Path('var/requirements_feedback_v1/live_regression_v1/ledger.sqlite3')
if not p.exists():
    print('NOT_STARTED: ledger absent')
else:
    c = sqlite3.connect(p.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
    try:
        print('cases:', c.execute('SELECT policy,status,count(*) FROM cases GROUP BY policy,status').fetchall())
        print('dispatches:', c.execute('SELECT policy,status,http,count(*) FROM dispatches GROUP BY policy,status,http').fetchall())
        print('unfinished:', c.execute("SELECT id,policy FROM cases WHERE status!='finished'").fetchall())
        for case_id, policy, raw in c.execute('SELECT id,policy,result FROM cases ORDER BY rowid'):
            if raw:
                r = json.loads(raw)
                print('result:', case_id, policy, r['status'])
    finally:
        c.close()
PY
```

若执行进程已结束但存在started组合，不要删除行或重新运行该组合。当前 `--resume` 的含义是跳过所有已开始组合，连中断组合也不会补跑；它不等于重试失败请求。本手册不提供自动resume步骤，中断后交回审核者确认恢复范围。HTTP错误/未知派发触发的停止标志不得手动清除。

## 6. 阶段C：汇总评分与证据

execute正常结束时已自动评分，通常生成 `reports/0001.json`。优先读取已有最新报告，不为“再确认一次”重复score。下面命令不调用模型：

```sh
.venv/bin/python - <<'PY'
import json
from pathlib import Path
run = Path('var/requirements_feedback_v1/live_regression_v1')
reports = sorted((run / 'reports').glob('*.json'))
assert reports, 'No report; preserve partial ledger and hand back'
r = json.loads(reports[-1].read_text())
print('report:', reports[-1])
for key in ('mode','by_policy','efficiency','real_model_requests','unknown_outcomes',
            'usage_unknown','known_total_tokens','provider_stopped'):
    print(key, json.dumps(r[key], ensure_ascii=False))
for s in r['scores']:
    print(s['scenario_id'], s['policy'], s['completed'], s['run_status'],
          s['terminal_state'], ','.join(s['failures']))
paired = {}
for s in r['scores']:
    paired.setdefault(s['scenario_id'], {})[s['policy']] = s['completed']
print('W wins:', [k for k,v in paired.items() if v.get('W') and not v.get('G')])
print('W losses:', [k for k,v in paired.items() if v.get('G') and not v.get('W')])
PY
```

部分停止时，这里的wins/losses只是评分布尔差异；必须注明未运行/中断项，不能称为已证实优劣。业务题与边界题分开报，未运行项不能从分母中删除。账本 `finished` 仅表示结果已落盘，也不等于业务成功。

如execute没有生成报告但账本已存在，先报告异常。审核者要求对部分结果补评分，且冻结校验仍通过时，才能使用以下离线命令；它会新增报告，不会模型重跑或补交付：

```sh
.venv/bin/python -m scripts.eval_requirements_generalization score \
  --out var/requirements_feedback_v1/live_regression_v1
```

### 证据定位

- `ledger.sqlite3`：实际派发、HTTP状态、用量、组合状态的事实来源。
- `reports/*.json`：逐组合评分及汇总；每次评分新增文件，不覆盖。
- `cases/<场景ID>_<G或W>/result.private.json`：本地执行观察。
- 同一case下的 `worker.log`、`*-observation.private.json`：定位失败阶段；文件可能包含私有内容，仅本地读取。
- 同一case下的XLSX及state目录：交付文件、追加与父文件证据；路径以实际结果字段为准，不猜文件名。
- `manifest.json`、`protocol.json`、`preflight.json`：冻结边界与预算。

失败分类先照抄评分维度和诊断码，例如交付形式、需求覆盖、澄清、工具/资料错误、模型协议或传输未知。需要解释原始返回时，只写与失败有关的最小摘要；不把推测写成确定根因，不自行纠正参考答案。

## 7. 结束检查与交付

模型运行结束后，再做一次冻结校验（此处不检查账本不存在）：

```sh
.venv/bin/python - <<'PY'
from pathlib import Path
from scripts.eval_requirements_generalization import validate
validate(Path('var/requirements_feedback_v1/live_regression_v1'))
print('PASS: post-run frozen validation')
PY
git status --short
```

若最终校验失败，保留结果但明确标记“冻结失效，不能作为合格对照”，交回处理。不要改hash补救。

新增公开结果文件时，使用 `audits/requirements_feedback_v1/live_regression_result.json` 和 `live_regression_result.md`；若文件已存在先读取，不能覆盖历史记录。可更新本轮README/技术大纲链接，但必须保留合成成绩与原负结果，`pending_live_protocol.json`作为准备时快照保持不变。不要复制整个var目录，原始账本、问句、返回和数据仍留本地。没有明确Git指示则保留可审阅改动，不提交或推送。

报告按此模板填写，不适用的项写“未完成/未知”，不要留空或编数字：

```text
运行：live_regression_v1
实现基线：74f2521；manifest：64907979d098bad5f3884d06fb738aa4ca89b389eb893f783ce775d542d75884
用户授权：引用对应指令（或：未授权，仅完成零调用检查）
状态：完成 / 部分停止 / 接手检查失败
进程退出码：
冻结校验：执行前 / 执行后
组合：应有16；已落盘__；中断__；未开始__
真实派发：G__/9，W__/27，合计__/36
HTTP错误__；最终未知派发__；已知token__；用量未知__
业务：G__/__，W__/__；边界：G__/__，W__/__
逐场景：ID | G结果 | W结果 | 失败码/维度 | 本地证据路径
W胜/负：__（注明未运行项，不把缺失当作理解失败）
证据：原报告路径、账本路径、公开摘要路径
限制：已见题回归；未证明泛化；默认策略及部署未改
需审核者判断：列出事实冲突或失败案例，不直接提出未经验证的根因
```

完成标准是：授权内执行或按停止条件留下完整部分结果、如实报告调用与业务结果、证据可追溯。不是“刷到全部通过”。

## 8. 可直接发给执行 Agent 的任务说明

> 请按 `docs/REQUIREMENTS_FEEDBACK_EXECUTION_HANDOFF.md` 执行交接。先完成阶段A的零调用检查，核对当前会话是否已明确授权该manifest下最多36次真实调用；没有授权则停在调用前，有授权则按阶段B执行一次并完成结果整理。不要改实现、题集、评分、数据或冻结文件，不自动重试或resume，不创建子Agent，不部署、提交或push。出现异常按文档保留证据并交回。最终按第7节模板交付，严格区分业务成功、流程完成与未知结果。
