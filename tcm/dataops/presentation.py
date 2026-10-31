"""Public execution metadata; never migrate stored checkpoints or artifacts."""
from copy import deepcopy


LOCAL_DRIVERS = {'research_fixed_field_policy', 'research_synthetic_observation_policy',
                 'synthetic_observation_policy'}


def selection_message(research):
    if research.get('task_mode', 'select_complete') != 'select_complete' or research.get('qualified_count') != 0:
        return None
    labels = {'missing': '未收录', 'not_inspected': '尚未核对', 'unmatched': '未找到对应条目',
              'source_mismatch': '来源版本不符', 'ambiguous': '有多个候选',
              'candidate_scope_value': '仅有饮片字段，本次要求药材正文',
              'ambiguous_field': '字段存在多个作用域值，尚未确定', 'scope_ambiguous': '作用域不明确'}
    lines = ['0 条满足本次字段条件；未以缺失值冒充齐全记录。']
    for item in research.get('candidate_states', []):
        if item.get('eligible'):
            continue
        reasons = '、'.join(f"{r['field']}：{labels.get(r['status'], r['status'])}"
                           for r in item.get('reasons', []))
        if not reasons:
            reasons = labels.get(item.get('status'), '现有记录未满足字段条件；历史记录未保存逐字段排除原因')
        lines.append(f"{item.get('name') or item['source_id']}：{reasons}")
    return '\n'.join(lines)


def workspace_view(document):
    doc = deepcopy(document)
    progress = doc.get('progress') or {}
    result = doc.get('result') or {}
    research = result.get('research') or {}
    driver = progress.get('driver')
    if not driver and result.get('execution_label'):
        driver = result['execution_label'].split(' / ', 1)[-1]
    if document['mode'] == 'fixed_local_F':
        mode, requests = 'fixed_local_F', 0
    else:
        mode = {'research_fixed_field_policy': 'research_fixed',
                'research_synthetic_observation_policy': 'research_synthetic',
                'synthetic_observation_policy': 'synthetic'}.get(driver)
        if mode is None:
            mode = ('research_native' if driver and driver.startswith('research_native_') else
                    'research_once_plan' if driver == 'research_once_plan_v1' else
                    'native' if driver == 'native_tool_calling' else 'execution_pending_or_unknown')
        # Missing/failed dispatch usage is unknown, never evidence of zero calls.
        requests = 0 if driver in LOCAL_DRIVERS else research.get('provider_requests')
    steps = progress.get('decision_steps', progress.get('model_calls', result.get('decision_steps', result.get('model_calls'))))
    doc['mode'] = mode
    message = selection_message(research)
    if message:
        doc['selection_message'] = message
    doc['execution'] = {'driver': driver, 'decision_steps': steps,
                        'provider_requests': requests,
                        'goal_provider_requests': doc.get('goal_understanding', {}).get('provider_requests', 0),
                        'queued_execution': bool(doc.get('job_id'))}
    for part in (doc.get('progress'), doc.get('result')):
        if part is not None:
            if 'model_calls' in part:
                part['decision_steps'] = part.pop('model_calls')
            part['provider_requests'] = requests
    return doc


def public_response(value):
    if isinstance(value, dict):
        if all(k in value for k in ('id', 'goal', 'source_revision', 'mode', 'created')):
            return workspace_view(value)
        if value.get('payload', {}).get('kind') == 'dataops':
            value = deepcopy(value)
            snapshot = value.get('snapshot') or {}
            for part in (snapshot, snapshot.get('result') or {}):
                if 'model_calls' in part:
                    part['decision_steps'] = part.pop('model_calls')
            limits = snapshot.get('execution_limits') or {}
            if 'model_calls' in limits:
                limits['decision_steps'] = limits.pop('model_calls')
        return {k: public_response(v) for k, v in value.items()}
    if isinstance(value, list):
        return [public_response(v) for v in value]
    return value
