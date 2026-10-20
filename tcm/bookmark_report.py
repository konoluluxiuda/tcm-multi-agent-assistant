"""Source-checked evidence notes from a single owned bookmark; no model synthesis."""
import json
from pathlib import Path

from tcm.mineru_pharmacopoeia import digest
from tcm.mineru_report import export_report, literal, quoted
from tcm.research_bookmarks import preview, check_source, BookmarkUnavailable


def export_note(item, runtime, *, include_note=False):
    payload = item['payload']
    result = preview(payload, runtime)  # Also checks actual query/name agreement.
    focus = payload['focus']
    lines = ['# 收藏研究证据笔记', '',
             '> 来源候选未人工审核。仅导出本项收藏的查询范围，不生成综述、疗效排序或作用机制结论。', '',
             f'- 收藏标题：{literal(item["title"])}',
             f'- 收藏修订号：{item["revision"]}',
             f'- 证据笔记格式版本：1',
             f'- 导出代码 SHA256：{digest(Path(__file__))}',
             '- 模型调用：0；联网调用：0。', '']
    if include_note:
        lines += ['## 个人笔记（用户记录，不作为资料证据）', '',
                  quoted(item['note']) if item['note'] else '未填写个人笔记。', '']
    lines += ['## 来源绑定', '', quoted(json.dumps(focus, ensure_ascii=False, indent=2)), '']
    if payload['kind'] == 'research':
        tool = check_source(payload, runtime)
        lines += ['## 药典查询证据', '',
                  export_report(tool.service, focus['last_question'], focus['index_sha256'])]
    else:
        data = result['assistant']['relations']
        rows = data['data']
        lines += ['## ETCM来源关联证据', '',
                  f'- 查询种类：{literal(data["kind"])}',
                  f'- 数据版本：{literal(data["dataset_version"])}',
                  f'- 收藏页码：{data["page"]}；仅导出本页，未自动读取其他页。',
                  f'- 当前目录总数：{rows["total"]}；本页返回：{len(rows["items"])}项。',
                  f'- 计数单位：{literal(rows.get("count_unit", "source_rows"))}',
                  '- 候选路径、共同标识和来源关联不证明化学身份、实验结合或作用机制。',
                  '- 空结果/未匹配不代表没有关系；来源抓取完整性未保证。',
                  '- 原始JSON行、JSON位置、文件SHA和抓取状态保留在下方；见证截断标记仍保留，不能视为全量证据。', '']
        for key in ('groups', 'target'):
            if key in data:
                lines += [f'### {"已选药材来源" if key == "groups" else "已选靶点来源"}', '',
                          quoted(json.dumps(data[key], ensure_ascii=False, indent=2)), '']
        if data['kind'] == 'disease':
            lines += ['疾病富集记录不是适应证或治疗推荐，不证明药材可治疗所列疾病；'
                      '未核验疾病身份或统计显著性，原始名称未自动翻译。', '',
                      '疾病表抓取状态（空列表表示来源未提供，不表示完整）：', '',
                      quoted(json.dumps(data.get('source_capture_status', []), ensure_ascii=False, indent=2)), '']
        if data['kind'] == 'target_herb':
            lines += ['靶点反查按药材来源记录计数，不是去重药材种数、药效排名或治疗推荐；'
                      '每条最多3行见证，截断标记和来源抓取状态保留。', '']
        if rows.get('coverage'):
            lines += ['### 目录覆盖（不是检索召回率）', '',
                      quoted(json.dumps(rows['coverage'], ensure_ascii=False, indent=2)), '']
        if not rows['items']:lines += ['本页未返回来源记录。', '']
        for number, row in enumerate(rows['items'], 1):
            lines += [f'### 本页第{number}项来源记录', '', quoted(json.dumps(row, ensure_ascii=False, indent=2)), '']
    check_source(payload, runtime)
    content = '\n'.join(lines).rstrip() + '\n'
    if len(content.encode('utf-8')) > 3 * 1024 * 1024:
        raise BookmarkUnavailable('Evidence note exceeds bounded export size')
    return content
