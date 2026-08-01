(() => {
  'use strict';

  const {dateText, el, text} = window.TaxpayerUI;
  const resolvedText = value => value === true ? '已直接解决' : value === false ? '未直接解决' : '状态待判断';
  const modeClasses = {emotion_response: 'emotion-response', matter_continuity: 'matter-continuity', information_delivery: 'information-delivery', 情绪响应: 'emotion-response', 业务应对: 'matter-continuity', 表达方式: 'information-delivery'};
  const compose = (node, ...children) => { node.append(...children); return node; };
  function formatIssueNarrative(fragments) {
    const items = fragments.map(value => String(value ?? '').trim().replace(/[；;。！？!?]+([”’）】\]]*)$/u, '$1')).filter(Boolean);
    return items.length ? `${items.join('；')}。` : '暂无可展示的历史跟进信息。';
  }
  const metric = (parent, label, value) => parent.append(compose(el('div', 'metric'), el('span', '', label), el('b', '', text(value, '0'))));
  const identity = (parent, label, value, wide = false) => parent.append(compose(el('div', `identity-detail${wide ? ' wide' : ''}`), el('label', '', label), el('span', '', text(value))));

  function renderProfile(profile, onOpenDetail) {
    const box = document.querySelector('#overview');
    box.replaceChildren();
    const subject = profile.caller_type === '企业'
      ? (profile.enterprise_identity && profile.enterprise_identity !== '无法判断' ? `企业 · ${profile.enterprise_identity}` : '企业（细化主体暂无法判断）')
      : text(profile.caller_type, '咨询主体暂无法判断');
    const details = el('div', 'identity-details');
    [['最近咨询', 'latest_question', true], ['最近坐席答复', 'latest_agent_answer', true], ['知识库参考回答', 'standard_answer', true], ['最近来电时间', 'latest_call_time'], ['登记单位', 'latest_registration_unit'], ['专题类别', 'latest_topic_category'], ['需求类别', 'latest_demand_category']].forEach(([label, key, wide]) => identity(details, label, profile[key], wide));
    const head = el('div', 'identity-head');
    const tags = el('div', 'category-pills');
    tags.append(el('span', 'category-pill', `业务专业度 · ${text(profile.proficiency_level, '暂无法判断')}`), el('span', 'category-pill', `近期情绪状态 · ${text(profile.emotion_state, '暂无法判断')}`));
    head.append(el('strong', '', subject), tags);
    box.append(compose(el('div', 'caller-identity'), head, details));
    const recent = profile.recent_workday_statistics || {};
    const metrics = el('div', 'metrics');
    [['历史来电', 'call_count'], ['重复诉求', 'repeated_issue_count'], ['历史工单', 'work_order_count'], ['存在联系相关部门或人员且未解决', 'contact_unresolved_count'], ['服务不满', 'dissatisfaction_count'], ['未直接解决', 'unresolved_count']].forEach(([label, key]) => metric(metrics, label, recent[key]));
    box.append(metrics);
    document.querySelector('#overview-range').textContent = recent.start_date && recent.end_date ? `${recent.start_date} 至 ${recent.end_date} · 最近5个工作日 · 仅供参考` : '最近5个工作日 · 仅供参考';
    document.querySelector('#overview-panel').classList.remove('hidden');
    renderHistoryFocus(profile, onOpenDetail);
    renderCallerHistory(profile, onOpenDetail);
  }

  function renderHistoryFocus(profile, onOpenDetail) {
    const box = document.querySelector('#profile');
    box.className = 'panel-body';
    box.replaceChildren();
    const groups = profile.history_focus || {};
    [['重复诉求', 'repeated_issues', '当前没有已确认的重复诉求。'], ['历史工单', 'work_orders', '当前没有历史工单记录。'], ['存在联系相关部门或人员且未解决', 'contact_unresolved', '当前没有联系相关部门或人员且未解决的记录。'], ['服务不满', 'dissatisfaction', '当前没有对坐席或本通服务不满的记录。'], ['未直接解决', 'unresolved', '当前没有未直接解决记录。']].forEach(([title, key, empty]) => {
      const rows = groups[key] || [];
      const section = el('section', 'issue-section');
      const list = el('div', 'issue-list');
      rows.length ? rows.slice(0, 3).forEach(item => list.append(issueCard(item, title, onOpenDetail))) : list.append(el('div', 'issue-empty', empty));
      section.append(compose(el('div', 'issue-section-head'), el('strong', '', title), el('span', 'issue-count', `${rows.length} 项`)), list);
      box.append(section);
    });
    document.querySelector('#view-current-history').classList.remove('hidden');
  }

  function issueCard(item, label, onOpenDetail) {
    const card = el('div', 'issue-card');
    const meta = el('div', 'issue-meta');
    meta.append(el('span', 'issue-tag', label), el('span', '', dateText(item.call_time)));
    if (item.registration_unit) meta.append(el('span', '', `· ${item.registration_unit}`));
    const facts = [];
    if (item.is_repeated_issue) facts.push(item.matched_previous_question ? `已确认与${item.matched_previous_call_time ? `${dateText(item.matched_previous_call_time)}的` : ''}历史事项“${text(item.matched_previous_question)}”重复` : '已确认与既往来电属于同一事项，可查看本通与历史记录核对具体内容');
    if (item.work_order) facts.push('该通形成工单');
    if (item.contact_unresolved) facts.push('存在联系相关部门或人员且未解决');
    if (item.taxpayer_dissatisfied) facts.push('来电人对当前坐席或本通服务表达不满');
    if (item.resolved === false) facts.push(`未直接解决：${text(item.unresolved_reason, '原因未形成明确记录')}`);
    facts.push(`当前记录：${resolvedText(item.resolved)}`);
    const action = el('button', 'issue-action', '查看该通来电证据 →');
    action.type = 'button';
    action.addEventListener('click', () => onOpenDetail(item.business_id));
    card.append(meta, el('div', 'issue-question', text(item.core_question, '该次咨询事项未形成明确记录')), el('div', 'issue-reason', formatIssueNarrative(facts)), action);
    return card;
  }

  function renderCallerHistory(profile, onOpenDetail) {
    const trajectories = Array.isArray(profile.trajectories) ? profile.trajectories : [];
    const counts = {resolved: trajectories.filter(item => item.resolved === true).length, unresolved: trajectories.filter(item => item.resolved === false).length, unknown: trajectories.filter(item => item.resolved !== true && item.resolved !== false).length};
    document.querySelector('#caller-history-summary').textContent = `历史来电 ${trajectories.length} 次 · 已直接解决 ${counts.resolved} · 未直接解决 ${counts.unresolved} · 待判断 ${counts.unknown}`;
    const box = document.querySelector('#caller-history');
    box.replaceChildren();
    if (!trajectories.length) { box.append(el('div', 'placeholder', '暂无可展示的历史来电。')); return; }
    const list = el('div', 'caller-history-list');
    list.append(compose(el('div', 'caller-history-header'), el('span', '', '来电时间 / 结果'), el('span', '', '咨询事项与处理摘要'), el('span', '', '服务线索'), el('span', '', '')));
    trajectories.forEach(item => {
      const row = el('article', `caller-history-row ${item.resolved === false ? 'unresolved' : ''}`);
      const meta = compose(el('div', 'caller-history-meta'), el('time', '', dateText(item.call_time)), el('span', `history-status ${item.resolved === false ? 'unresolved' : item.resolved === true ? 'resolved' : 'unknown'}`, resolvedText(item.resolved)));
      const summary = item.resolved === false ? text(item.unresolved_reason, '未解决原因暂未记录') : text(item.agent_answer_summary, item.resolved === true ? '已直接解决，未形成答复提炼。' : '处理结果待进一步判断。');
      const content = compose(el('div', 'caller-history-content'), el('strong', '', text(item.core_question || item.question_category, '问题待归类')), el('p', '', summary));
      const facts = el('div', 'caller-history-facts');
      [['work_order', '历史工单'], ['contact_unresolved', '存在联系相关部门或人员且未解决'], ['is_repeated_issue', '重复诉求'], ['abnormal_end', '异常中断']].forEach(([key, label]) => item[key] && facts.append(el('span', '', label)));
      const action = el('button', 'button secondary compact', '详情');
      action.type = 'button';
      action.addEventListener('click', () => onOpenDetail(item.business_id));
      row.append(meta, content, facts, action);
      list.append(row);
    });
    box.append(list);
  }

  function renderAdvice(advice) {
    const box = document.querySelector('#advice');
    box.className = 'panel-body';
    box.replaceChildren();
    const mode = compose(el('section', 'advice-strategy'), el('h3', '', '组合接待策略'));
    const cards = el('div', 'strategy-grid');
    (advice.service_modes || []).forEach(component => {
      const className = modeClasses[component?.category_id] || modeClasses[component?.category] || '';
      cards.append(compose(el('article', `strategy-card ${className}`.trim()), el('span', '', text(component.category, '策略分项')), el('strong', '', text(component.mode)), el('p', '', text(component.basis, '依据当前历史信息确定。'))));
    });
    if (!cards.children.length) cards.append(el('div', 'strategy-card', text(advice.service_mode, '当前诉求确认')));
    mode.append(cards);
    box.append(mode, el('h3', '', '总体接待建议'), el('div', 'advice-summary', text(advice.advice_summary)));
    const badge = document.querySelector('#advice-badge');
    badge.classList.remove('hidden');
    badge.textContent = advice.generation_status === 'model_generated' ? '智能实时建议' : '系统辅助建议';
    badge.className = `badge${advice.generation_status === 'model_generated' ? '' : ' fallback'}`;
  }

  function renderMissingProfile() {
    document.querySelector('#overview-panel').classList.add('hidden');
    document.querySelector('#caller-history-overlay').classList.add('hidden');
    const box = document.querySelector('#profile');
    box.className = 'panel-body placeholder';
    box.textContent = '该号码暂无历史来电记录，本次按首次接待方式服务。';
  }

  window.TaxpayerWorkbench = Object.freeze({renderAdvice, renderMissingProfile, renderProfile});
})();
