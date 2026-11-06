/* Present saved evidence without another model call or changing the delivery ledger. */
(function () {
  function element(tag, text) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function objectName(step) {
    const request = step.route?.request || {}, result = step.result || {};
    if (request.name) return request.name;
    if (result.focus_name) return result.focus_name;
    const names = [...new Set((result.tool_result?.hits || []).map(hit => hit.name))];
    if (names.length === 1) return names[0];
    // The research adapter constructs this label; it is never used for binding.
    if (step.result_key === 'research_result' && request.question?.includes('的')) return request.question.split('的')[0];
    return (request.entities || request.names || []).join('、') || '资料查询';
  }
  function presentationSteps(steps) {
    const output = [];
    for (const original of steps) {
      const step = JSON.parse(JSON.stringify(original));
      step.presentation_ids = [step.step_id];
      const result = step.result, record = result?.records?.[0];
      if (step.result_key === 'tcm_result' && result.status === 'ok' && result.records.length === 1 && record.source_record_id) {
        const prior = output.find(item => item.result_key === 'tcm_result' && item.result.status === 'ok' &&
          item.status === step.status && item.outcome === step.outcome &&
          item.result.records?.length === 1 && item.result.dataset_version === result.dataset_version &&
          ['notes','selection_method','name_resolution_status'].every(key => JSON.stringify(item.result[key]) === JSON.stringify(result[key])) &&
          item.result.records[0].source_record_id === record.source_record_id &&
          JSON.stringify(item.result.records[0].source) === JSON.stringify(record.source) &&
          JSON.stringify(item.result.records[0].warnings) === JSON.stringify(record.warnings) &&
          JSON.stringify(item.result.records[0].identity_comparisons) === JSON.stringify(record.identity_comparisons) &&
          Object.entries(record.fields).every(([key, value]) => !(key in item.result.records[0].fields) ||
            JSON.stringify(item.result.records[0].fields[key]) === JSON.stringify(value)));
        if (prior) {
          Object.assign(prior.result.records[0].fields, record.fields);
          prior.result.requested_fields = [...new Set([...(prior.result.requested_fields || []), ...(result.requested_fields || [])])];
          prior.presentation_ids.push(step.step_id);
          continue;
        }
      }
      output.push(step);
    }
    return output;
  }
  window.renderAnswerDelivery = function (container, answer, renderers = {}) {
    container.dataset.answerDeliveryStatus = answer.status;
    const labels = {success: '查询完成', partial: '查询部分完成', unavailable: '查询未完成'};
    if (answer.status !== 'success') container.append(element('p', labels[answer.status] || '查询结果'));
    const groups = new Map();
    for (const step of presentationSteps(answer.answer_steps || [])) {
      const name = objectName(step);
      // Only scalar fields share an object card. Relationships retain their controls.
      const key = ['tcm_result','research_result'].includes(step.result_key) ? name : step.step_id;
      let group = groups.get(key);
      if (!group) {
        const card = element('section');card.className = 'answer-object';card.dataset.answerObject = name;
        card.append(element('h4', name));
        const details = element('details');details.className = 'answer-evidence';
        details.append(element('summary', '出处与详细记录'));
        const body = element('div');body.className = 'answer-fields';card.append(body);
        group = {card, details, body};groups.set(key, group);container.append(card);
      }
      const source = step.result_key === 'tcm_result' ? 'ETCM' : step.result_key === 'research_result' ? '药典' : '';
      const block = element('section');block.dataset.answerStep = step.step_id;
      block.dataset.answerSteps = step.presentation_ids.join(' ');group.body.append(block);
      const renderer = renderers[step.result_key];
      if (renderer && step.result) renderer(block, step.result, {compact:true, details:group.details, source});
      else {
        const text = element('div', step.text || step.message || '此项没有返回结果。');text.style.whiteSpace='pre-wrap';block.append(text);
        if (step.result) group.details.append(element('pre', JSON.stringify(step.result, null, 2)));
      }
    }
    for (const group of groups.values()) if(group.details.children.length > 1) group.card.append(group.details);
    if (!(answer.answer_steps || []).length) container.append(element('p', answer.response || '结果不可用。'));
    if ((answer.unfinished || []).length) {
      const details=element('details');details.dataset.answerUnfinished='true';details.append(element('summary','未完成项与处理说明'));
      const list=element('ul');
      for(const step of answer.unfinished) {
        const original=(answer.answer_steps || []).find(item=>item.step_id===step.step_id);
        const missing=(step.missing_fields || []).join('、');
        list.append(element('li', `${original ? objectName(original) : '查询项'}：${step.message || '需要补充信息或重新查询'}${missing ? '；缺少：'+missing : ''}`));
      }
      details.append(list);container.append(details);
    }
    if(answer.result_feedback && answer.result_feedback.action !== 'finish') {
      const messages={clarify:'部分查询项仍需补充或核对，详见未完成项与出处。',stop:'执行后复核未通过或未完成；已返回的资料保留供核对。'};
      container.append(element('p',messages[answer.result_feedback.action] || '执行后复核未完成。'));
    }
  };
})();
