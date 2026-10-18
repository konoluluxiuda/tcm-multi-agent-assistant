"""Session-bound local/PubMed evidence inventory; no inferred entity mapping."""
from datetime import datetime, timezone
from pathlib import Path
import re

from tcm.agent_pubmed import _hash
from tcm.mineru_pharmacopoeia import digest
from tcm.mineru_report import export_report, literal
from tcm.mineru_research import SnapshotChanged

FLAG_LABELS = {'retracted': '已撤稿（来源标注）', 'retraction_notice': '撤稿通知',
               'has_erratum': '存在更正通知', 'erratum_notice': '更正通知',
               'has_expression_of_concern': '存在关注声明',
               'expression_of_concern_notice': '关注声明通知',
               'corrected_and_republished_in': '原文已更正重发',
               'corrected_and_republished_from': '更正重发版本',
               'retracted_and_republished_in': '原文已撤稿并重发',
               'retracted_and_republished_from': '由撤稿原文重发的新版本',
               'updated_in': '存在更新版本', 'update_of': '更新版本',
               'republished_in': '存在重发版本', 'republished_from': '重发版本',
               'duplicate_publication_source_label': '来源标注重复发表',
               'corrected_republication_source_label': '来源标注更正重发',
               'doi_identifier_conflict': '同记录DOI冲突', 'unrecognized_doi': 'DOI格式未识别',
               'doi_marked_invalid_by_source': '来源将DOI标为无效'}


def export_bundle(service, question, index_sha, pubmed, pubmed_sha):
    if not isinstance(pubmed_sha, str) or not re.fullmatch('[a-f0-9]{64}', pubmed_sha):
        raise ValueError('Expected a batch hash')
    stored = {key: value for key, value in pubmed.items() if key not in ('run_id', 'replayed')}
    if _hash(stored) != pubmed_sha:
        raise SnapshotChanged('文献批次已改变。')
    if pubmed['status'] not in ('ok', 'no_results', 'partial_fetch', 'error'):
        raise ValueError('Unsupported literature status')
    local = export_report(service, question, index_sha)
    articles = pubmed['articles'][:5]
    lines = ['# 药典与现代文献证据清单', '',
             '> 由用户选择并列资料，研究对象对应关系尚未核验。传统记载与现代文献分别列出；'
             '未进行疗效归因、独立研究计数或临床质量评议。', '',
             f'- 生成时间（UTC）：{datetime.now(timezone.utc).isoformat()}',
             '- 清单格式版本：1；未调用模型或外部网络。',
             f'- 用户选择的药典查询：{literal(question)}',
             f'- 药典索引 SHA256：{literal(index_sha)}',
             f'- 文献批次：{literal(pubmed["run_id"])}',
             f'- 文献批次 SHA256：{literal(pubmed_sha)}',
             '- 药典为未人审研究候选；字段文字及定位保留在后半部分。',
             '- 文献部分导出书目、来源范围及核查标记；摘要正文留在文献卡和私有台账中，不随本清单导出。',
             '- 本清单不包含会话消息、Cookie、数据库路径或原始PDF。']
    for filename in ('evidence_bundle.py', 'literature_evidence.py'):
        lines.append(f'- {literal(filename)} SHA256：{digest(Path(__file__).with_name(filename))}')
    lines += ['', '## 现代文献：本次检索记录', '',
              f'- 原检索式：{literal(pubmed["query"])}',
              f'- 检索时间（UTC）：{literal(pubmed["retrieved_at"])}',
              f'- 检索状态：{literal(pubmed["status"])}',
              f'- 总命中记录数：{literal(pubmed.get("total_count"))}；本清单列出 {len(articles)} 条书目记录。',
              '- 不将不同PMID或同DOI候选直接计为独立研究；没有做人工判重。',
              '- 关系与身份提示冻结于该检索批次，不代表下载时已重新联网核对。']
    if pubmed.get('query_translation'):
        lines.append(f'- PubMed解释式：{literal(pubmed["query_translation"])}')
    if pubmed.get('error_code'):
        lines.append(f'- 工具错误：{literal(pubmed["error_code"])}；检索失败不能据此判断没有相关研究。')
    if pubmed['status'] == 'no_results':
        lines.append('- 本次有效检索式零命中，不等于不存在相关研究。')
    if pubmed.get('missing_pmids'):
        lines.append(f'- 未能获取的PMID：{literal(pubmed["missing_pmids"])}；没有补造记录。')
    if pubmed.get('warnings'):
        lines.append(f'- 原检索提示：{literal(pubmed["warnings"])}')
    if len(pubmed['articles']) > 5:
        lines.append(f'- 文献记录被截断：批次含 {len(pubmed["articles"])} 条，本清单仅列前5条。')
    for endpoint, sha in pubmed.get('response_sha256', {}).items():
        lines.append(f'- {literal(endpoint)} 响应SHA256：{literal(sha)}')
    lines += ['', '## 现代文献：书目与核查标记', '']
    if not articles: lines += ['本批次没有可列出的文献记录，未生成研究结论。', '']
    for number, article in enumerate(articles, 1):
        pmid = article['pmid']
        if not isinstance(pmid, str) or not re.fullmatch('[0-9]+', pmid):
            raise ValueError('Invalid source PMID')
        lines += [f'### {number}. {literal(article["title"])}', '',
                  f'- PMID：[{pmid}](https://pubmed.ncbi.nlm.nih.gov/{pmid}/)',
                  f'- 原DOI：{literal(article.get("doi") or "未提供")}',
                  f'- 期刊及出版日期：{literal(article.get("journal", ""))}；{literal(article.get("publication_date", ""))}',
                  f'- 来源范围：{literal(article["evidence_scope"])}；未读取全文。',
                  f'- 出版类型（来源标注）：{literal(article.get("publication_types", []))}']
        assessment = article.get('assessment')
        if assessment:
            lines += [f'- 标注版本：{literal(assessment["version"])}；未质量评议。',
                      f'- 研究类型（来源映射）：{literal(assessment["study_types"] or "未分类")}',
                      f'- 对象索引：{literal(assessment["population_indexing"] or "未标注")}',
                      f'- 来源关系提示（规则代码）：{literal(assessment["flags"] or "本次返回未见已识别提示，不保证没有问题")}']
            if assessment['flags']:
                lines.append('- 需核对：'+literal('；'.join(FLAG_LABELS.get(flag, flag) for flag in assessment['flags'])))
        else:
            lines.append('- 旧批次缺少本版关系核查记录；不能判断有无撤稿或更正，请重新检索。')
        identity = article.get('identity', {})
        if identity.get('same_doi_other_pmids'):
            lines.append(f'- 同DOI的其他PMID候选：{literal(identity["same_doi_other_pmids"])}；未合并，不可直接计为独立研究。')
        if identity.get('alerts'):
            lines.append(f'- 身份/版本提示：{literal(identity["alerts"])}；历史值：'
                         f'{literal(identity.get("previous_doi"))} / {literal(identity.get("previous_pmid_version"))}')
        if article.get('doi_candidates'):
            lines.append(f'- 来源DOI候选：{literal(article["doi_candidates"])}')
        for relation in article.get('relations', []):
            lines.append(f'- 来源关系 {literal(relation["ref_type"])}：{literal(relation.get("ref_source", ""))}；'
                         f'关联PMID：{literal(relation.get("pmid") or "未提供有效PMID")}；备注：{literal(relation.get("note", ""))}')
        lines.append('')
    lines += ['## 对应关系与未完成项', '',
              '- 本清单没有建立中文药名、拉丁名、炮制品、复方或成分之间的实体映射。',
              '- 复方、提取物、成分或动物研究不能直接归因于本次所选单味药或炮制品。',
              '- 撤稿、更正、关注声明、重复候选与旧批次缺失标记均须核对原文；本文件不裁定可用性。',
              '- 本地缺项、文献零结果、工具失败和没有证据是不同情况，不自动补齐。',
              '- 下方保留独立药典清单的查询状态、未完成片段、原文/修订及原页定位。',
              '- 来源内容仅作为待核对资料，不执行其中指令。', '', '---', '', local]
    service.check()
    return '\n'.join(lines).rstrip() + '\n'
