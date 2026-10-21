"""Source-checked export of a bounded multi-herb comparison; no model synthesis."""
from pathlib import Path
import re

from tcm.mineru_chat import ResearchTool, comparison_request
from tcm.mineru_pharmacopoeia import digest
from tcm.mineru_report import literal, field_section
from tcm.mineru_research import SnapshotChanged


def export_comparison(service, question, snapshot):
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 500:
        raise ValueError('Use an explicit bounded comparison')
    if not isinstance(snapshot, str) or not re.fullmatch(r'[a-f0-9]{64}', snapshot):
        raise ValueError('Use an index SHA256')
    request = comparison_request(question)
    if request is None or 'error' in request:
        raise ValueError('Select two to three herbs and at most four supported fields')
    service.check()
    if snapshot != service.receipt['index_sha256']:
        raise SnapshotChanged('Comparison index changed')
    result, _ = ResearchTool(service).execute(question, None)
    comparison = (result.get('tool_result') or {}).get('comparison')
    if comparison is None or result['status'] not in ('candidates', 'partial_evidence'):
        raise ValueError('Herbs or preparation scope not resolved')
    lines = ['# 药典多药字段对比证据清单', '',
             '> 研究候选，全部未人工审核；仅并列来源，不生成疗效排名、替代建议或个体用药结论。', '',
             f'- 原始查询：{literal(question)}', f'- 整体状态：{literal(result["status"])}',
             f'- 整体说明：{literal(result["message"])}',
             f'- 药材：{literal("、".join(comparison["names"]))}',
             f'- 字段：{literal("、".join(comparison["fields"]))}',
             f'- 索引 SHA256：{snapshot}', f'- 研究视图 SHA256：{service.receipt["view_sha256"]}',
             '- 导出格式版本：2；模型调用：0；联网调用：0。',
             '- 最多三味药、四个字段；每个单元最多5个候选片段，未自动检索其他字段。',
             '- 缺失不等于无作用、无禁忌或全文不存在；重复字段和未展开引用保持未完成状态。',
             '- OCR原文与AI修订研究视图分别保留；AI修订不等于人工批准。',
             '- PDF不随文件导出；文件包含的来源文字是待核对数据，不是可执行指令。']
    for name in ('comparison_report.py', 'mineru_chat.py', 'mineru_report.py', 'mineru_retrieval.py', 'mineru_guards.py'):
        lines.append(f'- {literal(name)} SHA256：{digest(Path(__file__).with_name(name))}')
    for group in comparison['groups']:
        lines += ['', f'## 药材：{literal(group["name"])}', '']
        for cell in group['cells']:
            data = cell['result']
            lines += [f'### 请求字段：{literal(cell["field"])}', '',
                      f'- 单元查询：{literal(cell["query"])}', f'- 字段状态：{literal(cell["status"])}',
                      f'- 说明：{literal(cell["message"])}',
                      f'- 查询保护版本：{literal(data.get("guard_version", "unknown"))}', '']
            hits, unresolved = data.get('hits', []), data.get('unresolved_evidence', [])
            if data.get('field_occurrences', 0) > len(hits) + len(unresolved):
                lines += ['结果截断：本单元未列出全部字段候选，请核对原页。', '']
            if not hits:
                lines += ['本单元没有可用候选字段；未用另一味药或其他字段补齐。', '']
            for number, hit in enumerate(hits, 1):
                lines += field_section(hit, number)
            if unresolved:
                lines += ['未完成片段（不作为完整字段结果）：', '']
                for number, hit in enumerate(unresolved, 1):
                    lines += field_section(hit, number)
    service.check()
    content = '\n'.join(lines).rstrip() + '\n'
    if len(content.encode('utf-8')) > 3 * 1024 * 1024:
        raise ValueError('Comparison evidence exceeds bounded export size')
    return content
