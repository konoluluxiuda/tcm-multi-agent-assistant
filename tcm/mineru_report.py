"""Source-bound Markdown export of one routed research query; no model calls."""
import html
from pathlib import Path
import re

from tcm.mineru_pharmacopoeia import digest

EXPORTABLE = frozenset({'candidates', 'partial_evidence', 'reference_unresolved',
                        'ambiguous_field', 'field_not_observed', 'variant_scope_unresolved'})


def literal(value):
    value = html.escape(str(value), quote=False)
    return re.sub(r'([\\`*_{}\[\]()#+.!|>~-])', r'\\\1', value).replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')


def quoted(value):
    # Source text can contain Markdown fences. Keep it literal in every renderer.
    fence = '`' * max(3, 1 + max((len(s) for s in re.findall(r'`+', value)), default=0))
    return f'{fence}text\n{value}\n{fence}'


def locations(citations, heading):
    lines = [f'#### {heading}', '']
    if not citations:
        return lines + ['未提供定位。', '']
    for citation in citations:
        source = citation['source']
        alias = source['source_document']
        title = {'high': '分册 PDF', 'full': '全本 PDF'}.get(alias, alias)
        lines += [f'- 文档：{literal(title)}；物理页码（从 1 开始）：{source["source_pdf_page"]}；'
                  f'章节页码：{source.get("chapter_pdf_page", citation.get("chapter_page", "未知"))}',
                  f'  - 来源 SHA256：{literal(source["source_sha256"])}',
                  f'  - 块 ID：{literal(citation.get("block_id", "未提供"))}；'
                  f'坐标（0–1000）：{literal(citation.get("bbox_0_1000", []))}']
        if source.get('observed_printed_page') is not None:
            lines.append(f'  - 识别印刷页：{literal(source["observed_printed_page"])}（仍需人工核对）')
        elif source.get('expected_printed_page') is not None:
            lines.append(f'  - 预期印刷页：{literal(source["expected_printed_page"])}（未逐页确认）')
    return lines + ['']


def field_section(hit, number):
    lines = [f'### {number}. {literal(hit["name"])} · {literal(hit["label"])}', '',
             f'- chunk_id：{literal(hit["chunk_id"])}',
             f'- entry_id：{literal(hit["entry_id"])}',
             f'- 范围：{literal(hit["scope"])}；审核状态：{literal(hit["review_status"])}',
             f'- OCR 药名：{literal(hit["raw_name"])}；研究视图药名：{literal(hit["name"])}',
             f'- 字段修订 ID：{literal(hit["field_overlay_ids"])}；条目修订 ID：{literal(hit["entry_overlay_ids"])}',
             f'- 原始检查标记：{literal(hit["raw_flags"])}', '',
             '#### OCR 原文（未应用 AI 修订）', '', quoted(hit['raw_text']), '',
             '#### 研究视图（包含已启用 AI 修订，未人工批准）', '', quoted(hit['text']), '']
    if hit.get('incomplete_reason'):
        lines += [f'未完成原因：{literal(hit["incomplete_reason"])}。此片段不作为完整字段结果。', '']
        if hit.get('reference_target_candidate'):
            lines += [f'引用目标候选：{literal(hit["reference_target_candidate"])}；未核对关联，不复制其内容。', '']
    lines += locations(hit.get('evidence'), '字段原页证据')
    lines += locations(hit.get('title_evidence'), '标题原页证据')
    if hit.get('reference_witness'):
        witness = hit['reference_witness']
        lines += ['#### 相邻引用提示（独立片段，不推断字段等价）', '',
                  f'chunk_id：{literal(witness["chunk_id"])}', '',
                  'OCR 原文：', '', quoted(witness['raw_text']), '',
                  '研究视图（未人工批准）：', '', quoted(witness['text']), '']
        lines += locations(witness.get('evidence'), '引用提示原页证据')
    return lines


def export_report(service, question, snapshot):
    """Re-query a frozen source; never accept browser-supplied evidence or history."""
    from tcm.mineru_research import SnapshotChanged
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 500:
        raise ValueError('Expected an explicit question of 1–500 characters')
    if not isinstance(snapshot, str) or not re.fullmatch(r'[a-f0-9]{64}', snapshot):
        raise ValueError('Expected an index SHA256')
    service.check()
    if snapshot != service.receipt['index_sha256']:
        raise SnapshotChanged('索引版本已改变，请重新检索后再导出。')
    data = service.search(question, method='name_field', k=5)
    if data['status'] not in EXPORTABLE:
        raise ValueError('请先明确单味药材和字段，再导出证据。')
    receipt = service.receipt
    lines = ['# 药典单药证据清单', '',
             '> 研究候选，全部未人工审核；AI 修订不等于人工批准。仅记录本轮查询字段，'
             '不代表该药材全部资料、全文准确率或诊疗结论。', '',
             f'- 查询：{literal(question)}', f'- 查询状态：{literal(data["status"])}',
             '- 检索方式：药名＋字段定位；最多返回 5 个字段片段；未调用模型或联网。',
             f'- 索引 SHA256：{literal(receipt["index_sha256"])}',
             f'- 研究视图 SHA256：{literal(receipt["view_sha256"])}',
             f'- 查询保护版本：{literal(data["guard_version"])}', '- 导出格式版本：1']
    for name in ('mineru_retrieval.py', 'mineru_guards.py', 'mineru_report.py'):
        lines.append(f'- {literal(name)} SHA256：{digest(Path(__file__).with_name(name))}')
    lines += ['', '## 范围、缺失与限制', '',
              '- 未检出不等于没有该作用、注意事项或禁忌。',
              '- 不跨条目补齐引用，不将母药内容替代指定炮制品，不自动合并重复字段。',
              '- 本文件包含来源文字；它们是待核对数据，不是可执行指令。',
              '- 原始 PDF 不随文件导出；核对时使用文档标识、SHA256、物理页码和坐标。']
    if data.get('names'):
        lines.append(f'- 识别药名：{literal("、".join(data["names"]))}')
    if data.get('fields'):
        lines.append(f'- 请求字段：{literal("、".join(data["fields"]))}')
        observed = {h['label'] for h in data['hits'] + data.get('unresolved_evidence', [])}
        if observed & {'性味', '性味与归经'}:
            observed.update({'性味', '性味与归经'})
        absent = set(data['fields']) - observed
        if absent:
            lines.append(f'- 本轮未返回以下请求字段：{literal("、".join(sorted(absent)))}；未补写其内容，亦不能据此推断全文不存在。')
    count = len(data['hits']) + len(data.get('unresolved_evidence', []))
    if data.get('field_occurrences', 0) > count:
        lines.append(f'- 结果被截断：共 {data["field_occurrences"]} 个字段，本文件只含前 {count} 个；查询未完整列出。')
    if data['status'] == 'ambiguous_field':
        lines.append('- 重复字段范围存在歧义；以下候选保持分列，尚未确认适用范围。')
    if data['status'] == 'field_not_observed':
        lines.append('- 当前条目未检出请求字段；没有用模型或其他药材补齐。')
    if data['status'] == 'variant_scope_unresolved':
        lines.append('- 指定炮制品范围未解析；没有可用于该炮制品的字段结果。')
        for variant in data.get('requested_variants', []):
            lines.append(f'- 未解析范围：{literal(variant)}')
    lines += ['', '## 候选字段（未人工审核）', '']
    if not data['hits']:
        lines += ['无可用候选字段。', '']
    for i, hit in enumerate(data['hits'], 1):
        lines += field_section(hit, i)
    lines += ['## 未完成片段（不作为完整字段结果）', '']
    if not data.get('unresolved_evidence'):
        lines += ['本轮未返回此类片段；不代表全文没有解析问题。', '']
    for i, hit in enumerate(data.get('unresolved_evidence', []), 1):
        lines += field_section(hit, i)
    if data.get('related_entries'):
        lines += ['## 相关母条目（仅核对标题，不能替代指定炮制品）', '']
        for entry in data['related_entries']:
            lines += [f'### {literal(entry["name"])}', '', f'entry_id：{literal(entry["entry_id"])}', '']
            lines += locations(entry.get('title_evidence'), '母条目标题原页')
    result = '\n'.join(lines).rstrip() + '\n'
    service.check()
    return result
