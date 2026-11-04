# 独立AI问句集与本地参考

40个场景、43轮问句。`authored_questions.json` 与 `AUTHOR.md` 是原始出题资料，哈希由 `author_freeze.json` 固定；不是临床用户研究。题目已用于真实评测，后续开发不得继续称为未见题。

`intent_annotations.private.json`、`references.private.json` 为本地意图标注和来源参考，含资料来源信息，不进入Git。公开的 `annotation_notes.json` 与 `reference_review.json` 仅记录核验范围与局限；程序化来源一致性不等于药材身份人工审核。取得相同资料后仍需复核参考，不能从被测输出反推答案。

历史live_v1因未知派发终止；修复后的live_v2在原授权范围内恢复，回执位于忽略目录 `var/requirements_completion_v1/live_v2/`。每次真实执行均需要对应冻结manifest，不能仅凭本目录原句直接重跑。最新结果见[收尾验收](../../audits/requirements_completion_v1/README.md)，旧成绩见[首轮记录](../../audits/requirements_heldout_v2_live_v1/README.md)。
