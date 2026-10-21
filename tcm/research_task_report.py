"""Pure report formatter shared by the CLI and web service."""
import json

def markdown(task):
    r=task['report']
    if not r:raise ValueError('Task has no report yet')
    lines=['# '+('ETCM本地关系证据核查任务' if task.get('data_mode')=='etcm_local' else '合成资料关系证据核查任务'),'',r['notice'],'',f'- 状态：{task["status"]}',
           '- 来源：'+', '.join(r['source_ids']),'- 靶点：'+', '.join(r['selected_targets']),'',
           '## 数据库来源关系','']
    if r.get('snapshot'):
        lines += ['数据版本及目录指纹：'+json.dumps(r['snapshot'],ensure_ascii=False),'',
                  '来源文件及哈希：'+json.dumps(r['source_records'],ensure_ascii=False),'',
                  '来源声明的共同靶点总数：'+str(r['common_target_total']),'']
    for row in r['relation_evidence']:
        lines += ['- '+row['relation_id']+'：'+row['relation_scope']]
        lines += ['  - '+w['source_id']+' '+w['json_pointer'] for w in row['witnesses']]
        if task.get('data_mode')=='etcm_local' or task.get('plan',{}).get('intent')=='source_relations':
            lines += ['```json',json.dumps(row['witnesses'],ensure_ascii=False,indent=2),'```']
    if r.get('reference_leads'):
        leads=r['reference_leads']
        lines += ['','## 来源列出文献ID（未读取文献）','',leads['notice'],'```json',
                  json.dumps(leads['groups'],ensure_ascii=False,indent=2),'```']
    lines += ['','## 研究记录及对象范围','']
    for row in r['study_evidence']:
        lines += ['- ['+row['evidence_id']+'] '+row['classification']+'；'+row['source_locator'], '  '+row['text']]
    if task.get('plan'):
        lines += ['','## 用户确认的任务计划','','```json',json.dumps(task['plan'],ensure_ascii=False,indent=2),'```']
    if task.get('proposed_plan'):
        lines += ['','## 原规划提案（用户确认前）','','```json',json.dumps(task['proposed_plan'],ensure_ascii=False,indent=2),'```']
    if task.get('request_draft'):
        lines += ['','## 原始问句、自动草稿与用户核对','',
                  '完整性由用户核对清单声明；没有自动验证原始问句的全部语义。',
                  '```json',json.dumps({'question':task['question'],'draft':task['request_draft'],
                       'user_review':task.get('request_review')},ensure_ascii=False,indent=2),'```']
    if r.get('request_coverage'):
        lines += ['','## 原请求与确认执行范围','',
                  '确认范围完成：'+str(r['confirmed_scope_complete'])+('；确认清单全部完成：' if task.get('request_draft') else '；原请求全部完成：')+str(r['original_request_complete']),
                  '```json',json.dumps(r['request_coverage'],ensure_ascii=False,indent=2),'```']
    if r.get('completion'):
        lines += ['','## 本次任务完成范围','',json.dumps(r['completion'],ensure_ascii=False,indent=2)]
    if r.get('limitations'):
        lines += ['','## 数据与结论边界','',*('- '+x for x in r['limitations'])]
    lines += ['','## 未完成项','',*(('- '+g for g in r['gaps']) if r['gaps'] else ['本次本地查询要求已完成，不表示研究结论或疗效得到验证。' if task.get('data_mode')=='etcm_local' else '本合成任务预设的来源核查步骤已完成，不表示真实科研结论成立。'])]
    return '\n'.join(lines)+'\n'
