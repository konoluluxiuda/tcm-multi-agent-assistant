# 药材结构化查询 MVP：运行与验收

日期：2026-10-12。已提供数据模块、CLI、独立 HTTP API 和结构化查询页面，尚未接入原医疗聊天路由。数据层与 CLI 仅使用 Python 标准库；HTTP 服务另外需要 FastAPI 和 Uvicorn，API 测试需要 HTTPX。无需模型、Tavily、向量库或 Docker。

## 运行

在项目根目录执行：

```bash
python -m tcm import \
  --source /home/zry/workspace/HDCTI/dataset/ETCM2.0 \
  --version local-2026-10-10

python -m tcm query --name HuangQin
python -m tcm query --name 'Scutellaria baicalensis Georgi'
python -m tcm query --name HuaShi
python -m tcm query --name 黄芩
```

默认数据库：`var/tcm/herbs.sqlite3`。可通过位于子命令之前的 `--db /path/to/herbs.sqlite3` 指定。`var/` 已排除在 Git 和 Docker 构建上下文外；没有把源数据复制进公开静态目录。

查询也支持 `--id <source_record_id>`、`--version local-2026-10-10`。省略版本时使用最近一次成功提交的导入批次所对应版本，生产调用应显式固定版本。版本标签是本地标签，不代表官方数据发布日期。

## HTTP API

轻量环境可独立安装（版本沿用仓库 requirements.txt）：

```bash
python -m venv .venv-tcm
.venv-tcm/bin/python -m pip install fastapi==0.115.11 uvicorn==0.34.0 httpx==0.28.1 anyio==4.8.0 starlette==0.46.1 pydantic==2.10.6
source .venv-tcm/bin/activate
```


独立轻量服务 [`tcm/api.py`](../tcm/api.py)，只加载 herbs repository，不依赖 agent 图或 CV 模型。配置经环境变量 `TCM_DB`（默认 `var/tcm/herbs.sqlite3`）、`TCM_DATASET_VERSION`（默认最新导入批次）。

```bash
uvicorn tcm.api:app --host 127.0.0.1 --port 7863
```

路由：

| 方法/路径 | 用途 |
|---|---|
| `GET /health` | 健康检查 |
| `GET /` | 查询页面：结果表格、分页、同名候选、字段比较和来源展开 |
| `GET /herbs/{name}` | 单药材详情（返回完整 provenance） |
| `GET /herb-records/{source_record_id}` | 按具体来源 ID 查详情，供同名候选选择 |
| `GET /herbs?family=...&flavors=...&limit=20&offset=0` | 分页字段筛选（子串匹配，多条件 AND） |
| `GET /herbs/compare?names=a,b,c` | 多药材字段对齐比较 |

筛选可用的字段：`family`、`habitat`、`medicinal_part`、`properties`、`flavors`、`meridians`、`efficacy_class`、`indications`。未知字段（包括与合法字段混用）、空白值、重复字段返回 400，数据库缺失返回 503。`/health` 仅表示进程存活，不检查数据库就绪状态。7863 用于避开原医疗应用的 7860；如有占用可自行改端口。

```bash
curl localhost:7863/health
curl 'localhost:7863/herbs/HuangQin'
curl 'localhost:7863/herbs?family=Leguminosae'
curl 'localhost:7863/herbs/compare?names=HuangQin,HuaShi'
```

## 页面与分页

- 名称查询支持 Enter 提交。同名记录显示来源文件，点击“查看记录”按来源 ID 打开，不重新用同名字符串选取第一条。
- 属性筛选可添加多个字段条件，使用结果表格和上一页/下一页浏览；每页 20 条。页面保留当前筛选条件和返回版本用于后续翻页与详情查询。
- 比较按字段并列展示，缺失字段显示“未提供”，同名项保留候选并标注未选定，未找到的项标注不可比较。打开候选只查看详情，不自动替换比较列。
- 详情与比较中的“查看字段来源”可展开原始值、来源文件、JSON Pointer 和 SHA-256。
- 中文名未映射、空结果、查询错误、资料库不可用有独立提示。原值始终按文本渲染。

`filter_herbs()` 和 HTTP 筛选现在默认分页，`limit` 默认 20、范围 1–100，`offset` 为非负整数。`count` 表示总匹配数，`returned_count` 表示本页数量，`next_offset=null` 表示无下一页。外部调用方不能继续假设 `records` 包含全部匹配项，应按 `next_offset` 遍历。非法分页参数在 HTTP 层返回 422。

排序固定为来源相对路径与 ID。各查询接口支持 `dataset_version` 参数；未提供则使用服务配置或最近导入版本。同一版本被重新导入时内容仍可能变化，当前未提供跨请求固定批次的快照游标。分页限制响应大小，但当前仍扫描该版本所有有效记录统计总数，不代表大型数据库性能优化。

## 返回契约

- `ok`：一个来源记录匹配；拼音、拉丁名、英文名支持去首尾空格、折叠空白和大小写无关的精确查询。
- `ambiguous_entity`：匹配多个来源记录，返回候选，禁止自动合并或任选一条。当前记录并非已核验药材实体，`herb_id=null`。
- `not_found`：当前版本没有匹配记录；`dataset_not_found`：没有导入指定版本。
- `unsupported_name_mapping`：中文查询暂未建立名称映射；不将其解释成“数据库没有该药材”。
- `quarantined` / `missing_from_snapshot`：按来源 ID 查询到已隔离或已从该版本快照消失的记录，不返回旧有效内容。

详情中每个基础字段返回 `state/value/source`。筛选和比较记录同时保留原有扁平展示值、完整 `fields`、记录级 `source` 与 `warnings`，缺失值和未核验状态不会被摘要丢弃。`source` 保存源字段名、原值、原字段项（含原链接信息）、相对文件路径、文件 SHA-256、实际 JSON Pointer、版本和未核验状态。缺失字段为 `state=missing`，不由模型补全。HTML 名称另取纯文本；未知术语保留原值；PMID 检查格式后去重，但不自动认定支持任何结论。

`name_zh` 和 `aliases` 当前为 null，相关状态由 `chinese_names_unmapped` 明示。传统属性没有翻译或受控词表映射。交叉链接保留原项，尚未进行配对或外链验证。原始基础 section 和抓取汇总保留；其他关联表与图表不入库。JSON 输出中的原值可能包含 HTML，当前页面通过 `textContent` 显示结果，不执行源字段中的 HTML。

## 导入与更新边界

每次导入读取**整个 etcm_herbs 目录**，请勿把局部目录当作同一版本的完整快照：

1. 校验 JSON（含重复对象键）、API 状态、唯一基础 section、重复字段、字段类型、必需拼音名称及 PMID 格式。
2. 内容异常的单个文件在数据库标为 `quarantined`，保留路径、SHA 和错误；原文件不移动、不删除。文件读取失败则回滚整个批次。
3. 文件相对路径决定来源 ID；同名不同文件保留为多条来源。文件名异常仅作警告，不能仅凭名称猜测正确实体。
4. 相同版本重复导入不会新增重复记录；内容改变更新当前记录，旧名称索引清除。源记录变成错误响应时不得继续查询到旧有效内容。
5. 同版本中消失的文件标为 `missing_from_snapshot`；空目录拒绝导入以避免整体误失效。文件更名会产生新来源 ID，尚无自动实体迁移/合并。
6. 不同版本独立保留；同一版本只保存最新记录状态，导入批次保存计数，不是完整历史修订库。需要保留旧内容时使用新版本标签。
7. 一个版本绑定一个源根目录，拒绝将不同根目录静默覆盖到同一版本。数据库不能位于只读源目录内。
8. 所有数据变更在一个 SQLite 事务中提交；查询以只读连接执行，查询不存在的数据库不会创建空文件。

校验通过仅表示满足当前结构契约，源内容仍标记未核验。已提供原文字段子串筛选和字段对齐比较；尚不提供中文别名解析、语义召回、关系分析或药用建议。子串匹配不是标准术语筛选。

## 验证与后续

```bash
python -m unittest discover -s tests -p test_tcm_herbs.py -v
python -m unittest discover -s tests -p test_tcm_api.py -v
```

回归覆盖来源定位、HTML、稀疏字段、未知术语、重复名称、幂等导入、更新失效、跨版本隔离、源文件删除、错误输入、中文未映射、事务回滚和只读源边界。

真实数据导入、重复导入和查询验证结果见 [本次验收报告](../audits/etcm2_mvp/report.md)。repository 已接入独立文本 API（见上文「HTTP API」）。下一步是建立少量人工核验的中文名称映射与黄金查询集；不需要先增加新的 Agent。

2026-10-12 修复与验证见 [API 与页面验收报告](../audits/etcm2_mvp/api_review_2026-10-12.md)。

结构化页面后续验收见 [页面工作流报告](../audits/etcm2_mvp/page_workflow_2026-10-12.md)。
