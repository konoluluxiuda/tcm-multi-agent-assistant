# MinerU修订层与图表正文字段恢复

2026-10-17。本页记录v3解析及首版修订视图（612候选）。后续已修复缺级别标题、采用来源稳定ID并重基11条修订，当前为v4解析/616候选和 `var/mineru_revisions_v2/`，见 [当前修复记录](MINERU_HEADING_RECOVERY.md)。原始PDF、识别文件及历史候选保留，未接入正式聊天或检索库。

## 已交付行为

### 图表caption按原位置恢复

MinerU的 `layout.json` 为caption元素保留独立坐标。v3以图像文件名定位唯一版面对象，核对caption元素数量及文字，再把每个元素作为阅读顺序中的事件处理，不伪造新OCR块。

- 恢复18个caption/脚注元素，合计780字符，原文逐字保留且只归属一次。
- 金银花的6段正文恢复为：检查下的水分、总灰分、酸不溶性灰分、重金属，以及含量测定下的起始说明、色谱条件。
- 字段引用保留 `block_id + text_source + item_index + start/end`；引用框来自对应caption子段，另存layout SHA，避免把正文错误定位到色谱图裁图区域。
- 图表本体保留为字段中的资产引用。公式文字及裁图关系保留，不将银杏叶的公式片段改写成完整公式。
- 612条标题与原始块归属不变，15,812个原始块内容不变。原“图注含字段标题”等风险标记仍保留，字段恢复不自动等于内容已验收。

### 11条修订应用到独立研究视图

| 修订类型 | 数量 | 应用内容 |
|---|---:|---|
| 标题名称读字 | 4 | 龙脷叶、胡芦巴、绵萆薢、秦艽 |
| 字词修订 | 2 | 榼藤子来源中的字词、麝香用法中的“多入” |
| 标题漏行补充 | 5 | 五条拼音；其中两条另有拉丁名 |

这是基于先前原图检查的AI修订，不是ETCM实体映射或人工批准。原始名称、字段原文、风险标记和身份未验证状态均保留。只更改明确定位的目标，不做全文同音字或全局药名替换，也不把其他相同识别自动同步改写。

每条修订绑定目标语料SHA、条目身份指纹、原观察值、证据图片SHA、原PDF SHA/物理页及裁剪位置。字词替换必须唯一匹配一个原文区间；重叠修订、目标观察值变化、标题身份变化或证据图片变化均拒绝。缺失标题行作为有原图证据的补充保存，不冒充原OCR识别行，也不清除原缺行标记。

## 查看产物

`var/mineru_revisions_v1/` 内：

- `revision_diff.md`：11条原值/修订值及启用状态。
- `index.html`：修订对照与原图，可直接本地打开。
- `revision_ledger.json`：修订台账及来源证据。
- `research_view.json`：已应用11条修订的612条研究候选，字段同时保留raw_text、text和引用。
- `original_view.json`：不应用修订的同批v3候选视图；仍包含已修复的caption字段划分。
- `summary.json`：输入、代码、结果指纹及计数。

这里的“原始视图”指关闭文字覆盖层的v3解析视图，不是回退v3的字段解析算法；v2旧候选另行保留。新输出目录用于保存每次运行，不能覆盖既有结果。

## 构建和撤销

```bash
# 从已核验的MinerU结果重新生成候选，目录必须不存在。
python -m scripts.build_mineru_pharmacopoeia --out var/mineru_pharmacopoeia_next

# 默认使用v3候选、v2复核提案，构建独立研究视图。
python -m scripts.build_mineru_revisions --out var/mineru_revisions_next

# 关闭全部文字修订，保留caption解析修复。
python -m scripts.build_mineru_revisions --without-overlays \
  --out var/mineru_revisions_without_overlays

# 关闭某条修订；ID参见revision_diff.md，可多次指定--disable。
python -m scripts.build_mineru_revisions --disable mineru-edit-0001 \
  --out var/mineru_revisions_one_disabled

python -m unittest tests.test_mineru_pharmacopoeia \
  tests.test_mineru_review_packet tests.test_mineru_captions \
  tests.test_mineru_revisions tests.test_pharmacopoeia_segmentation -q
```

若重新构建到不同候选目录，通过 `--base` 显式指定；修订必须重新验证目标身份和观察值。当前修订输入是冻结的v2原图复核提案，不支持直接修改JSON便跳过校验。

## 验证与限制

- 56项相关回归通过，新增覆盖caption归属、子段坐标、原文一致性、单条/全部撤销、目标漂移、冲突修订及审核状态。
- v3与研究修订均实际重复构建，结果SHA一致。
- 单独关闭首条修订仅改变对应条目；全部关闭的研究视图SHA等于保存的original_view文件。
- 实际篡改导入候选或证据图片副本，构建在输出创建前拒绝；原PDF和候选文件未被改写。
- 已将金银花六段正文与高清PDF物理258页原图对照，确认字段位置；没有据此计算全文准确率或作临床正确性验收。
- 修订前后的数值剂量逐字段一致。全部条目仍为未人审、非生产可用，标题补行与名称修订不证明全文都正确。

脱敏汇总见 [审计记录](../audits/mineru_revisions/)。正文、修订语料、原图与截图均在忽略目录，不进入Git或Docker镜像。

## 下一任务

已建立本地词法/字段检索基线，使用20个新编问题、4个范围/缺项问题及4个独立列示的已知失败回归；当前仍为AI来源辅助参考，不是真实用户题库或人工金标准。评测确认白头翁标题缺少text_level，导致正文并入白术，须先修复再考虑入库。见 [检索基线与失败记录](MINERU_RETRIEVAL_BASELINE.md)。后续补真实问题、向量方案对照及人工来源核验；检索命中不视为事实验证通过。
