# 贮藏字段边界修复与原块核验

日期：2026-10-30。范围：修复确定性解析错误，为后续真实模型对照准备可信的输入，不把此修复当作LLM收益。本轮外部模型调用0；没有提交Git、发布镜像或改写旧实验成绩。

## 变更与结果

黄芪PDF物理页344（印刷页325）的贮藏正文只有原块`part-301-400:840`；其后的841/842是试剂配制注释。旧解析器把未标字段的后续段落全部追加到当前字段，导致污染已存在于索引，DataOps仅照录。

本轮逐页查看high原PDF物理页107、108、226、227、285、286、344、392、394、413、427，确认水蛭、陈皮、附子、枳壳、枳实、黄芪、番泻叶、滑石粉、槟榔、橘红、橘核共11处后续注释的归属。它们是试剂说明、栽培变种或检测说明，仍属于条目资料，不能称为无关附录。玉竹、柏子仁、炙黄芪作为3个原页正常对照。

- 解析器v5：仅在贮藏字段之后识别明确的上述注释类型，另存“条目注释”，连同跨页续文保留；未知注释和贮藏相关注释不截断。支持同一原块中换行后的注释，原始偏移不变；不使用药名或固定块ID作为解析规则。
- 原始15812个块及616个条目ID不变，所有原文与已有修订文本逐字守恒；其他业务字段、标题及既有11条覆盖修订完全不变。只为11个条目各增加一个注释字段。
- 新数据链：`var/mineru_pharmacopoeia_v5` → `var/mineru_revisions_v3` → `var/mineru_retrieval_v3`。索引616条目、8665块（原8654块加11注释），旧v2目录不修改。
- [14条冻结参考](../../eval/pharmacopoeia_storage_boundary_v1.json)直接指定原页/原始块归属，参考值只从这些冻结块重建，不从候选索引/研究视图生成。包含源PDF/块hash、坐标、原偏移、应保留和应移出的块。AI视觉核对待人审，不是人工金标准，也不是独立未见题。
- 同一参考：旧索引3/14，新索引14/14；其中正常对照3/3保持不变。指标要求字段值、字段引用和另存注释均正确，清掉正文却丢掉注释也不通过。不将14/14写成全库/真实用户准确率。
- 19模块185项受影响离线检查通过、0跳过/0联网尝试；最终问法补修后3模块33项复测通过，与前述计数重叠，不相加。新增测试覆盖跨页注释、同块偏移、合法/未知注释保留、源与参考篡改、文本干净但引用污染、删除注释和作用域替代。

28道既有开发问句分别运行name_field/BM25，共56个新旧对照：name_field的状态与候选完全不变；BM25有1题候选变化。陈皮“该怎样保存”仍为partial_evidence、首位不变，干净贮藏进入第二位，属于字段缩短和FTS统计变化；不能以此宣称检索准确率提升。原差异回执保留，未调弱基线或改旧参考。

实际服务7896/7912已重启到v3，仍保持原state、原功能开关和本地Langfuse。固定交付与动态v1→追加贮藏v2的字段值、引用原块、下载hash及XLSX单元格均对独立参考验证通过，旧字段复用8个、v1文件不变。聊天页面整理、下载、刷新和续改通过；顺带修复了“黄芪如何贮藏/如何储存”的右侧语言上下文，实际聊天两端口均可返回干净贮藏正文及第344页出处。未改变别名/炮制对象范围。

切换前创建的测试旧工作区在切换后仍能读取旧值和下载原文件；跨索引增量提案返回409且不创建子工作区，不静默把旧结果写成新资料。本地Langfuse的动态v2观测也已回读，来源摘要对应新索引。

## 复现与回滚

已有输出目录不可覆盖；完整从原导入重建时给各阶段选择新的目录：

```bash
.venv/bin/python -m scripts.build_mineru_pharmacopoeia --out var/mineru_pharmacopoeia_v5
.venv/bin/python -m scripts.build_mineru_revisions \
  --base var/mineru_pharmacopoeia_v5 --out var/mineru_revisions_v3
.venv/bin/python -m scripts.build_mineru_retrieval \
  --revisions var/mineru_revisions_v3 --out var/mineru_retrieval_v3
.venv/bin/python -m scripts.eval_storage_boundaries \
  --index var/mineru_retrieval_v3 --out var/storage_boundary_recheck.json
.venv/bin/python -m scripts.check_storage_delivery \
  --url http://127.0.0.1:7912 --out var/storage_delivery_recheck.json
```

最后一条需要已启动`--dataops-synthetic`的实际服务，创建测试专用会话及3个工作区，不调用外部模型。预览、普通药典查询脚本及研究Compose默认指向v3；旧实验脚本保留固定v2以复现历史，不能直接当v3最新结果。现有本地稠密/Qdrant代次绑定旧快照，本轮未重建或验收，不宣称已迁移。

回滚预览：停止服务，在原state命令中显式增加`--pharmacopoeia-index var/mineru_retrieval_v2`；该版本保留已知贮藏污染，只用于旧实验复现。研究Compose回滚绑定源到v2。原PDF、OCR、数据库和旧产物均不删除。索引切换必须重启；旧工作区来源版本不符时新建任务，不跨版本缓存复用。

机器可读结果：[acceptance.json](acceptance.json)。详细本地回执在`var/storage_boundary_v1`：before/after、corpus_diff、retrieval_regression、tests、final_query_tests、delivery、source_switch、chat_browser、runtime及trace。私有cookies、PDF页图和真实交付文件不复制到Git审计目录。

下一项：使用既有V3预算和Langfuse运行三题真实模型兼容对照，定位动态A未交付的原因；再做独立的模型价值实验。当前工具仍不支持通用原页上下文补查或受限字段修订提案；这些不能因确定性解析器修好而写成已实现。
