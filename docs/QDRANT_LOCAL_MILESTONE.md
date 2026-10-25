# 当前药典研究链路的 Qdrant 本地接入

2026-10-25。先完成累计交付提交 `3eea40f`，再实施本轮可选检索后端。真实资料未外发，构建与查询禁网；安装依赖只下载软件包。

## 实际实现

`preview_local_tasks --qdrant-generation ...` → 单药/两药报告文本分支 → 本地BGE编码 → Qdrant实体/版本/正文范围过滤 → 原SQLite锚点回读 → 与字段/BM25结果RRF融合 → 原后台报告/导出/历史。

- SDK沿用仓库已有版本 `qdrant-client==1.13.3`，使用本地持久模式。此机Docker的WSL集成不可用，**没有部署Qdrant独立服务或集群**。
- 复用8654条512维BGE向量，没有重新嵌入或改变文本切分。点ID由chunk ID稳定生成；payload只存检索元数据，正文和PDF锚点仍从经过校验的原索引读取。
- 新目录构建、完成计数和文件摘要后发布；拒绝覆盖已有代次。receipt绑定源索引、模型、向量及条目清单；读取前检查数据库文件摘要。
- 检索前限定唯一药材、源版本、正文范围及允许字段对应的chunk集合；无全局无过滤检索入口。小型实体候选集全部取回后按分数及chunk ID稳定排序，保持numpy基线的并列顺序。
- 本地SDK独占文件目录，外层增加进程文件锁、10秒等待上限；SDK关闭后释放。当前是串行访问单个本地代次，不能当成并发向量服务器吞吐量证明。
- 索引不可用或校验不通过时，报告保留已有词法证据并显式记录`dense=unavailable, backend=qdrant_local`，不会偷偷更换为别的向量来源。

不是重新实现一套向量库；适配复用已有`DenseRetriever`的实体保护和引用处理，新增可替换rank方法。查询仍加载原缓存作指纹/形状校验，因此尚未获得服务端常驻索引的内存或启动收益。

实现依据：[Qdrant过滤文档](https://qdrant.tech/documentation/search/filtering/)，并核对安装的1.13.3 SDK接口。SQLite BM25仍是关键词检索分支，未把它包装成Qdrant原生稀疏向量检索；学习式重排尚未接入。

## 验收结果与选择

- 5次真实查询（含重复读取）与numpy基线命中顺序/状态一致，分数差小于1e-5。
- 四个独立查询进程均实际使用Qdrant、结果一致；拒绝版本不符、文件校验失败、覆盖已有代次；缺失代次可降级为词法结果。
- 真浏览器在7880提交当归/川芎联合报告；导出内容中双方文本分支均为`qdrant_local`且`dense=used`，共同靶点172，刷新历史正常，无JS错误。
- 31项受影响离线回归通过：索引契约、原向量检索、单/双药子图、任务API、研究部署。未重复运行全仓回归；此前961项对应提交前的阶段。

沿用44题开发集，同快照对照：

| 方案 | 目标锚点Hit@5 | MRR@5 | 拒答正确 | 错药材题数 | 本机每题均时 |
|---|---:|---:|---:|---:|---:|
| 名称/字段 | 25/35 | 0.7143 | 9/9 | 0 | 1.19ms |
| 名称限定BM25 | 29/35 | 0.8286 | 9/9 | 0 | 3.87ms |
| Qdrant dense + RRF | 29/35 | 0.8143 | 9/9 | 0 | 499.05ms |

这是开发诊断，不是独立评测，不报告Recall；均时含每题初始化/完整性校验，不代表服务器p95。结论是后端接入成功，**未证明效果或性能优于BM25**。默认仍不开启向量；本机7879保持原配置，7880为显式Qdrant演示。

## 复现、切换与回滚

在已有numpy/ONNX/tokenizers的本地环境安装可选依赖：

```bash
python -m pip install -r requirements-local-qdrant.txt
python -m scripts.build_local_qdrant --out var/qdrant_research_v1/generation_new
python -m scripts.check_local_qdrant --generation var/qdrant_research_v1/generation_new --out var/qdrant_check.json
python -m scripts.eval_cross_source_retrieval --qdrant-generation var/qdrant_research_v1/generation_new --out var/qdrant_eval.json
```

本机已准备代次的启动命令：

```bash
/tmp/tcm-ci-mail-env/bin/python -m scripts.preview_local_tasks \
  --port 7880 --state var/qdrant_research_v1/preview_state \
  --diseases var/disease_catalog_v1/build/diseases.sqlite3 \
  --dense-python /tmp/tcm-dense-20261019/bin/python \
  --qdrant-generation var/qdrant_research_v1/generation
```

升级时构建新目录，验收后切换配置并重启，旧目录保留。回滚可移除`--qdrant-generation`回到numpy，或同时移除`--dense-python`回到词法。无数据库schema迁移，也不改变已有报告历史。

摘要保存在`audits/qdrant_research_v1/`；向量数据库、正文、模型不提交Git。后续仍缺服务模式部署/并发负载验证、独立问题集、中文重排与质量收益证据。本轮新增代码在累计提交之后产生，尚未再次提交。
