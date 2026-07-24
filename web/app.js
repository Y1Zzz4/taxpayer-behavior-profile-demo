(() => {
  'use strict';

  const text = (value, fallback = '暂无记录') => value === null || value === undefined || value === '' ? fallback : String(value);
  const dateText = value => value ? String(value).replace('T', ' ').slice(0, 16) : '时间未记录';
  const resolvedText = value => value === true ? '已直接解决' : value === false ? '未直接解决' : '状态待判断';
  const el = (tag, className = '', content) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (content !== undefined) node.textContent = content;
    return node;
  };
  // Table messages may contain API errors, so they must always enter the DOM
  // as text rather than through an HTML parsing sink.
  function tableMessageRow(message, columnCount) {
    const row = el('tr');
    const cell = el('td', 'table-empty', message);
    cell.colSpan = columnCount;
    row.append(cell);
    return row;
  }
  function selectOption(value, label) {
    const option = el('option', '', label);
    option.value = value;
    return option;
  }
  function formatIssueNarrative(fragments) {
    // Model and manually entered reasons may already end in a sentence mark.
    // Normalize each independent clause before the UI adds its own separators,
    // so punctuation remains correct regardless of the source wording.
    const normalized = fragments
      .map(fragment => String(fragment ?? '').trim().replace(/[；;。！？!?]+([”’）】\]]*)$/u, '$1'))
      .filter(Boolean);
    return normalized.length ? `${normalized.join('；')}。` : '暂无可展示的历史跟进信息。';
  }
  const pages = new Set(['workbench', 'dashboard', 'history', 'showcase', 'users']);
  const titles = {workbench: '12366坐席接待助手', dashboard: '画像数据概览', history: '历史来电记录', showcase: '画像推演中心', users: '用户与权限'};
  const state = {
    user: null,
    phone: '',
    dashboardLoaded: false,
    history: {page: 1, totalPages: 0, phone: '', loaded: false},
    showcaseCatalog: null,
    knowledgeSearchRequest: 0,
    graphCleanup: null,
  };
  const modeClassByCategory = {
    emotion_response: 'emotion-response',
    matter_continuity: 'matter-continuity',
    information_delivery: 'information-delivery',
    情绪响应: 'emotion-response',
    业务应对: 'matter-continuity',
    表达方式: 'information-delivery',
  };
  const modeClass = component => modeClassByCategory[component?.category_id] || modeClassByCategory[component?.category] || '';
  function appendModeTags(parent, components, fallback = '') {
    if (!Array.isArray(components) || !components.length) {
      if (fallback) parent.append(el('span', 'mode-tag', fallback));
      return;
    }
    components.forEach(component => {
      const label = component.category ? `${component.category} · ${component.mode}` : text(component.mode);
      parent.append(el('span', `mode-tag ${modeClass(component)}`.trim(), label));
    });
  }

  async function api(url, options) {
    const response = await fetch(url, options);
    let body = {};
    try { body = await response.json(); } catch (_) { /* keep generic error */ }
    if (!response.ok) {
      if (response.status === 401 && url !== '/api/auth/login') showLogin();
      throw new Error(body.error || '请求失败');
    }
    return body;
  }

  function showLogin(message = '') {
    state.user = null;
    document.querySelector('#login-screen').classList.remove('hidden');
    document.querySelector('#login-error').textContent = message;
  }

  function enterApp(user) {
    state.user = user;
    document.querySelector('#login-screen').classList.add('hidden');
    document.querySelector('#user-name').textContent = user.display_name;
    document.querySelector('#user-role').textContent = user.role_label;
    document.querySelector('#user-avatar').textContent = user.role === 'admin' ? '管' : '坐';
    document.querySelectorAll('[data-admin-only]').forEach(node => node.classList.toggle('hidden', user.role !== 'admin'));
    const requested = location.hash.slice(1) || 'workbench';
    showPage(user.role !== 'admin' && ['showcase', 'users'].includes(requested) ? 'workbench' : requested, false);
  }

  async function restoreSession() {
    try {
      const result = await api('/api/auth/me');
      enterApp(result.user);
    } catch (_) {
      showLogin();
    }
  }

  document.querySelector('#login-form').addEventListener('submit', async event => {
    event.preventDefault();
    const button = document.querySelector('#login-submit');
    button.disabled = true;
    document.querySelector('#login-error').textContent = '';
    try {
      const result = await api('/api/auth/login', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({username: document.querySelector('#login-username').value.trim(), password: document.querySelector('#login-password').value})
      });
      document.querySelector('#login-password').value = '';
      enterApp(result.user);
    } catch (error) {
      document.querySelector('#login-error').textContent = error.message;
    } finally { button.disabled = false; }
  });

  document.querySelector('#logout').addEventListener('click', async () => {
    try { await api('/api/auth/logout', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'}); } catch (_) { /* cookie still expires server-side when possible */ }
    showLogin('已安全退出系统。');
  });

  function showPage(name, updateHash = true) {
    let page = pages.has(name) ? name : 'workbench';
    if (state.user?.role !== 'admin' && ['showcase', 'users'].includes(page)) page = 'workbench';
    document.querySelectorAll('.page-view').forEach(node => node.classList.toggle('hidden', node.id !== `page-${page}`));
    document.querySelectorAll('.nav-link').forEach(node => {
      const active = node.dataset.page === page;
      node.classList.toggle('active', active);
      active ? node.setAttribute('aria-current', 'page') : node.removeAttribute('aria-current');
    });
    document.title = `${titles[page]}｜12366坐席服务辅助系统`;
    if (updateHash) location.hash = page;
    if (page === 'dashboard' && !state.dashboardLoaded) loadDashboard();
    if (page === 'history' && !state.history.loaded) loadHistory(1);
    if (page === 'showcase' && !state.showcaseCatalog) loadShowcaseCatalog();
    if (page === 'users') loadUsers();
    if (page !== 'showcase' && state.graphCleanup) { state.graphCleanup(); state.graphCleanup = null; }
    window.scrollTo({top: 0, behavior: 'smooth'});
  }
  document.querySelectorAll('.nav-link').forEach(node => node.addEventListener('click', () => showPage(node.dataset.page)));
  window.addEventListener('hashchange', () => state.user && showPage(location.hash.slice(1), false));

  function metric(parent, label, value) {
    const item = el('div', 'metric');
    item.append(el('span', '', label), el('b', '', text(value, '0')));
    parent.append(item);
  }

  function identityDetail(parent, label, value, wide = false) {
    const item = el('div', `identity-detail${wide ? ' wide' : ''}`);
    item.append(el('label', '', label), el('span', '', text(value)));
    parent.append(item);
  }

  function renderOverview(profile) {
    const box = document.querySelector('#overview');
    box.replaceChildren();
    const identity = el('div', 'caller-identity');
    const heading = el('div', 'identity-head');
    const subject = profile.caller_type === '企业'
      ? (profile.enterprise_identity && profile.enterprise_identity !== '无法判断' ? `企业 · ${profile.enterprise_identity}` : '企业（细化主体暂无法判断）')
      : text(profile.caller_type, '咨询主体暂无法判断');
    heading.append(el('strong', '', subject));
    const tags = el('div', 'category-pills');
    tags.append(el('span', 'category-pill', `业务专业度 · ${text(profile.proficiency_level, '暂无法判断')}`));
    tags.append(el('span', 'category-pill', `近期情绪状态 · ${text(profile.emotion_state, '暂无法判断')}`));
    heading.append(tags);
    const details = el('div', 'identity-details');
    identityDetail(details, '最近咨询', profile.latest_question, true);
    identityDetail(details, '最近坐席答复', profile.latest_agent_answer, true);
    identityDetail(details, '知识库参考回答', profile.standard_answer, true);
    identityDetail(details, '最近来电时间', profile.latest_call_time);
    identityDetail(details, '登记单位', profile.latest_registration_unit);
    identityDetail(details, '专题类别', profile.latest_topic_category);
    identityDetail(details, '需求类别', profile.latest_demand_category);
    identity.append(heading, details);
    const recent = profile.recent_workday_statistics || {};
    const metrics = el('div', 'metrics');
    metric(metrics, '历史来电', recent.call_count);
    metric(metrics, '重复诉求', recent.same_demand_count);
    metric(metrics, '历史工单', recent.work_order_count);
    metric(metrics, '等待推诿', recent.wait_pushback_count);
    metric(metrics, '服务不满', recent.dissatisfaction_count);
    metric(metrics, '未直接解决', recent.unresolved_count);
    box.append(identity, metrics);
    document.querySelector('#overview-range').textContent = recent.start_date && recent.end_date
      ? `${recent.start_date} 至 ${recent.end_date} · 最近5个工作日 · 仅供参考`
      : '最近5个工作日 · 仅供参考';
    document.querySelector('#overview-panel').classList.remove('hidden');
  }

  function issueCard(item, label) {
    const card = el('div', 'issue-card');
    const meta = el('div', 'issue-meta');
    meta.append(el('span', 'issue-tag', label), el('span', '', dateText(item.call_time)));
    if (item.registration_unit) meta.append(el('span', '', `· ${item.registration_unit}`));
    card.append(meta, el('div', 'issue-question', text(item.core_question, '该次咨询事项未形成明确记录')));
    const facts = [];
    if (item.is_repeated_issue) {
      const question = text(item.matched_previous_question);
      const callTime = item.matched_previous_call_time ? dateText(item.matched_previous_call_time) : '';
      if (question) {
        facts.push(`已确认与${callTime ? `${callTime}的` : ''}历史事项“${question}”重复`);
      } else {
        facts.push('已确认与既往来电属于同一事项，可查看本通与历史记录核对具体内容');
      }
    }
    if (item.work_order) facts.push('该通形成工单');
    if (item.wait_pushback) facts.push('同时出现等待表述和潜在推诿');
    if (item.taxpayer_dissatisfied) facts.push('来电人对当前坐席或本通服务表达不满');
    if (item.resolved === false) facts.push(`未直接解决：${text(item.unresolved_reason, '原因未形成明确记录')}`);
    facts.push(`当前记录：${resolvedText(item.resolved)}`);
    card.append(el('div', 'issue-reason', formatIssueNarrative(facts)));
    const action = el('button', 'issue-action', '查看该通来电证据 →');
    action.type = 'button'; action.addEventListener('click', () => openDetail(item.business_id)); card.append(action);
    return card;
  }

  function issueSection(parent, title, rows, empty) {
    const section = el('section', 'issue-section');
    const head = el('div', 'issue-section-head');
    head.append(el('strong', '', title), el('span', 'issue-count', `${rows.length} 项`));
    const list = el('div', 'issue-list');
    rows.length ? rows.slice(0, 3).forEach(item => list.append(issueCard(item, title))) : list.append(el('div', 'issue-empty', empty));
    section.append(head, list); parent.append(section);
  }

  function renderHistoryFocus(profile) {
    const box = document.querySelector('#profile');
    box.className = 'panel-body'; box.replaceChildren();
    const groups = profile.history_focus || {};
    issueSection(box, '重复诉求', groups.same_demand || [], '当前没有已确认的重复诉求。');
    issueSection(box, '历史工单', groups.work_orders || [], '当前没有历史工单记录。');
    issueSection(box, '等待推诿', groups.wait_pushback || [], '当前没有同时命中等待和潜在推诿的记录。');
    issueSection(box, '服务不满', groups.dissatisfaction || [], '当前没有对坐席或本通服务不满的记录。');
    issueSection(box, '未直接解决', groups.unresolved || [], '当前没有未直接解决记录。');
    document.querySelector('#view-current-history').classList.remove('hidden');
  }

  function bulletSection(parent, title, values) {
    if (!values || !values.length) return;
    const section = el('section', 'advice-section'); section.append(el('h3', '', title));
    const list = el('ul'); values.forEach(value => list.append(el('li', '', value))); section.append(list); parent.append(section);
  }

  function renderAdvice(advice) {
    const box = document.querySelector('#advice'); box.className = 'panel-body'; box.replaceChildren();
    const mode = el('div', 'advice-mode'); mode.append(el('span', '', '组合接待策略')); const modeTags = el('div', 'mode-tags'); appendModeTags(modeTags, advice.service_modes, text(advice.service_mode, '通俗引导 · 平稳接待 · 当前诉求确认')); mode.append(modeTags); box.append(mode);
    box.append(el('div', 'advice-summary', text(advice.advice_summary)));
    bulletSection(box, '接待重点', advice.service_focus);
    const details = el('details', 'advice-details');
    details.append(el('summary', '', '查看建议详情'));
    const detailBody = el('div', 'advice-details-body');
    bulletSection(detailBody, '历史事项提醒', advice.history_followups);
    bulletSection(detailBody, '服务关注事项', advice.risk_reminders);
    [['模式落实', advice.mode_application], ['开场衔接', advice.opening_strategy], ['沟通方式', advice.communication_style]].forEach(([label, value]) => {
      const item = el('div', 'advice-detail'); item.append(el('span', '', label), el('p', '', text(value))); detailBody.append(item);
    });
    bulletSection(detailBody, '需要避免', advice.avoid_actions);
    bulletSection(detailBody, '参考依据', advice.evidence);
    details.append(detailBody); box.append(details);
    const badge = document.querySelector('#advice-badge'); badge.classList.remove('hidden');
    badge.textContent = advice.generation_status === 'model_generated' ? '智能实时建议' : '系统辅助建议';
    badge.className = `badge${advice.generation_status === 'model_generated' ? '' : ' fallback'}`;
  }

  document.querySelector('#lookup-form').addEventListener('submit', async event => {
    event.preventDefault();
    const phone = document.querySelector('#phone').value.trim();
    const notice = document.querySelector('#notice');
    if (!phone) { notice.textContent = '请输入来电号码。'; notice.className = 'notice error'; return; }
    const button = document.querySelector('#submit'); button.disabled = true; notice.textContent = '正在调取历史画像并生成接待建议…'; notice.className = 'notice';
    try {
      const result = await api('/api/profile', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({phone})});
      state.phone = phone;
      if (result.found) { renderOverview(result.profile); renderHistoryFocus(result.profile); }
      else {
        document.querySelector('#overview-panel').classList.add('hidden');
        const profileBox = document.querySelector('#profile'); profileBox.className = 'panel-body placeholder'; profileBox.textContent = '该号码暂无历史来电记录，本次按首次接待方式服务。';
      }
      const advice = await api('/api/advice', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({phone})});
      renderAdvice(advice.advice); notice.textContent = result.found ? '历史信息已调取，接待建议已实时生成。' : '未匹配到历史信息，已生成保守接待建议。';
    } catch (error) { notice.textContent = `读取失败：${error.message}`; notice.className = 'notice error'; }
    finally { button.disabled = false; }
  });

  document.querySelector('#view-current-history').addEventListener('click', () => {
    document.querySelector('#history-phone').value = state.phone; state.history.phone = state.phone; state.history.loaded = false; showPage('history');
  });

  const colors = ['#596fd8', '#28a394', '#dc6b76', '#dda13d', '#7b61c4', '#4f91c7', '#df8454'];
  function renderDonut(target, rows, palette = colors, centerLabel = '来电记录') {
    target.replaceChildren(); const values = rows || []; const total = values.reduce((sum, item) => sum + Number(item.value || 0), 0);
    if (!total) { target.append(el('div', 'empty-chart', '暂无可展示数据')); return; }
    let offset = 0; const parts = values.map((item, index) => { const start = offset; offset += item.value / total * 360; return `${palette[index % palette.length]} ${start}deg ${offset}deg`; });
    const layout = el('div', 'donut-layout'); const donut = el('div', 'donut'); donut.style.background = `conic-gradient(${parts.join(',')})`;
    const center = el('div', 'donut-center'); center.append(el('strong', '', total), el('span', '', centerLabel)); donut.append(center);
    const legend = el('div', 'chart-legend'); values.forEach((item, index) => {
      const row = el('div', 'legend-row'); row.tabIndex = 0;
      const dot = el('i', 'legend-swatch'); dot.style.background = palette[index % palette.length];
      const value = el('strong', 'legend-value', item.value); value.append(el('span', 'legend-percent', `${Math.round(item.value / total * 100)}%`));
      row.append(dot, el('span', 'legend-label', item.label), value); legend.append(row);
    });
    layout.append(donut, legend); target.append(layout);
  }
  function renderTrend(target, rows) {
    target.replaceChildren(); const values = (rows || []).map(item => ({...item, value: Number(item.value || 0)}));
    if (!values.length) { target.append(el('div', 'empty-chart', '暂无可展示数据')); return; }
    const ns = 'http://www.w3.org/2000/svg'; const svgNode = (tag, attrs = {}) => { const node = document.createElementNS(ns, tag); Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value)); return node; };
    const width = 760, height = 225, left = 42, right = 16, top = 14, bottom = 38;
    const plotWidth = width - left - right, plotHeight = height - top - bottom;
    const rawMax = Math.max(...values.map(item => item.value), 1); const max = Math.max(4, Math.ceil(rawMax / 4) * 4);
    const x = index => left + (values.length === 1 ? plotWidth / 2 : plotWidth * index / (values.length - 1));
    const y = value => top + plotHeight - value / max * plotHeight;
    const shell = el('div', 'line-chart-shell'); const svg = svgNode('svg', {class: 'line-chart', viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': '来电量趋势折线图'});
    const defs = svgNode('defs'); const gradient = svgNode('linearGradient', {id: 'callArea', x1: '0', y1: '0', x2: '0', y2: '1'}); gradient.append(svgNode('stop', {offset: '0%', 'stop-color': '#6578df', 'stop-opacity': '.28'}), svgNode('stop', {offset: '100%', 'stop-color': '#6578df', 'stop-opacity': '.02'})); defs.append(gradient); svg.append(defs);
    for (let step = 0; step <= 4; step += 1) {
      const value = max * step / 4; const lineY = y(value); svg.append(svgNode('line', {class: 'line-grid', x1: left, x2: width - right, y1: lineY, y2: lineY}));
      const label = svgNode('text', {class: 'line-axis-text', x: left - 9, y: lineY + 3, 'text-anchor': 'end'}); label.textContent = Math.round(value); svg.append(label);
    }
    const pointList = values.map((item, index) => `${x(index)},${y(item.value)}`).join(' ');
    const area = svgNode('path', {class: 'line-area', d: `M ${x(0)} ${top + plotHeight} L ${pointList.replaceAll(',', ' ')} L ${x(values.length - 1)} ${top + plotHeight} Z`});
    const line = svgNode('polyline', {class: 'line-path', points: pointList}); svg.append(area, line);
    values.forEach((item, index) => {
      const dot = svgNode('circle', {class: 'line-dot', cx: x(index), cy: y(item.value), r: 4, tabindex: 0}); const title = svgNode('title'); title.textContent = `${item.date || item.label}：${item.value}通`; dot.append(title); svg.append(dot);
      const label = svgNode('text', {class: 'line-axis-text', x: x(index), y: height - 13, 'text-anchor': 'middle'}); label.textContent = item.label; svg.append(label);
    });
    shell.append(svg); target.append(shell);
  }
  function renderUnitResolution(target, rows) {
    target.replaceChildren(); const values = (rows || []).filter(item => Number(item.total || 0) > 0);
    if (!values.length) { target.append(el('div', 'empty-chart', '暂无可展示数据')); return; }
    const ns = 'http://www.w3.org/2000/svg'; const svgNode = (tag, attrs = {}) => { const node = document.createElementNS(ns, tag); Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value)); return node; };
    const width = Math.max(820, values.length * 76), height = 315, left = 54, right = 54, top = 24, bottom = 78; const plotWidth = width - left - right, plotHeight = height - top - bottom;
    const step = plotWidth / values.length; const barWidth = Math.min(34, step * .5); const rateY = value => top + plotHeight - Number(value || 0) / 100 * plotHeight;
    const shell = el('div', 'combo-chart-shell'); const legend = el('div', 'combo-legend'); [['combo-bar-key', '问题解决率'], ['combo-line-key', '解决率趋势']].forEach(([className, label]) => { const item = el('span'); item.append(el('i', className), document.createTextNode(label)); legend.append(item); });
    const svg = svgNode('svg', {class: 'combo-chart', viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': '各登记单位问题解决率对比与趋势'}); svg.style.width = `${width}px`;
    for (let index = 0; index <= 4; index += 1) { const y = top + plotHeight - plotHeight * index / 4; svg.append(svgNode('line', {class:'line-grid', x1:left, x2:width-right, y1:y, y2:y})); const rate = svgNode('text', {class:'line-axis-text', x:left-9, y:y+3, 'text-anchor':'end'}); rate.textContent = `${index * 25}%`; svg.append(rate); }
    const points = [];
    values.forEach((item, index) => { const x = left + step * (index + .5), rate = item.resolved_rate; const hasRate = rate !== null && rate !== undefined; const y = rateY(hasRate ? rate : 0); const bar = svgNode('rect', {class:`combo-bar${hasRate ? '' : ' unknown'}`, x:x-barWidth/2, y, width:barWidth, height:top+plotHeight-y, rx:5}); const barTitle = svgNode('title'); barTitle.textContent = hasRate ? `${item.label}：解决率 ${rate}%` : `${item.label}：暂无已判定记录`; bar.append(barTitle); svg.append(bar); if (hasRate) points.push({x, y, item}); const label = svgNode('text', {class:'combo-axis-label', x, y:height-57, transform:`rotate(-36 ${x} ${height-57})`, 'text-anchor':'end'}); label.textContent = item.label; svg.append(label); });
    if (points.length) { const line = svgNode('polyline', {class:'combo-line', points:points.map(point => `${point.x},${point.y}`).join(' ')}); svg.append(line); points.forEach(point => { const dot = svgNode('circle', {class:'combo-dot', cx:point.x, cy:point.y, r:4, tabindex:0}); const rateLabel = svgNode('text', {class:'combo-rate-label', x:point.x, y:Math.max(top + 10, point.y - 10), 'text-anchor':'middle'}); rateLabel.textContent = `${point.item.resolved_rate}%`; const title = svgNode('title'); title.textContent = `${point.item.label}：解决率 ${point.item.resolved_rate}%`; dot.append(title); svg.append(rateLabel, dot); }); }
    shell.append(legend, svg); target.append(shell);
  }
  function renderStacked(target, rows, drilldown = false) {
    target.replaceChildren(); const values = rows || []; const total = values.reduce((sum, item) => sum + item.value, 0);
    if (!values.length) { target.append(el('div', 'empty-chart', '暂无可展示数据')); return; }
    const legend = el('div', 'stacked-legend'); [['legend-resolved', '已解决'], ['legend-unresolved', '未直接解决'], ['legend-unknown', '待判断']].forEach(([className, label]) => { const item = el('span'); item.append(el('i', className), document.createTextNode(label)); legend.append(item); });
    const list = el('div', 'stacked-list'); values.forEach(item => {
      const value = Number(item.value || 0); const share = item.share ?? Math.round(value / Math.max(total, 1) * 100);
      const row = el('div', 'stacked-row'); const head = el('div', 'stacked-head'); head.append(el('strong', '', item.label), el('span', '', `占比${share}%`));
      const track = el('div', 'stacked-track'); const filled = el('div', 'stacked-total'); filled.style.width = `${Math.max(value ? 4 : 0, Math.min(100, share))}%`;
      [['resolved', 'resolved', '已解决'], ['unresolved', 'unresolved', '未直接解决'], ['unknown', 'unknown', '待判断']].forEach(([key, className, label]) => { const rate = item[`${key}_share`] ?? (value ? Number(item[key] || 0) / value * 100 : 0); const part = el('i', `stacked-segment ${className}`); part.style.width = `${rate}%`; part.title = `${label}：${rate}%`; filled.append(part); }); track.append(filled);
      const meta = el('div', 'stacked-meta'); [['legend-resolved', '已解决', item.resolved_share], ['legend-unresolved', '未直接解决', item.unresolved_share], ['legend-unknown', '待判断', item.unknown_share]].forEach(([className, label, rate]) => { const itemMeta = el('span'); itemMeta.append(el('i', className), document.createTextNode(`${label} ${rate ?? 0}%`)); meta.append(itemMeta); });
      row.append(head, track, meta);
      if (drilldown && Array.isArray(item.children) && item.children.length) {
        const toggle = el('button', 'topic-drill-toggle', '查看二级专题'); toggle.type = 'button'; toggle.setAttribute('aria-expanded', 'false');
        const detail = el('div', 'secondary-topic-list hidden'); item.children.forEach(child => { const childRow = el('div', 'secondary-topic-row'); const childShare = child.share ?? 0; const childHead = el('div'); childHead.append(el('strong', '', child.label), el('span', '', `占比${childShare}%`)); const childTrack = el('div', 'secondary-topic-track'); const childFill = el('div', 'secondary-topic-fill'); childFill.style.width = `${Math.max(child.value ? 4 : 0, Math.min(100, childShare))}%`; [['resolved', '已解决'], ['unresolved', '未直接解决'], ['unknown', '待判断']].forEach(([key, label]) => { const rate = child[`${key}_share`] ?? (child.value ? Number(child[key] || 0) / Number(child.value) * 100 : 0); const part = el('i', key); part.style.width = `${rate}%`; part.title = `${label}：${rate}%`; childFill.append(part); }); childTrack.append(childFill); const childMeta = el('small', '', `已解决 ${child.resolved_share ?? 0}% · 未直接解决 ${child.unresolved_share ?? 0}% · 待判断 ${child.unknown_share ?? 0}%`); childRow.append(childHead, childTrack, childMeta); detail.append(childRow); });
        toggle.addEventListener('click', () => { const expanded = toggle.getAttribute('aria-expanded') === 'true'; toggle.setAttribute('aria-expanded', String(!expanded)); toggle.textContent = expanded ? '查看二级专题' : '收起二级专题'; detail.classList.toggle('hidden', expanded); }); row.append(toggle, detail);
      }
      list.append(row);
    }); target.append(legend, list);
  }
  function renderFacts(target, rows) {
    target.replaceChildren(); const grid = el('div', 'fact-grid'); (rows || []).forEach(row => { const share = Number(row.share || 0); const card = el('div', `fact-card${share > 0 ? ' active' : ''}`); card.append(el('strong', '', `${share}%`), el('span', '', row.label)); grid.append(card); }); target.append(grid);
  }
  function dashboardIcon(name) {
    const ns = 'http://www.w3.org/2000/svg'; const svg = document.createElementNS(ns, 'svg'); svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true');
    const paths = {
      phone: ['M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6A19.79 19.79 0 0 1 2.12 4.18 2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.69 2.8a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.33 1.85.56 2.81.69A2 2 0 0 1 22 16.92z'],
      check: ['M20 6 9 17l-5-5'],
      order: ['M9 11l3 3L22 4', 'M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11']
    };
    (paths[name] || paths.phone).forEach(value => { const path = document.createElementNS(ns, 'path'); path.setAttribute('d', value); svg.append(path); }); return svg;
  }
  function statCard(parent, label, value, note, color, icon) { const card = el('article', 'stat-card'); card.style.setProperty('--card-color', color); const iconBox = el('div', 'stat-icon'); iconBox.append(dashboardIcon(icon)); card.append(iconBox, el('span', 'stat-label', label), el('strong', 'stat-value', text(value, '—')), el('small', 'stat-note', note)); parent.append(card); }

  function renderInsights(target, data) {
    target.replaceChildren(); const overview = data.overview || {}; const callers = data.caller_types || []; const categories = data.question_categories || []; const demands = data.demand_categories || []; const facts = data.historical_facts || [];
    const insights = [];
    if (overview.total_calls) insights.push(`当前共收录 ${overview.total_calls} 条来电记录，数据覆盖 ${text(overview.data_date_range, '当前有效日期')}。`);
    if (callers.length) { const total = callers.reduce((sum, item) => sum + Number(item.value || 0), 0); insights.push(`咨询主体以“${callers[0].label}”为主，占已识别来电记录的 ${total ? Math.round(callers[0].value / total * 100) : 0}%。`); }
    if (overview.resolved_rate != null) { const unresolved = (data.resolution_status || []).find(item => item.label === '未直接解决'); insights.push(`已判断记录的直接解决率为 ${overview.resolved_rate}%，未直接解决 ${unresolved?.value || 0} 条。`); }
    if (categories.length && demands.length) insights.push(`咨询较集中于“${categories[0].label}”，主要需求为“${demands[0].label}”。`);
    const activeFact = [...facts].sort((a, b) => Number(b.share || 0) - Number(a.share || 0))[0]; if (activeFact?.share) insights.push(`历史服务事实中“${activeFact.label}”占总来电 ${activeFact.share}%，建议结合明细持续关注。`);
    const list = el('div', 'insight-list'); insights.slice(0, 4).forEach((item, index) => { const row = el('div', 'insight-item', item); row.dataset.index = index + 1; list.append(row); }); target.append(list.children.length ? list : el('div', 'empty-chart', '当前数据量不足，暂未形成整体情况分析。'));
  }

  async function loadDashboard(force = false) {
    if (state.dashboardLoaded && !force) return;
    const metrics = document.querySelector('#dashboard-metrics'); metrics.replaceChildren(el('div', 'stat-card', '正在汇总数据…'));
    try {
      const data = await api('/api/dashboard'); const overview = data.overview || {};
      const context = document.querySelector('#dashboard-context'); context.replaceChildren(el('strong', '', overview.data_date_range ? `数据范围：${overview.data_date_range}` : '当前暂无有效来电日期'), el('span', 'dashboard-context-note', '统计结果随数据库增量更新'));
      metrics.replaceChildren(); statCard(metrics, '累计来电', overview.total_calls, '当前收录的来电记录', colors[0], 'phone'); statCard(metrics, '直接解决率', overview.resolved_rate == null ? '—' : `${overview.resolved_rate}%`, '按已判断记录计算', colors[1], 'check');
      renderTrend(document.querySelector('#daily-chart'), data.daily_calls);
      renderDonut(document.querySelector('#caller-chart'), data.caller_types, ['#536bd3', '#f0a04b', '#8a64c7'], '来电记录');
      renderDonut(document.querySelector('#personal-resolution-chart'), data.personal_resolution, ['#2da58f', '#e77878', '#a8b1c4'], '个人来电');
      renderDonut(document.querySelector('#enterprise-resolution-chart'), data.enterprise_resolution, ['#7968d7', '#e99a52', '#8eb0c7'], '企业来电');
      renderInsights(document.querySelector('#insight-list'), data); renderFacts(document.querySelector('#historical-facts'), data.historical_facts); renderStacked(document.querySelector('#category-chart'), data.question_categories, true); renderStacked(document.querySelector('#demand-chart'), data.demand_categories); renderUnitResolution(document.querySelector('#unit-resolution-chart'), data.registration_unit_resolution);
      const grid = document.querySelector('#update-grid'); grid.replaceChildren(); const update = data.latest_update; const badge = document.querySelector('#update-status');
      if (update) { [['数据日期', update.data_date], ['来源文件', update.input_filename], ['新增来电', update.new_call_count], ['新增号码', update.new_phone_count], ['完成时间', dateText(update.finished_at)]].forEach(([label, value]) => { const item = el('div', 'update-item'); item.append(el('span', '', label), el('strong', '', text(value))); grid.append(item); }); badge.textContent = update.status === 'completed' ? '更新完成' : text(update.status); }
      else { grid.append(el('div', 'empty-chart', '暂无更新批次')); badge.textContent = '暂无批次'; }
      state.dashboardLoaded = true;
    } catch (error) { metrics.replaceChildren(el('div', 'stat-card', `读取失败：${error.message}`)); }
  }
  document.querySelector('#refresh-dashboard').addEventListener('click', () => loadDashboard(true));

  function historyRow(item) {
    const row = el('tr');
    row.tabIndex = 0;
    const subject = item.caller_type === '企业'
      ? `企业 · ${text(item.enterprise_identity, '细化主体待判断')}`
      : text(item.caller_type);
    const phoneCell = el('td');
    phoneCell.append(
      el('strong', '', text(item.masked_phone)),
      el('small', '', dateText(item.call_time)),
    );
    const questionCell = el('td');
    questionCell.append(
      el('strong', '', text(item.core_question)),
      el(
        'small',
        '',
        `${text(item.question_category)} · ${text(item.demand_category)}`,
      ),
    );
    const resolvedCell = el('td', '', resolvedText(item.resolved));
    if (item.work_order) resolvedCell.append(el('small', '', '历史工单'));
    row.append(
      phoneCell,
      el('td', '', subject),
      questionCell,
      resolvedCell,
    );
    row.addEventListener('click', () => openDetail(item.business_id));
    return row;
  }

  async function loadHistory(page = 1) {
    const body = document.querySelector('#history-body');
    body.replaceChildren(tableMessageRow('正在读取来电记录…', 4));
    try {
      const data = await api('/api/history', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({page, page_size: 10, phone: state.history.phone || null})});
      state.history.page = data.page; state.history.totalPages = data.total_pages; state.history.loaded = true; body.replaceChildren();
      if (!data.items.length) body.append(tableMessageRow('没有匹配的来电记录', 4));
      data.items.forEach(item => body.append(historyRow(item)));
      document.querySelector('#history-summary').textContent = `${data.filtered ? '当前号码' : '全部记录'} · 共 ${data.total} 条`;
      document.querySelector('#history-page-status').textContent = `第 ${data.page} / ${data.total_pages || 1} 页`; document.querySelector('#history-prev').disabled = data.page <= 1; document.querySelector('#history-next').disabled = data.page >= data.total_pages;
    } catch (error) {
      body.replaceChildren(tableMessageRow(`读取失败：${error.message}`, 4));
    }
  }
  document.querySelector('#history-filter').addEventListener('submit', event => { event.preventDefault(); state.history.phone = document.querySelector('#history-phone').value.trim(); loadHistory(1); });
  document.querySelector('#history-clear').addEventListener('click', () => { document.querySelector('#history-phone').value = ''; state.history.phone = ''; loadHistory(1); });
  document.querySelector('#history-prev').addEventListener('click', () => state.history.page > 1 && loadHistory(state.history.page - 1));
  document.querySelector('#history-next').addEventListener('click', () => state.history.page < state.history.totalPages && loadHistory(state.history.page + 1));

  function detailSection(title, data, fields) {
    const section = el('section', 'detail-section'); section.append(el('div', 'detail-section-title', title)); const grid = el('div', 'detail-grid');
    fields.forEach(([label, key, wide = false, formatter = text]) => { const item = el('div', `detail-field${wide ? ' wide' : ''}`); item.append(el('label', '', label), el('div', '', formatter(data?.[key]))); grid.append(item); }); section.append(grid); return section;
  }
  async function openDetail(businessId) {
    const overlay = document.querySelector('#detail-overlay'); const content = document.querySelector('#detail-content'); overlay.classList.remove('hidden'); document.body.classList.add('drawer-open'); content.replaceChildren(el('div', 'loading', '正在读取详情…'));
    try {
      const result = await api('/api/history/detail', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({business_id: businessId})}); if (!result.found) throw new Error('记录不存在'); const detail = result.detail;
      document.querySelector('#detail-title').textContent = `来电详情 · ${text(detail.original.business_id)}`; content.replaceChildren(
        detailSection('重点分析信息', detail.extracted, [['核心问题', 'core_question', true], ['坐席答复提炼', 'agent_answer_summary', true], ['一级专题', 'topic_category'], ['二级专题', 'secondary_topic'], ['需求类别', 'demand_category'], ['解决情况', 'resolved', false, resolvedText], ['未解决原因', 'unresolved_reason', true], ['业务专业度', 'proficiency_level'], ['业务专业度依据', 'proficiency_basis', true], ['近期情绪状态', 'emotion_state'], ['近期情绪状态依据', 'emotion_basis', true], ['等待推诿', 'wait_pushback', false, value => value ? '是' : '否'], ['联系后未解决', 'contact_unresolved', false, value => value ? '是' : '否'], ['服务不满', 'taxpayer_dissatisfied', false, value => value ? '是' : '否']]),
        detailSection('人工登记与原始信息', detail.original, [['业务内容', 'business_content', true], ['答复内容', 'answer_content', true], ['登记日期', 'registration_time'], ['通话开始', 'call_start_time'], ['通话结束', 'call_end_time'], ['坐席工号', 'agent_id'], ['坐席姓名', 'agent_name'], ['登记单位', 'registration_unit'], ['登记处理方式', 'handling_method'], ['业务类别', 'business_category'], ['满意度', 'satisfaction'], ['呼叫流水号', 'call_serial_number'], ['转写结果', 'transcript', true]])
      );
    } catch (error) { content.textContent = `详情读取失败：${error.message}`; }
  }
  function closeDetail() { document.querySelector('#detail-overlay').classList.add('hidden'); document.body.classList.remove('drawer-open'); }
  document.querySelector('#detail-close').addEventListener('click', closeDetail); document.querySelector('#detail-overlay').addEventListener('click', event => event.target.id === 'detail-overlay' && closeDetail());

  function showcasePanel(title, note = '', className = '') { const panel = el('article', `panel${className ? ` ${className}` : ''}`); const head = el('div', 'panel-head'); const titleWrap = el('div', 'panel-title'); titleWrap.append(el('h2', '', title)); head.append(titleWrap); if (note) head.append(el('span', 'panel-head-note', note)); const body = el('div', 'showcase-panel-body'); panel.append(head, body); return {panel, body}; }

  function renderGraph(data) {
    if (state.graphCleanup) state.graphCleanup();
    const root = document.querySelector('#profile-knowledge-content'); root.replaceChildren(); const catalog = state.showcaseCatalog; const taxonomy = catalog.taxonomy || {}; const panel = showcasePanel('多维画像三维关系图', data ? `${data.masked_phone} · 当前实例高亮` : '整体逻辑 · 可旋转探索');
    const toolbar = el('div', 'knowledge-graph-toolbar'); const toolbarNote = el('p', '', '三个画像维度分别推导表达方式、情绪响应和业务应对，三类结果同时组成接待策略。'); const toolbarActions = el('div', 'graph-toolbar-actions');
    const resetButton = el('button', 'graph-tool', '复位视角'); const rotationButton = el('button', 'graph-tool active', '暂停旋转'); const labelsButton = el('button', 'graph-tool active', '隐藏标签'); const overviewButton = el('button', 'graph-tool active', '显示全局'); const nextButton = el('button', 'graph-tool', '逐类聚焦 →'); const replayButton = data ? el('button', 'graph-tool replay', '重播推导') : null;
    [resetButton, rotationButton, labelsButton, overviewButton, nextButton, replayButton].filter(Boolean).forEach(button => button.type = 'button'); toolbarActions.append(...[resetButton, rotationButton, labelsButton, overviewButton, nextButton, replayButton].filter(Boolean)); toolbar.append(toolbarNote, toolbarActions);
    const stage = el('div', 'knowledge-graph-stage'); const canvas = el('canvas', 'knowledge-graph-canvas'); canvas.tabIndex = 0; canvas.setAttribute('role', 'img'); canvas.setAttribute('aria-label', '可旋转和缩放的三维画像关系图');
    const hint = el('div', 'graph-gesture-hint', '拖拽旋转 · 滚轮缩放 · 点击小球聚焦'); const legend = el('div', 'graph-depth-legend'); [['proficiency', '业务专业度'], ['emotion', '近期情绪状态'], ['facts', '历史服务事实'], ['mode-expression', '表达方式'], ['mode-emotion', '情绪响应'], ['mode-continuity', '业务应对'], ['guidance', '服务建议']].forEach(([className, label]) => { const item = el('span'); item.append(el('i', className), document.createTextNode(label)); legend.append(item); });
    let derivationTitle = null, derivationCopy = null, derivationBar = null; const derivationStatus = data ? el('div', 'graph-derivation-status') : null; if (derivationStatus) { derivationTitle = el('strong', '', '准备画像推导'); derivationCopy = el('span', '', '正在读取该号码的历史画像信息…'); const track = el('div', 'derivation-progress'); derivationBar = el('i'); track.append(derivationBar); derivationStatus.append(derivationTitle, derivationCopy, track); }
    stage.append(...[canvas, hint, derivationStatus, legend].filter(Boolean)); panel.body.append(toolbar, stage); root.append(panel.panel, renderClassificationCatalog(data));
    const dimensions = taxonomy.dimensions || []; const modeGroups = taxonomy.service_mode_groups || []; const instance = data?.before?.profile_model; const active = new Set(); (instance?.items || []).forEach(item => (item.values || []).forEach(value => active.add(`${item.id}:${value}`))); (data?.before?.result?.mode_components || []).forEach(component => active.add(`mode:${component.mode_id}`));
    const zoneConfig = {
      proficiency: {label: '业务专业度', y: -185, z: -75},
      emotion: {label: '近期情绪状态', y: 0, z: 105},
      facts: {label: '历史服务事实', y: 185, z: -55}
    };
    const nodes = []; const edges = [];
    const pushNode = node => { nodes.push(node); return node.id; };
    dimensions.forEach((dimension, dIndex) => {
      const zone = zoneConfig[dimension.id] || {label: dimension.name, y: (dIndex - 1) * 185, z: 0};
      const rootId = pushNode({id: `dim:${dimension.id}`, label: zone.label, kind: 'dimension', group: dimension.id, x: -340, y: zone.y, z: zone.z, r: 19});
      const categories = [...(dimension.categories || [])]; if (dimension.unknown) categories.push(dimension.unknown);
      categories.forEach((category, index) => { const angle = Math.PI * 2 * index / Math.max(categories.length, 1); const radiusY = dimension.id === 'facts' ? 82 : 68; const radiusZ = dimension.id === 'facts' ? 125 : 100; const id = pushNode({id: `${dimension.id}:${category}`, label: category, kind: 'category', group: dimension.id, x: -105 + Math.cos(angle) * 20, y: zone.y + Math.sin(angle) * radiusY, z: zone.z + Math.cos(angle) * radiusZ, r: 12, current: active.has(`${dimension.id}:${category}`)}); edges.push([rootId, id]); });
    });
    const modeZoneConfig = {
      emotion_response: {label: '情绪响应', y: -185, z: 85, group: 'mode_emotion_response'},
      matter_continuity: {label: '业务应对', y: 0, z: -105, group: 'mode_matter_continuity'},
      information_delivery: {label: '表达方式', y: 185, z: 65, group: 'mode_information_delivery'}
    };
    const groupSources = {
      emotion_response: ['emotion:不满', 'emotion:焦虑', 'emotion:平稳', 'emotion:暂无法判断', 'facts:等待推诿', 'facts:对坐席不满'],
      matter_continuity: ['facts:历史工单', 'facts:异常中断', 'facts:联系后未解决', 'facts:近五个工作日未命中'],
      information_delivery: ['proficiency:专业', 'proficiency:了解', 'proficiency:小白', 'proficiency:暂无法判断']
    };
    modeGroups.forEach((modeGroup, groupIndex) => {
      const zone = modeZoneConfig[modeGroup.id] || {label: modeGroup.label, y: (groupIndex - 1) * 185, z: 0, group: `mode_${modeGroup.id}`};
      const groupRoot = pushNode({id: `modegroup:${modeGroup.id}`, label: zone.label, kind: 'mode_group', group: zone.group, x: 120, y: zone.y, z: zone.z, r: 19});
      (groupSources[modeGroup.id] || []).forEach(source => edges.push([source, groupRoot]));
      (modeGroup.modes || []).forEach((mode, index) => {
        const angle = Math.PI * 2 * index / Math.max(modeGroup.modes.length, 1); const id = `mode:${mode.id}`;
        pushNode({id, label: mode.label, kind: 'mode', group: zone.group, x: 345, y: zone.y + Math.sin(angle) * 72, z: zone.z + Math.cos(angle) * 105, r: 15, current: active.has(id)}); edges.push([groupRoot, id]);
        const guideId = `guide:${mode.id}`; pushNode({id: guideId, label: mode.focus, kind: 'guidance', group: 'guidance', x: 555, y: zone.y + Math.sin(angle) * 72, z: zone.z + Math.cos(angle) * 105, r: 9, current: active.has(id)}); edges.push([id, guideId]);
      });
    });
    const sceneCenterX = (Math.min(...nodes.map(node => node.x)) + Math.max(...nodes.map(node => node.x))) / 2;
    const nodeMap = new Map(nodes.map(node => [node.id, node])); const edgeKey = (source, target) => `${source}→${target}`; const activeCategoryIds = new Set(nodes.filter(node => node.kind === 'category' && node.current).map(node => node.id)); const activeModeIds = new Set(nodes.filter(node => node.kind === 'mode' && node.current).map(node => node.id)); const activeGuideIds = new Set(nodes.filter(node => node.kind === 'guidance' && node.current).map(node => node.id)); const activeModeGroupIds = new Set(edges.filter((sourceTarget) => activeModeIds.has(sourceTarget[1]) && nodeMap.get(sourceTarget[0])?.kind === 'mode_group').map(([source]) => source)); const activeRootIds = new Set(edges.filter(([, target]) => activeCategoryIds.has(target)).map(([source]) => source)); const inputEdgeKeys = new Set(edges.filter(([source, target]) => activeRootIds.has(source) && activeCategoryIds.has(target)).map(([source, target]) => edgeKey(source, target))); const categoryEdgeKeys = new Set(edges.filter(([source, target]) => activeCategoryIds.has(source) && activeModeGroupIds.has(target)).map(([source, target]) => edgeKey(source, target))); const modeEdgeKeys = new Set(edges.filter(([source, target]) => activeModeGroupIds.has(source) && activeModeIds.has(target)).map(([source, target]) => edgeKey(source, target))); const guideEdgeKeys = new Set(edges.filter(([source, target]) => activeModeIds.has(source) && activeGuideIds.has(target)).map(([source, target]) => edgeKey(source, target))); const instancePathIds = new Set([...activeRootIds, ...activeCategoryIds, ...activeModeGroupIds, ...activeModeIds, ...activeGuideIds]);
    let rotX = -.12, rotY = -.3, zoom = 1, drag = false, px = 0, py = 0, dragDistance = 0, alive = true, frame = 0, lastFrameTime = 0, autoRotate = !window.matchMedia('(prefers-reduced-motion: reduce)').matches, showLabels = true, focusGroup = null, focusNodeId = null, focusIndex = -1, focusedCache = null, lastProjected = new Map(), demoActive = Boolean(data), demoStart = performance.now(), demoPhase = -1;
    rotationButton.classList.toggle('active', autoRotate); rotationButton.textContent = autoRotate ? '暂停旋转' : '继续旋转';
    if (demoActive) { overviewButton.classList.remove('active'); replayButton?.classList.add('active'); toolbarNote.textContent = '正在演示该号码画像如何分别推导三类接待方式。'; }
    const context = canvas.getContext('2d'); const palette = {proficiency: ['#c8d5ff','#4968d3'], emotion: ['#ffc5cf','#c94f69'], facts: ['#a4efdf','#218a7c'], mode_emotion_response: ['#efccff','#8755b7'], mode_matter_continuity: ['#ffdaa9','#bd7428'], mode_information_delivery: ['#bde9ff','#347fa8'], guidance: ['#efb9df','#8c4777']};
    function resize() { const rect = canvas.getBoundingClientRect(); const ratio = Math.min(devicePixelRatio || 1, 1.5); canvas.width = rect.width * ratio; canvas.height = rect.height * ratio; context.setTransform(ratio, 0, 0, ratio, 0, 0); }
    function project(node, transform) { let x = node.x - sceneCenterX, y = node.y, z = node.z; const x1 = x * transform.cy - z * transform.sy, z1 = x * transform.sy + z * transform.cy; const y1 = y * transform.cx - z1 * transform.sx, z2 = y * transform.sx + z1 * transform.cx; const perspective = 760 / (900 + z2); return {x: transform.centerX + x1 * perspective * zoom, y: transform.centerY + y1 * perspective * zoom, scale: perspective * zoom, depth: z2}; }
    const clamp = value => Math.max(0, Math.min(1, value));
    function demoProgress(elapsed) { return {input: clamp((elapsed - 350) / 900), category: clamp((elapsed - 1350) / 1050), mode: clamp((elapsed - 2550) / 1100), guide: clamp((elapsed - 3800) / 850), total: clamp(elapsed / 4800)}; }
    function updateDerivationStatus(elapsed) {
      if (!derivationStatus || !derivationTitle || !derivationCopy || !derivationBar) return; const mode = text(data?.before?.result?.service_mode); const signature = ((instance?.items || []).map(item => `${item.name}：${item.value}`)).join(' · '); let phase = 0, title = '准备画像推导', copy = '正在读取该号码的历史画像信息…';
      if (elapsed >= 350) { phase = 1; title = '点亮当前画像'; copy = signature || '正在确认当前画像标签。'; }
      if (elapsed >= 1350) { phase = 2; title = '进入三个服务类别'; copy = '画像依据分别汇入表达方式、情绪响应和业务应对。'; }
      if (elapsed >= 2550) { phase = 3; title = '类别内选择具体方式'; copy = '从每个类别中选择一项，三个结果互不覆盖。'; }
      if (elapsed >= 3800) { phase = 4; title = '形成组合接待策略'; copy = mode; }
      if (elapsed >= 4650) { phase = 5; title = '推导完成'; copy = `${mode} · ${text(data?.before?.result?.service_suggestion)}`; }
      if (phase !== demoPhase) { demoPhase = phase; derivationTitle.textContent = title; derivationCopy.textContent = copy; }
      derivationBar.style.width = `${demoProgress(elapsed).total * 100}%`;
    }
    function focusedNodeIds() {
      if (focusNodeId) {
        const ids = new Set([focusNodeId]); const start = nodeMap.get(focusNodeId); const incident = id => edges.forEach(([source, target]) => { if (source === id || target === id) { ids.add(source); ids.add(target); } }); incident(focusNodeId);
        if (start?.kind === 'dimension') [...ids].filter(id => nodeMap.get(id)?.kind === 'category').forEach(id => edges.filter(([source]) => source === id).forEach(([, target]) => ids.add(target)));
        if (start?.kind === 'mode' || start?.kind === 'guidance') { const modeIds = [...ids].filter(id => nodeMap.get(id)?.kind === 'mode'); modeIds.forEach(id => incident(id)); }
        [...ids].filter(id => nodeMap.get(id)?.kind === 'category').forEach(id => edges.filter(([, target]) => target === id).forEach(([source]) => ids.add(source)));
        [...ids].filter(id => nodeMap.get(id)?.kind === 'mode').forEach(id => edges.filter(([source]) => source === id).forEach(([, target]) => ids.add(target)));
        return ids;
      }
      if (!focusGroup) return new Set(nodes.map(node => node.id)); const ids = new Set(nodes.filter(node => node.group === focusGroup).map(node => node.id)); for (let pass = 0; pass < 2; pass += 1) edges.forEach(([source, target]) => { if (ids.has(source) || ids.has(target)) { ids.add(source); ids.add(target); } }); return ids;
    }
    function draw(timestamp = 0) {
      if (!alive) return; if (document.hidden || timestamp - lastFrameTime < 32) { frame = requestAnimationFrame(draw); return; } lastFrameTime = timestamp; if (autoRotate && !drag) rotY += .003; context.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
      const transform = {cy:Math.cos(rotY), sy:Math.sin(rotY), cx:Math.cos(rotX), sx:Math.sin(rotX), centerX:canvas.clientWidth / 2 - Math.min(52, canvas.clientWidth * .045), centerY:canvas.clientHeight / 2}; const projected = new Map(nodes.map(node => [node.id, project(node, transform)])); const focused = focusedCache || (focusedCache = focusedNodeIds()); const elapsed = Math.max(0, timestamp - demoStart); const progress = demoProgress(elapsed); const hasFocus = Boolean(demoActive || focusGroup || focusNodeId); if (demoActive) updateDerivationStatus(elapsed);
      edges.forEach(([source, target], index) => {
        const p1 = projected.get(source), p2 = projected.get(target); if (!p1 || !p2) return; const key = edgeKey(source, target); const activeEdge = focused.has(source) && focused.has(target); let animationProgress = null;
        if (demoActive) { if (inputEdgeKeys.has(key)) animationProgress = progress.input; else if (categoryEdgeKeys.has(key)) animationProgress = progress.category; else if (modeEdgeKeys.has(key)) animationProgress = progress.mode; else if (guideEdgeKeys.has(key)) animationProgress = progress.guide; }
        context.globalAlpha = demoActive ? (animationProgress === null ? .035 : .18) : hasFocus ? (activeEdge ? .7 : .06) : 1; context.strokeStyle = 'rgba(119,158,190,.35)'; context.lineWidth = 1; context.beginPath(); context.moveTo(p1.x,p1.y); context.lineTo(p2.x,p2.y); context.stroke();
        if (demoActive && animationProgress !== null && animationProgress > 0) { const endX = p1.x + (p2.x - p1.x) * animationProgress, endY = p1.y + (p2.y - p1.y) * animationProgress; context.globalAlpha = .92; context.strokeStyle = '#86e5d3'; context.lineWidth = 2.2; context.beginPath(); context.moveTo(p1.x,p1.y); context.lineTo(endX,endY); context.stroke(); context.fillStyle = '#d0fff6'; context.shadowBlur = 12; context.shadowColor = '#74e1ce'; context.beginPath(); context.arc(endX,endY,2.8,0,Math.PI*2); context.fill(); context.shadowBlur = 0; }
        else if (!demoActive && activeEdge && (!hasFocus || index % 2 === 0)) { const moving = (timestamp * .00016 + index * .093) % 1; const dotX = p1.x + (p2.x - p1.x) * moving, dotY = p1.y + (p2.y - p1.y) * moving; context.globalAlpha = hasFocus ? .95 : .55; context.fillStyle = '#a8f4e7'; context.beginPath(); context.arc(dotX,dotY,hasFocus ? 2.2 : 1.5,0,Math.PI*2); context.fill(); }
      });
      context.globalAlpha = 1; lastProjected = projected;
      [...nodes].sort((a,b) => projected.get(b.id).depth - projected.get(a.id).depth).forEach(node => {
        const point = projected.get(node.id); const isInput = activeRootIds.has(node.id) || activeCategoryIds.has(node.id); const isModeGroup = activeModeGroupIds.has(node.id); const isMode = activeModeIds.has(node.id); const isServiceNode = isModeGroup || isMode; const isGuide = activeGuideIds.has(node.id); let reveal = 1;
        if (demoActive) { reveal = isInput ? progress.input : isModeGroup ? progress.category : isMode ? progress.mode : isGuide ? progress.guide : .04; }
        const pulseTarget = node.current || focusNodeId === node.id || (demoActive && instancePathIds.has(node.id)); const pulse = pulseTarget ? 1 + Math.sin(timestamp * .004) * .07 : 1; const r = Math.max(5, node.r * point.scale * pulse); point.hitRadius = r; const [light,dark] = palette[node.group] || palette[node.kind] || palette.guidance;
        context.globalAlpha = demoActive ? Math.max(.04, reveal) : hasFocus && !focused.has(node.id) ? .16 : 1; const gradient = context.createRadialGradient(point.x-r*.35, point.y-r*.35, 1, point.x, point.y, r); gradient.addColorStop(0, light); gradient.addColorStop(1, dark); const highlighted = demoActive ? reveal > .7 && instancePathIds.has(node.id) : node.current || focusNodeId === node.id; context.shadowBlur = highlighted ? 19 : 5; context.shadowColor = isServiceNode ? light : isGuide ? '#eba7d5' : '#8fe7d8'; context.fillStyle = gradient; context.beginPath(); context.arc(point.x,point.y,r,0,Math.PI*2); context.fill(); context.shadowBlur = 0;
        if (highlighted) { const ringColor = isServiceNode ? light : isGuide ? '#e7a1d1' : '#8edfd0'; context.strokeStyle = ringColor; context.lineWidth = 2; context.beginPath(); context.arc(point.x,point.y,r+5,0,Math.PI*2); context.stroke(); }
        const showDemoLabel = demoActive && reveal > .5 && instancePathIds.has(node.id); if ((demoActive && showDemoLabel) || (!demoActive && (showLabels || node.current || (hasFocus && focused.has(node.id))))) { context.fillStyle = '#edf4ff'; context.font = `${node.current ? 700 : 600} 11px system-ui`; context.fillText(node.label.length > 18 ? `${node.label.slice(0,18)}…` : node.label, point.x+r+5, point.y+3); } context.globalAlpha = 1;
      }); frame = requestAnimationFrame(draw);
    }
    canvas.addEventListener('pointerdown', event => { drag = true; dragDistance = 0; px = event.clientX; py = event.clientY; canvas.setPointerCapture(event.pointerId); }); canvas.addEventListener('pointermove', event => { if (!drag) return; const dx = event.clientX - px, dy = event.clientY - py; dragDistance += Math.hypot(dx, dy); rotY += dx * .006; rotX = Math.max(-1, Math.min(1, rotX + dy * .004)); px = event.clientX; py = event.clientY; }); canvas.addEventListener('pointerup', event => { drag = false; canvas.releasePointerCapture(event.pointerId); if (dragDistance >= 5) return; const rect = canvas.getBoundingClientRect(), x = event.clientX - rect.left, y = event.clientY - rect.top; const clicked = [...nodes].reverse().find(node => { const point = lastProjected.get(node.id); return point && Math.hypot(point.x - x, point.y - y) <= (point.hitRadius || 7) + 7; }); if (!clicked) return; demoActive = false; replayButton?.classList.remove('active'); if (derivationTitle && derivationCopy) { derivationTitle.textContent = '手动关系聚焦'; derivationCopy.textContent = '再次点击当前小球可取消聚焦，或点击“重播推导”恢复演示。'; } focusNodeId = focusNodeId === clicked.id ? null : clicked.id; focusGroup = null; focusIndex = -1; focusedCache = null; overviewButton.classList.toggle('active', !focusNodeId); toolbarNote.textContent = focusNodeId ? `当前聚焦：${clicked.label}，相关画像依据和服务建议已高亮。` : '当前展示三个画像维度与三类组合接待方式的完整关系。'; }); canvas.addEventListener('wheel', event => { event.preventDefault(); zoom = Math.max(.6, Math.min(1.7, zoom * (event.deltaY > 0 ? .92 : 1.08))); }, {passive: false});
    resetButton.addEventListener('click', () => { rotX = -.12; rotY = -.3; zoom = 1; demoActive = false; replayButton?.classList.remove('active'); focusGroup = null; focusNodeId = null; focusIndex = -1; focusedCache = null; overviewButton.classList.add('active'); toolbarNote.textContent = '三个画像维度分别连接三类接待方式，类别之间同时生效。'; });
    rotationButton.addEventListener('click', () => { autoRotate = !autoRotate; rotationButton.classList.toggle('active', autoRotate); rotationButton.textContent = autoRotate ? '暂停旋转' : '继续旋转'; });
    labelsButton.addEventListener('click', () => { showLabels = !showLabels; labelsButton.classList.toggle('active', showLabels); labelsButton.textContent = showLabels ? '隐藏标签' : '显示标签'; });
    overviewButton.addEventListener('click', () => { demoActive = false; replayButton?.classList.remove('active'); focusGroup = null; focusNodeId = null; focusIndex = -1; focusedCache = null; zoom = 1; overviewButton.classList.add('active'); toolbarNote.textContent = '当前展示三个画像维度与三类组合接待方式的完整关系。'; });
    nextButton.addEventListener('click', () => { const groups = [...dimensions.map(item => item.id), ...modeGroups.map(item => `mode_${item.id}`)]; if (!groups.length) return; demoActive = false; replayButton?.classList.remove('active'); focusIndex = (focusIndex + 1) % groups.length; focusGroup = groups[focusIndex]; focusNodeId = null; focusedCache = null; zoom = 1.12; overviewButton.classList.remove('active'); const modeGroupId = focusGroup.replace(/^mode_/, ''); const label = zoneConfig[focusGroup]?.label || modeZoneConfig[modeGroupId]?.label || focusGroup; toolbarNote.textContent = `当前聚焦：${label}，相关画像依据和服务建议同步高亮。`; });
    replayButton?.addEventListener('click', () => { demoActive = true; demoStart = performance.now(); demoPhase = -1; focusGroup = null; focusNodeId = null; focusIndex = -1; focusedCache = null; rotX = -.12; rotY = -.3; zoom = 1; overviewButton.classList.remove('active'); replayButton.classList.add('active'); toolbarNote.textContent = '正在演示该号码画像如何分别推导三类接待方式。'; });
    const observer = new ResizeObserver(resize); observer.observe(canvas); requestAnimationFrame(() => { resize(); draw(); }); state.graphCleanup = () => { alive = false; cancelAnimationFrame(frame); observer.disconnect(); };
  }

  function renderClassificationCatalog(data) {
    const taxonomy = state.showcaseCatalog.taxonomy || {}; const panel = showcasePanel('完整分类与判定规则', data ? '当前画像与三个分项模式已同步突出' : '三维特征、五项事实、三类八项接待方式');
    const activeLabels = new Set(); ((data?.before?.profile_model || {}).items || []).forEach(item => (item.values || []).forEach(value => activeLabels.add(`${item.id}:${value}`))); const activeModeIds = new Set((data?.before?.result?.mode_components || []).map(item => item.mode_id));
    const dimensions = el('section', 'taxonomy-section'); const dHead = el('div', 'taxonomy-section-head'); dHead.append(el('strong', 'taxonomy-major-heading', '纳税人画像字段'), el('span', '', data ? '蓝色标签为当前画像' : '用于识别当前服务需求')); const dGrid = el('div', 'dimension-catalog'); (taxonomy.dimensions || []).forEach(dimension => { const hasCurrent = data && ((data.before.profile_model?.items || []).some(item => item.id === dimension.id)); const card = el('article', `dimension-card${hasCurrent ? ' active' : ''}`); const head = el('div', 'dimension-card-head'); head.append(el('strong', '', dimension.name), el('span', '', `${dimension.categories.length} 类`)); const tags = el('div', 'taxonomy-tags'); [...dimension.categories, dimension.unknown].forEach(category => tags.append(el('span', activeLabels.has(`${dimension.id}:${category}`) ? 'active' : '', category))); card.append(head, el('p', '', dimension.description), tags); dGrid.append(card); }); dimensions.append(dHead, dGrid);
    const modes = el('section', 'taxonomy-section'); const mHead = el('div', 'taxonomy-section-head'); mHead.append(el('strong', 'taxonomy-major-heading', '坐席接待方式'), el('span', '', data ? '每类彩色边框项为当前结果' : '每个类别选择一项，三个结果同时生效')); const groupGrid = el('div', 'service-mode-groups'); (taxonomy.service_mode_groups || []).forEach(group => { const groupCard = el('section', `service-mode-group ${modeClass({category_id: group.id})}`); const head = el('div', 'service-mode-group-head'); head.append(el('strong', '', group.label), el('span', '', `${(group.modes || []).length} 种接待方式`)); const grid = el('div', 'service-mode-catalog'); (group.modes || []).forEach(mode => { const card = el('article', `service-mode-card${activeModeIds.has(mode.id) ? ' active' : ''}`); card.append(el('strong', '', mode.label), el('p', '', mode.focus), el('div', 'composite-meta', `判定规则：${mode.rule}`), el('div', 'composite-meta', `沟通建议：${mode.communication}`)); grid.append(card); }); groupCard.append(head, el('p', 'service-mode-group-description', group.description), grid); groupGrid.append(groupCard); }); modes.append(mHead, groupGrid); panel.body.append(dimensions, modes); return panel.panel;
  }

  function profileOption(item, index) {
    const rank = Number(item.index || index + 1); const option = el('option', '', `${String(rank).padStart(2, '0')} · ${item.masked_phone}`); option.value = item.profile_key; return option;
  }

  function replaceProfileOptions(select, items) {
    const previous = select.value; select.replaceChildren(); items.forEach((item, index) => select.append(profileOption(item, index))); select.disabled = !items.length;
    if (items.some(item => item.profile_key === previous)) select.value = previous;
    return previous !== select.value && Boolean(select.value);
  }

  function profileSearchMeta(catalog, query = '') {
    const count = Number(catalog.summary?.profile_count || 0); const shown = (catalog.items || []).length;
    if (!shown) return '未找到匹配的号码画像';
    return query.trim() ? `找到 ${shown} 个匹配结果；最多展示5个` : `默认展示最近 ${shown} 个；其余 ${Math.max(0, count - shown)} 个可通过号码或序号搜索`;
  }

  async function searchProfiles(query) {
    const request = ++state.knowledgeSearchRequest;
    const select = document.querySelector('#knowledge-profile-select'); const meta = document.querySelector('#knowledge-index-meta');
    meta.textContent = '正在检索号码画像…';
    try {
      const catalog = await api(`/api/showcase/catalog?limit=5&q=${encodeURIComponent(query.trim())}`); if (request !== state.knowledgeSearchRequest) return;
      state.showcaseCatalog = {...state.showcaseCatalog, ...catalog, items:catalog.items}; const changed = replaceProfileOptions(select, catalog.items || []); meta.textContent = profileSearchMeta(catalog, query);
      if (select.value && (changed || query.trim())) await loadGraphInstance();
    } catch (error) { if (request === state.knowledgeSearchRequest) meta.textContent = `检索失败：${error.message}`; }
  }

  async function loadShowcaseCatalog() {
    try {
      const catalog = await api('/api/showcase/catalog?limit=5'); state.showcaseCatalog = catalog; const knowledgeSelect = document.querySelector('#knowledge-profile-select'); replaceProfileOptions(knowledgeSelect, catalog.items || []); document.querySelector('#knowledge-index-meta').textContent = profileSearchMeta(catalog); renderGraph(null);
    } catch (error) { document.querySelector('#knowledge-mode-status').textContent = `画像目录读取失败：${error.message}`; }
  }
  let knowledgeSearchTimer = 0; document.querySelector('#knowledge-profile-search').addEventListener('input', event => { clearTimeout(knowledgeSearchTimer); knowledgeSearchTimer = setTimeout(() => searchProfiles(event.target.value), 260); });
  document.querySelectorAll('[data-knowledge-mode]').forEach(button => button.addEventListener('click', async () => { const instance = button.dataset.knowledgeMode === 'instance'; document.querySelectorAll('[data-knowledge-mode]').forEach(node => node.classList.toggle('active', node === button)); document.querySelector('#knowledge-profile-wrap').classList.toggle('hidden', !instance); if (!instance) { document.querySelector('#knowledge-mode-status').textContent = '当前展示整体画像方法论，不关联具体号码。'; renderGraph(null); } else { await loadGraphInstance(); } }));
  async function loadGraphInstance() { const key = document.querySelector('#knowledge-profile-select').value; if (!key) return; try { const data = await api('/api/showcase', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({profile_key:key})}); document.querySelector('#knowledge-mode-status').textContent = `${data.masked_phone} · 仅高亮该号码当前画像，不改变已有画像数据。`; renderGraph(data); } catch(error) { document.querySelector('#knowledge-mode-status').textContent = `读取失败：${error.message}`; } }
  document.querySelector('#knowledge-profile-select').addEventListener('change', loadGraphInstance);

  async function loadUsers() {
    const body = document.querySelector('#user-body');
    body.replaceChildren(tableMessageRow('正在读取用户…', 6));
    try {
      const data = await api('/api/users');
      body.replaceChildren();
      data.items.forEach(user => {
        const row = el('tr');
        const roleSelect = el('select');
        roleSelect.append(
          selectOption('agent', '坐席'),
          selectOption('admin', '管理员'),
        );
        roleSelect.value = user.role;
        roleSelect.addEventListener(
          'change',
          () => updateUser(user.id, {role: roleSelect.value}),
        );
        const status = el(
          'span',
          `user-status ${user.is_active ? 'active' : 'disabled'}`,
          user.is_active ? '已启用' : '已停用',
        );
        const actions = el('div');
        const toggle = el(
          'button',
          'button secondary compact',
          user.is_active ? '停用' : '启用',
        );
        toggle.addEventListener(
          'click',
          () => updateUser(user.id, {is_active: !user.is_active}),
        );
        const reset = el('button', 'button ghost compact', '重置密码');
        reset.addEventListener('click', () => {
          const password = prompt('请输入至少8位的新密码');
          if (password) updateUser(user.id, {password});
        });
        actions.append(toggle, reset);
        [user.username, user.display_name].forEach(
          value => row.append(el('td', '', value)),
        );
        const roleCell = el('td');
        roleCell.append(roleSelect);
        const statusCell = el('td');
        statusCell.append(status);
        const actionCell = el('td');
        actionCell.append(actions);
        row.append(
          roleCell,
          statusCell,
          el('td', '', dateText(user.created_at)),
          actionCell,
        );
        body.append(row);
      });
    } catch(error) {
      body.replaceChildren(tableMessageRow(`读取失败：${error.message}`, 6));
    }
  }
  async function updateUser(userId, fields) { try { await api('/api/users/update', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({user_id:userId, ...fields})}); loadUsers(); } catch(error) { alert(error.message); loadUsers(); } }
  document.querySelector('#user-form').addEventListener('submit', async event => { event.preventDefault(); const notice = document.querySelector('#user-notice'); try { await api('/api/users/create', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({username:document.querySelector('#new-username').value.trim(), display_name:document.querySelector('#new-display-name').value.trim(), password:document.querySelector('#new-password').value, role:document.querySelector('#new-role').value})}); event.target.reset(); notice.textContent = '用户创建成功。'; notice.className = 'notice'; loadUsers(); } catch(error) { notice.textContent = error.message; notice.className = 'notice error'; } });
  document.querySelector('#refresh-users').addEventListener('click', loadUsers);

  restoreSession();
})();
