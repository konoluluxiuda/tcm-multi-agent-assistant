# ETCM2 药材结构化 MVP 验收

日期：2026-10-10。本地版本标签：`local-2026-10-10`。

## 结果

实现了独立的药材基础字段校验、SQLite 来源记录存储、拼音/拉丁名/英文名或来源 ID 精确查询。所有返回字段可定位到原文件及 JSON Pointer。不调用模型或联网工具，不修改原始目录，不导入成分/靶点/疾病或关联图表。

| 验证 | 结果 |
|---|---|
| 首轮真实导入 | 2,076 份有效来源记录；0 隔离；1 组重复拼音 |
| 同版本重复导入 | 新增 0；更新 0；未变化 2,076；无记录数增长 |
| 新增单元回归 | 10/10 通过 |
| 既有 runtime 回归 | 5/5 通过 |
| 真实数据检查 | 26/26 通过；含 13 个字段 JSON Pointer 回指验证 |
| 哈希一致性 | 2,076 条入库记录 SHA-256 与上一轮审计清单一致 |
| SQLite 完整性 | `PRAGMA integrity_check=ok` |
| CLI 验证 | HuaShi 返回 2 个候选；黄芩返回中文映射暂不支持 |
| 数据库大小 | 20,774,912 字节，约 19.81 MiB |

结构校验通过不等于科学内容正确。HuaShi 的两份文件继续保留为独立来源记录，未自动合并。文件名异常按警告保留；没有建立核验后的 canonical herb_id。

## 行为验证

- HuangQin：精确查询得到一条记录；大小写与首尾空白归一化；拉丁名查询及来源 ID 查询通过。
- HuaShi：返回 `ambiguous_entity` 与两份来源，不随意选择答案。
- HuaYeEZhangChai：稀疏字段明确标记缺失。
- 黄芩：返回 `unsupported_name_mapping`，不会凭拼音生成中文名。
- 不存在的药名：返回 `not_found`。
- 单元测试另外覆盖：源内容更新、错误响应替换旧有效记录、跨版本隔离、文件删除、空快照拒绝、读取失败整批回滚、非法输入和只读来源边界。

## 产物与复现

- [使用文档](../../docs/ETCM2_MVP_USAGE.md)
- [导入器与查询模块](../../tcm/herbs.py)
- [CLI](../../tcm/__main__.py)
- [回归用例](../../tests/test_tcm_herbs.py)
- [首轮导入结果](import_first.json)、[重复导入结果](import_repeat.json)
- [真实查询检查结果](query_checks.json)、[可重跑验证脚本](../../scripts/verify_etcm_mvp.py)

```bash
python -m unittest discover -s tests -p test_tcm_herbs.py -v
python -m unittest discover -s tests -p test_runtime.py -v
python -m scripts.verify_etcm_mvp --source /home/zry/workspace/HDCTI/dataset/ETCM2.0
```

数据库在 `var/tcm/herbs.sqlite3`，已排除 Git 跟踪和 Docker 构建上下文；生成库可按使用文档重建，不公开分发原数据。

## 当前边界

这是 CLI / repository 阶段，现有网页和聊天 Agent 尚未接入这套查询，因此不能在聊天页据此宣称已经支持中草药数据。没有重启容器、重建或发布镜像。

待完成：人工核验中文名称映射、属性词表与筛选/比较、文本 API、人工黄金集、受控来源展示。导入批次仅记录摘要，同一版本保留最新记录状态；需要历史内容时使用独立版本标签。快照内容应在导入期间保持不变。源数据许可、官方版本与科学正确性仍待确认。

## 后续验收

上述为 2026-10-10 的阶段状态。2026-10-12 已新增并验证独立 API、查询页面、筛选与比较，见 [后续修复验收](api_review_2026-10-12.md)；原医疗聊天路由仍未接入。
