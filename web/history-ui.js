(() => {
  'use strict';

  const {dateText, el, tableMessageRow, text} = window.TaxpayerUI;
  const resolvedText = value => value === true ? '已直接解决' : value === false ? '未直接解决' : '状态待判断';

  const historyRow = (item, onOpenDetail) => {
    const row = el('tr'); row.tabIndex = 0;
    const subject = item.caller_type === '企业' ? `企业 · ${text(item.enterprise_identity, '细化主体待判断')}` : text(item.caller_type);
    const phone = el('td'); phone.append(el('strong', '', text(item.masked_phone)), el('small', '', dateText(item.call_time)));
    const question = el('td'); question.append(el('strong', '', text(item.core_question)), el('small', '', `${text(item.question_category)} · ${text(item.demand_category)}`));
    const resolved = el('td', '', resolvedText(item.resolved)); if (item.work_order) resolved.append(el('small', '', '历史工单'));
    row.append(phone, el('td', '', subject), question, resolved);
    row.addEventListener('click', () => onOpenDetail(item.business_id));
    return row;
  };

  function renderPage(data, onOpenDetail) {
    const body = document.querySelector('#history-body'); body.replaceChildren();
    if (!data.items.length) body.append(tableMessageRow('没有匹配的来电记录', 4));
    data.items.forEach(item => body.append(historyRow(item, onOpenDetail)));
    document.querySelector('#history-summary').textContent = `${data.filtered ? '当前号码' : '全部记录'} · 共 ${data.total} 条`;
    document.querySelector('#history-page-status').textContent = `第 ${data.page} / ${data.total_pages || 1} 页`;
    document.querySelector('#history-prev').disabled = data.page <= 1;
    document.querySelector('#history-next').disabled = data.page >= data.total_pages;
  }

  function renderPageError(message) {
    document.querySelector('#history-body').replaceChildren(tableMessageRow(`读取失败：${message}`, 4));
  }

  function detailSection(title, data, fields) {
    const section = el('section', 'detail-section'); section.append(el('div', 'detail-section-title', title));
    const grid = el('div', 'detail-grid');
    fields.forEach(([label, key, wide = false, formatter = text]) => { const item = el('div', `detail-field${wide ? ' wide' : ''}`); item.append(el('label', '', label), el('div', '', formatter(data?.[key]))); grid.append(item); });
    section.append(grid); return section;
  }

  function renderDetail(detail) {
    document.querySelector('#detail-title').textContent = `来电详情 · ${text(detail.original.business_id)}`;
    document.querySelector('#detail-content').replaceChildren(
      detailSection('重点分析信息', detail.extracted, [['核心问题', 'core_question', true], ['坐席答复提炼', 'agent_answer_summary', true], ['一级专题', 'topic_category'], ['二级专题', 'secondary_topic'], ['需求类别', 'demand_category'], ['解决情况', 'resolved', false, resolvedText], ['未解决原因', 'unresolved_reason', true], ['业务专业度', 'proficiency_level'], ['业务专业度依据', 'proficiency_basis', true], ['近期情绪状态', 'emotion_state'], ['近期情绪状态依据', 'emotion_basis', true], ['存在联系相关部门或人员且未解决', 'contact_unresolved', false, value => value ? '是' : '否'], ['服务不满', 'taxpayer_dissatisfied', false, value => value ? '是' : '否']]),
      detailSection('人工登记与原始信息', detail.original, [['业务内容', 'business_content', true], ['答复内容', 'answer_content', true], ['登记日期', 'registration_time'], ['通话开始', 'call_start_time'], ['通话结束', 'call_end_time'], ['坐席工号', 'agent_id'], ['坐席姓名', 'agent_name'], ['登记单位', 'registration_unit'], ['登记处理方式', 'handling_method'], ['业务类别', 'business_category'], ['满意度', 'satisfaction'], ['呼叫流水号', 'call_serial_number'], ['转写结果', 'transcript', true]]),
    );
  }

  window.TaxpayerHistory = Object.freeze({renderDetail, renderPage, renderPageError});
})();
