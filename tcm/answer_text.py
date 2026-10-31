"""Bounded text views of local tool results for clients without card rendering.

Only the HTTP response is expanded. Source excerpts stay out of chat checkpoints.
The complete structured result remains authoritative for provenance and pagination.
"""
import json

from tcm.chat import FIELD_NAMES

LABELS = {value:key for key,value in reversed(list(FIELD_NAMES.items()))}


def bounded(value, limit=500):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + '…（文本预览截断，完整内容见结构化结果）'


def plain_answer(result):
    if result.get('evidence_answer') is not None:
        return result['response']
    herb = result.get('tcm')
    research = result.get('research')
    if herb is not None:
        if herb.get('status') != 'ok':
            return herb['message']
        lines = []
        for record in herb.get('records', [])[:2]:
            for key, field in list(record.get('fields', {}).items())[:14]:
                value = field.get('value') if field.get('state') == 'present' else '未记录'
                lines.append(f'{LABELS.get(key, key)}：{bounded(value)}')
            source = record.get('source', {})
            lines.append('来源：' + source.get('relative_file_path', record['source_record_id']))
        if len(herb.get('records', [])) > 2:
            lines.append('仅预览前2条来源；其余见结构化结果。')
        if herb.get('selection_method') == 'unique_local_candidate':
            lines.append('按唯一目录候选读取，中文药名对应仍待核验。')
        return bounded('\n'.join(lines) or herb['message'], 6000)
    if research is not None:
        data = research.get('tool_result') or {}
        lines = [research.get('message') or result['response']]
        hits = list(data.get('hits', []))
        comparison = data.get('comparison') or {}
        for group in comparison.get('groups', []):
            for cell in group.get('cells', []):
                hits.extend((cell.get('result') or {}).get('hits', []))
        for hit in hits[:4]:
            lines.append(f"{hit.get('name', '')} · {hit.get('label', '')}：{bounded(hit.get('text', ''), 800)}")
            sources = [proof.get('source', {}) for proof in hit.get('evidence', [])[:3]]
            pages = sorted({f"{s.get('source_document', '')} 第{s['source_pdf_page']}页"
                            for s in sources if isinstance(s.get('source_pdf_page'), int)})
            if pages:
                lines.append('出处：来源PDF ' + '；'.join(pages) + '（文件与坐标见结构化证据）。')
        if len(hits) > 4:
            lines.append('仅预览前4条药典候选；其余见结构化结果。')
        if hits:
            lines.append('药典提取候选，尚未完成人工审核。')
        return bounded('\n'.join(lines), 6000)
    return result['response']
