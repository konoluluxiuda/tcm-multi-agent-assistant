# R1 数据发布与 benchmark：使用和维护

状态：2026-10-30，R1已完成研究基线，见[实际验收](../audits/data_rag_r1/README.md)。当前发布`var/data_releases/r1_20261030`包含独立新建的616条目/8665片段词法索引、8665个未审核候选和30题冻结参考。**R1本身未构建向量或评分，清单保留当时状态；后续[R2](RAG_RETRIEVAL_COMPARISON.md)已在独立目录构建新向量并首测，网页未切换新检索，生成未验收。**

## 当前可以运行的命令

在项目根目录使用已有`.venv`与本地资料。命令均不读模型密钥、不调用外部模型、不导出Langfuse。

```bash
# 回查当前发布：登记源文件、2076份原JSON、产物及参考锚点
.venv/bin/python -m scripts.build_data_release \
  --check --out var/data_releases/r1_20261030

# 仅核验冻结参考与原始证据，不跑检索/生成，也不改答案
.venv/bin/python -m scripts.build_rag_benchmark \
  --check --out eval/rag_v1/references.json

# 单独生成未审核候选；文件路径必须新建
.venv/bin/python -m scripts.build_rag_benchmark \
  --candidates --out var/rag_candidates_new.json

# 使用相同已核对的源和参考，在新目录重建词法索引与发布
.venv/bin/python -m scripts.build_data_release \
  --out var/data_releases/r1_rebuild_new
```

`--register-index`仅登记已有词法索引而不重建；默认命令会在发布目录新建索引，原始JSON/PDF、侧库、研究视图及现用索引只读。输出路径不得位于输入数据目录内，已有目录不可覆盖或续跑。源路径不同可通过`--config`提供显式JSON路径表，允许键见`build_data_release.defaults()`；不使用`.env`解析源路径。

## 发布目录与边界

| 文件 | 用途 |
|---|---|
| `release.json` | 最后写入的ready标记、content_id、源/产物/代码hash、规模和兼容性；整体内容身份也参与回查 |
| `sources.private.json` | 登记本机源文件的绝对路径，权限600；不是可分享配置或完整备份 |
| `index/` | 默认构建的独立SQLite字段/FTS5索引，未自动切换到现有服务 |
| `raw_herb_files.json` | 当前快照原始JSON路径与hash；逐字段指针和规范化在构建时回查 |
| `benchmark_candidates.json` | 未审核题目模板与原始锚点候选，`usable_as_gold=false` |
| `benchmark_references.json` | 与仓库冻结参考相同的30题研究参考，不含临床批准 |
| `benchmark_source_check.json` | 原始锚点与契约完整性，不是问答成绩 |
| `index_anchor_mapping.json` | 原始证据到本版本chunk_id的映射，不是检索命中/召回率 |
| `build_started.json` / `failed.json` | 构建开始/失败诊断；没有合法`release.json`的目录不可消费 |

发布引用原资料，源文件移走或变化后会回查失败，不能把整个目录当作可移植备份。源SQLite存在未checkpoint的WAL时拒绝登记/使用，避免主文件hash没有覆盖最新内容。构建期间或回读发现变化，不会静默重算标准答案。

## benchmark分工与审核口径

当前[30题种子](../eval/rag_v1/seeds.json)包含作者明确选择的原始块/偏移或JSON指针及预期hash；[冻结参考](../eval/rag_v1/references.json)由脚本核验后生成。它们使用相同原始来源但不读取被测检索输出来建立答案；片段重编号只改变版本映射，不修改原始参考。

类别为20有证据、5缺独立字段、5对象/炮制形式未定，开发20/保留10按来源实体分组，无交叉。两题并列ETCM原值与药典原文，不自动批准对象身份。所有参考AI核对、待人工签核；R1冻结时保留题尚未运行；R2已首测，当前转为暴露回归，不代表作者盲测或全项目从未见过实体。

缺字段参考只说明明确原始OCR条目范围内没有该独立标签；需要报告资料不足，不能补造，也不能说现实中没有禁忌。候选歧义参考必须保留多个候选而非擅自选值。引用可定位、文本在原文中、回答语义受支持仍是三项不同检查，R1仅交付前置证据和参考。

30题已冻结，不依据R2成绩选择性删题或放宽要求。未来需要新资料/新题时，先生成候选、核验原始证据并另建种子/参考版本，再显式冻结：

```bash
# 新种子必须已有明确原始核对记录；不能将candidate批量标为approved
.venv/bin/python -m scripts.build_rag_benchmark \
  --freeze-seeds eval/rag_next/seeds.json \
  --out eval/rag_next/references.json
```

新源路径需同时提供对应`--config`，新发布绑定新参考。`--freeze-seeds`不会替作者证明需求/医学语义正确；它验证源hash、对象、偏移、缺失范围及分组，不把审核状态升级为human_signoff。

## R2读取发布时必须处理的问题

旧BGE缓存8654条/512维绑定旧v2；本发布词法索引8665片段，旧向量明确不可用。R2新建兼容向量/Qdrant代，记录模型指纹、配方、维度和实际token截断；不把80个长字符字段诊断或旧249条截断收据当新模型表现。

名称侧库有4条与修订视图不一致的中文候选，已列入`sidecars.mapping.quarantined_candidates`。它们只在发布清单中登记，旧侧库没有被回写；新跨源适配器必须执行排除/待核对策略。当前研究参考的两道结构化桥接不依赖这些来源。

R2应先回查release，再在同一资料/过滤/k/参考下比较检索。所有参考`relevance_complete=false`，默认报告Hit/MRR与错误类别，不能把34/34结构映射当作全库Recall。开发调优冻结后才首测保留题，之后保留题转为回归；这是R1时的前置契约；[R2实际结果](RAG_RETRIEVAL_COMPARISON.md)已完成检索首测，模型生成仍未验收。

检索器只能获得原问及共同的合法输入范围；不能用参考中的预期对象/锚点构造答案候选池，再把池内排序当召回成绩。固定池诊断必须单列。
