/* Shared, text-only rendering of persisted query deliveries. */
(function () {
  function element(tag, text) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    return node;
  }
  window.renderAnswerDelivery = function (container, answer, renderers = {}) {
    container.dataset.answerDeliveryStatus = answer.status;
    const labels = {success: '查询完成', partial: '查询部分完成', unavailable: '查询未完成'};
    container.append(element('p', labels[answer.status] || '查询结果'));
    for (const step of answer.answer_steps || []) {
      const block = element('section');
      block.dataset.answerStep = step.step_id;
      const request = step.route?.request || {};
      const name = request.name || request.question || (request.names || []).join('、') || '资料查询';
      block.append(element('h4', name));
      const renderer = renderers[step.result_key];
      if (renderer && step.result) {
        renderer(block, step.result);container.append(block);continue;
      }
      const text = element('div', step.text || step.message || '此项没有返回结果。');
      text.style.whiteSpace = 'pre-wrap';
      block.append(text);
      if (step.result) {
        const details = element('details');
        details.append(element('summary', '出处与原始记录'));
        const original = element('pre', JSON.stringify(step.result, null, 2));
        original.style.whiteSpace = 'pre-wrap';
        original.style.overflowWrap = 'anywhere';
        details.append(original); block.append(details);
      }
      container.append(block);
    }
    if (!(answer.answer_steps || []).length) container.append(element('p', answer.response || '结果不可用。'));
    if ((answer.unfinished || []).length) {
      container.append(element('p', '未完成项'));
      const list = element('ul');
      for (const step of answer.unfinished) {
        const missing = (step.missing_fields || []).join('、');
        list.append(element('li', `${step.step_id}：${step.message || '需要补充信息或重新查询'}${missing ? '；缺少：' + missing : ''}`));
      }
      container.append(list);
    }
  };
})();
