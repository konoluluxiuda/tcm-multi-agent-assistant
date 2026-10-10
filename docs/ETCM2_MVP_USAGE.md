# 药材结构化查询 MVP：运行与验收

日期：2026-10-10。当前交付的是独立数据模块和命令行工具，尚未接入聊天路由或网页。只使用 Python 标准库，无模型、Tavily、向量库或 Docker 启动依赖。

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

## 返回契约

- `ok`：一个来源记录匹配；拼音、拉丁名、英文名支持去首尾空格、折叠空白和大小写无关的精确查询。
- `ambiguous_entity`：匹配多个来源记录，返回候选，禁止自动合并或任选一条。当前记录并非已核验药材实体，`herb_id=null`。
- `not_found`：当前版本没有匹配记录；`dataset_not_found`：没有导入指定版本。
- `unsupported_name_mapping`：中文查询暂未建立名称映射；不将其解释成“数据库没有该药材”。
- `quarantined` / `missing_from_snapshot`：按来源 ID 查询到已隔离或已从该版本快照消失的记录，不返回旧有效内容。

每个基础字段返回 `state/value/source`。`source` 保存源字段名、原值、原字段项（含原链接信息）、相对文件路径、文件 SHA-256、实际 JSON Pointer、版本和未核验状态。缺失字段为 `state=missing`，不由模型补全。HTML 名称另取纯文本；未知术语保留原值；PMID 检查格式后去重，但不自动认定支持任何结论。

`name_zh` 和 `aliases` 当前为 null，相关状态由 `chinese_names_unmapped` 明示。传统属性没有翻译或受控词表映射。交叉链接保留原项，尚未进行配对或外链验证。原始基础 section 和抓取汇总保留；其他关联表与图表不入库。JSON 输出中的原值可能包含 HTML，未来网页必须按普通文本转义展示，不能直接作为 HTML 渲染。

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

校验通过仅表示满足当前结构契约，源内容仍标记未核验。该模块不提供中文别名解析、属性筛选、比较、语义召回、关系分析或药用建议。

## 验证与后续

```bash
python -m unittest discover -s tests -p test_tcm_herbs.py -v
```

回归覆盖来源定位、HTML、稀疏字段、未知术语、重复名称、幂等导入、更新失效、跨版本隔离、源文件删除、错误输入、中文未映射、事务回滚和只读源边界。

真实数据导入、重复导入和查询验证结果见 [本次验收报告](../audits/etcm2_mvp/report.md)。下一步是建立少量人工核验的中文名称映射与黄金查询集，再将 repository 接入文本 API；不需要先增加新的 Agent。
