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
  const pages = new Set(['workbench', 'dashboard', 'history', 'showcase', 'users']);
  const titles = {workbench: '12366坐席接待助手', dashboard: '画像数据概览', history: '历史来电记录', showcase: '画像推演中心', users: '用户与权限'};
  const state = {
    user: null,
    phone: '',
    dashboardLoaded: false,
    history: {page: 1, totalPages: 0, phone: '', loaded: false},
    showcaseCatalog: null,
    showcaseScenario: 'baseline',
    showcaseRequest: 0,
    graphCleanup: null,
  };

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
    tags.append(el('span', 'category-pill', `熟悉度 · ${text(profile.proficiency_level, '暂无法判断')}`));
    tags.append(el('span', 'category-pill', `近期情绪 · ${text(profile.emotion_state, '暂无法判断')}`));
    tags.append(el('span', 'category-pill', `接待模式 · ${text(profile.recommended_mode, '通俗引导')}`));
    heading.append(tags);
    const details = el('div', 'identity-details');
    identityDetail(details, '最近咨询', profile.latest_question, true);
    identityDetail(details, '最近来电时间', profile.latest_call_time);
    identityDetail(details, '登记单位', profile.latest_registration_unit);
    identityDetail(details, '专题类别', profile.latest_topic_category);
    identityDetail(details, '需求类别', profile.latest_demand_category);
    identity.append(heading, details);
    const recent = profile.recent_workday_statistics || {};
    const metrics = el('div', 'metrics');
    metric(metrics, '历史来电', recent.call_count);
    metric(metrics, '同类诉求', recent.same_demand_count);
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
    if (item.is_repeated_issue) facts.push(text(item.repeat_summary || item.matched_previous_question, '已确认同类诉求'));
    if (item.work_order) facts.push('该通形成工单');
    if (item.wait_pushback) facts.push('同时出现等待表述和潜在推诿');
    if (item.taxpayer_dissatisfied) facts.push('来电人对当前坐席或本通服务表达不满');
    if (item.resolved === false) facts.push(`未直接解决：${text(item.unresolved_reason, '原因未形成明确记录')}`);
    facts.push(`当前记录：${resolvedText(item.resolved)}`);
    card.append(el('div', 'issue-reason', facts.join('；')));
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
    issueSection(box, '同类诉求', groups.same_demand || [], '当前没有已确认的同类重复诉求。');
    issueSection(box, '历史工单', groups.work_orders || [], '当前没有历史工单记录。');
    issueSection(box, '等待推诿', groups.wait_pushback || [], '当前没有同时命中等待和潜在推诿的记录。');
    issueSection(box, '服务不满', groups.dissatisfaction || [], '当前没有对坐席或本通服务不满的记录。');
    issueSection(box, '未直接解决', groups.unresolved || [], '当前没有未直接解决记录。');
    const dimensions = el('section', 'history-profile-section');
    const head = el('div', 'issue-section-head'); head.append(el('strong', '', '近期画像依据'), el('span', 'issue-count', '接待参考'));
    const grid = el('div', 'history-profile-grid');
    [['业务熟悉度', profile.proficiency_level, profile.proficiency_basis], ['近期情绪状态', profile.emotion_state, profile.emotion_basis], ['推荐接待模式', profile.recommended_mode, profile.reception_mode?.basis]].forEach(([label, value, basis]) => {
      const card = el('div', 'history-profile-card'); card.append(el('span', '', label), el('strong', '', text(value)), el('p', '', text(basis, '当前证据不足。'))); grid.append(card);
    });
    dimensions.append(head, grid); box.append(dimensions);
    document.querySelector('#view-current-history').classList.remove('hidden');
  }

  function bulletSection(parent, title, values) {
    if (!values || !values.length) return;
    const section = el('section', 'advice-section'); section.append(el('h3', '', title));
    const list = el('ul'); values.forEach(value => list.append(el('li', '', value))); section.append(list); parent.append(section);
  }

  function renderAdvice(advice) {
    const box = document.querySelector('#advice'); box.className = 'panel-body'; box.replaceChildren();
    const mode = el('div', 'advice-mode'); mode.append(el('span', '', '推荐接待模式'), el('strong', '', text(advice.service_mode, '通俗引导'))); box.append(mode);
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
  function renderDonut(target, rows) {
    target.replaceChildren(); const values = rows || []; const total = values.reduce((sum, item) => sum + Number(item.value || 0), 0);
    if (!total) { target.append(el('div', 'empty-chart', '暂无可展示数据')); return; }
    let offset = 0; const parts = values.map((item, index) => { const start = offset; offset += item.value / total * 360; return `${colors[index % colors.length]} ${start}deg ${offset}deg`; });
    const layout = el('div', 'donut-layout'); const donut = el('div', 'donut'); donut.style.background = `conic-gradient(${parts.join(',')})`;
    const center = el('div', 'donut-center'); center.append(el('strong', '', total), el('span', '', '来电记录')); donut.append(center);
    const legend = el('div', 'chart-legend'); values.forEach((item, index) => {
      const row = el('div', 'legend-row'); row.tabIndex = 0;
      const dot = el('i', 'legend-swatch'); dot.style.background = colors[index % colors.length];
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
  function renderStacked(target, rows) {
    target.replaceChildren(); const values = rows || []; const total = values.reduce((sum, item) => sum + item.value, 0);
    if (!values.length) { target.append(el('div', 'empty-chart', '暂无可展示数据')); return; }
    const legend = el('div', 'stacked-legend'); [['legend-resolved', '已解决'], ['legend-unresolved', '未直接解决'], ['legend-unknown', '待判断']].forEach(([className, label]) => { const item = el('span'); item.append(el('i', className), document.createTextNode(label)); legend.append(item); });
    const list = el('div', 'stacked-list'); values.forEach(item => {
      const value = Number(item.value || 0); const share = item.share ?? Math.round(value / Math.max(total, 1) * 100);
      const row = el('div', 'stacked-row'); const head = el('div', 'stacked-head'); head.append(el('strong', '', item.label), el('span', '', `${value}次 · 占比${share}%`));
      const track = el('div', 'stacked-track'); const filled = el('div', 'stacked-total'); filled.style.width = `${Math.max(value ? 4 : 0, Math.min(100, share))}%`;
      [['resolved', 'resolved', '已解决'], ['unresolved', 'unresolved', '未直接解决'], ['unknown', 'unknown', '待判断']].forEach(([key, className, label]) => { const part = el('i', `stacked-segment ${className}`); part.style.width = `${value ? Number(item[key] || 0) / value * 100 : 0}%`; part.title = `${label}：${item[key] || 0}`; filled.append(part); }); track.append(filled);
      const meta = el('div', 'stacked-meta'); [['legend-resolved', '已解决', item.resolved], ['legend-unresolved', '未直接解决', item.unresolved], ['legend-unknown', '待判断', item.unknown]].forEach(([className, label, count]) => { const itemMeta = el('span'); itemMeta.append(el('i', className), document.createTextNode(`${label} ${count || 0}`)); meta.append(itemMeta); });
      row.append(head, track, meta); list.append(row);
    }); target.append(legend, list);
  }
  function renderFacts(target, rows) {
    target.replaceChildren(); const grid = el('div', 'fact-grid'); (rows || []).forEach(row => { const card = el('div', `fact-card${Number(row.value || 0) > 0 ? ' active' : ''}`); card.append(el('strong', '', row.value), el('span', '', row.label)); grid.append(card); }); target.append(grid);
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
    if (callers.length) { const total = callers.reduce((sum, item) => sum + Number(item.value || 0), 0); insights.push(`咨询主体以“${callers[0].label}”为主，占已识别画像的 ${total ? Math.round(callers[0].value / total * 100) : 0}%。`); }
    if (overview.resolved_rate != null) { const unresolved = (data.resolution_status || []).find(item => item.label === '未直接解决'); insights.push(`已判断记录的直接解决率为 ${overview.resolved_rate}%，未直接解决 ${unresolved?.value || 0} 条。`); }
    if (categories.length && demands.length) insights.push(`咨询较集中于“${categories[0].label}”，主要需求为“${demands[0].label}”。`);
    const activeFact = [...facts].sort((a, b) => Number(b.value || 0) - Number(a.value || 0))[0]; if (activeFact?.value) insights.push(`历史服务事实中“${activeFact.label}”出现较多，共 ${activeFact.value} 条，建议结合明细持续关注。`);
    const list = el('div', 'insight-list'); insights.slice(0, 4).forEach((item, index) => { const row = el('div', 'insight-item', item); row.dataset.index = index + 1; list.append(row); }); target.append(list.children.length ? list : el('div', 'empty-chart', '当前数据量不足，暂未形成整体情况分析。'));
  }

  async function loadDashboard(force = false) {
    if (state.dashboardLoaded && !force) return;
    const metrics = document.querySelector('#dashboard-metrics'); metrics.replaceChildren(el('div', 'stat-card', '正在汇总数据…'));
    try {
      const data = await api('/api/dashboard'); const overview = data.overview || {};
      const context = document.querySelector('#dashboard-context'); context.replaceChildren(el('strong', '', overview.data_date_range ? `数据范围：${overview.data_date_range}` : '当前暂无有效来电日期'), el('span', 'dashboard-context-note', '统计结果随数据库增量更新'));
      metrics.replaceChildren(); statCard(metrics, '累计来电', overview.total_calls, '当前收录的来电记录', colors[0], 'phone'); statCard(metrics, '直接解决率', overview.resolved_rate == null ? '—' : `${overview.resolved_rate}%`, '按已判断记录计算', colors[1], 'check');
      renderTrend(document.querySelector('#daily-chart'), data.daily_calls); renderDonut(document.querySelector('#caller-chart'), data.caller_types); renderDonut(document.querySelector('#resolution-chart'), data.resolution_status); renderInsights(document.querySelector('#insight-list'), data); renderFacts(document.querySelector('#historical-facts'), data.historical_facts); renderStacked(document.querySelector('#category-chart'), data.question_categories); renderStacked(document.querySelector('#demand-chart'), data.demand_categories);
      const grid = document.querySelector('#update-grid'); grid.replaceChildren(); const update = data.latest_update; const badge = document.querySelector('#update-status');
      if (update) { [['数据日期', update.data_date], ['来源文件', update.input_filename], ['新增来电', update.new_call_count], ['新增号码', update.new_phone_count], ['完成时间', dateText(update.finished_at)]].forEach(([label, value]) => { const item = el('div', 'update-item'); item.append(el('span', '', label), el('strong', '', text(value))); grid.append(item); }); badge.textContent = update.status === 'completed' ? '更新完成' : text(update.status); }
      else { grid.append(el('div', 'empty-chart', '暂无更新批次')); badge.textContent = '暂无批次'; }
      state.dashboardLoaded = true;
    } catch (error) { metrics.replaceChildren(el('div', 'stat-card', `读取失败：${error.message}`)); }
  }
  document.querySelector('#refresh-dashboard').addEventListener('click', () => loadDashboard(true));

  async function loadHistory(page = 1) {
    const body = document.querySelector('#history-body'); body.innerHTML = '<tr><td class="table-empty" colspan="5">正在读取来电记录…</td></tr>';
    try {
      const data = await api('/api/history', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({page, page_size: 10, phone: state.history.phone || null})});
      state.history.page = data.page; state.history.totalPages = data.total_pages; state.history.loaded = true; body.replaceChildren();
      if (!data.items.length) body.innerHTML = '<tr><td class="table-empty" colspan="5">没有匹配的来电记录</td></tr>';
      data.items.forEach(item => { const row = el('tr'); row.tabIndex = 0; const subject = item.caller_type === '企业' ? `企业 · ${text(item.enterprise_identity, '细化主体待判断')}` : text(item.caller_type); row.innerHTML = `<td><strong>${text(item.masked_phone)}</strong><small>${dateText(item.call_time)}</small></td><td>${subject}</td><td><strong>${text(item.core_question)}</strong><small>${text(item.question_category)} · ${text(item.demand_category)}</small></td><td>${resolvedText(item.resolved)}${item.work_order ? '<small>历史工单</small>' : ''}</td><td>${item.wait_pushback ? '等待推诿' : item.taxpayer_dissatisfied ? '服务不满' : item.contact_unresolved ? '联系后未解决' : '常规记录'}</td>`; row.addEventListener('click', () => openDetail(item.business_id)); body.append(row); });
      document.querySelector('#history-summary').textContent = `${data.filtered ? '当前号码' : '全部记录'} · 共 ${data.total} 条`;
      document.querySelector('#history-page-status').textContent = `第 ${data.page} / ${data.total_pages || 1} 页`; document.querySelector('#history-prev').disabled = data.page <= 1; document.querySelector('#history-next').disabled = data.page >= data.total_pages;
    } catch (error) { body.innerHTML = `<tr><td class="table-empty" colspan="5">读取失败：${error.message}</td></tr>`; }
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
    const overlay = document.querySelector('#detail-overlay'); const content = document.querySelector('#detail-content'); overlay.classList.remove('hidden'); document.body.classList.add('drawer-open'); content.innerHTML = '<div class="loading">正在读取详情…</div>';
    try {
      const result = await api('/api/history/detail', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({business_id: businessId})}); if (!result.found) throw new Error('记录不存在'); const detail = result.detail;
      document.querySelector('#detail-title').textContent = `来电详情 · ${text(detail.original.business_id)}`; content.replaceChildren(
        detailSection('重点分析信息', detail.extracted, [['核心问题', 'core_question', true], ['专题类别', 'topic_category'], ['需求类别', 'demand_category'], ['解决情况', 'resolved', false, resolvedText], ['未解决原因', 'unresolved_reason', true], ['业务熟悉度', 'proficiency_level'], ['熟悉度依据', 'proficiency_basis', true], ['近期情绪', 'emotion_state'], ['情绪依据', 'emotion_basis', true], ['等待推诿', 'wait_pushback', false, value => value ? '是' : '否'], ['联系后未解决', 'contact_unresolved', false, value => value ? '是' : '否'], ['服务不满', 'taxpayer_dissatisfied', false, value => value ? '是' : '否']]),
        detailSection('人工登记与原始信息', detail.original, [['业务内容', 'business_content', true], ['答复内容', 'answer_content', true], ['登记日期', 'registration_time'], ['通话开始', 'call_start_time'], ['通话结束', 'call_end_time'], ['坐席工号', 'agent_id'], ['坐席姓名', 'agent_name'], ['登记单位', 'registration_unit'], ['登记处理方式', 'handling_method'], ['业务类别', 'business_category'], ['满意度', 'satisfaction'], ['呼叫流水号', 'call_serial_number'], ['转写结果', 'transcript', true]])
      );
    } catch (error) { content.textContent = `详情读取失败：${error.message}`; }
  }
  function closeDetail() { document.querySelector('#detail-overlay').classList.add('hidden'); document.body.classList.remove('drawer-open'); }
  document.querySelector('#detail-close').addEventListener('click', closeDetail); document.querySelector('#detail-overlay').addEventListener('click', event => event.target.id === 'detail-overlay' && closeDetail());

  function showcasePanel(title, note = '', className = '') { const panel = el('article', `panel${className ? ` ${className}` : ''}`); const head = el('div', 'panel-head'); const titleWrap = el('div', 'panel-title'); titleWrap.append(el('h2', '', title)); head.append(titleWrap); if (note) head.append(el('span', 'panel-head-note', note)); const body = el('div', 'showcase-panel-body'); panel.append(head, body); return {panel, body}; }

  function renderInference(data) {
    const root = document.querySelector('#showcase-content'); root.replaceChildren();
    const before = data.before.result || {}; const after = data.after.result || {}; const changedRows = (data.changes || []).filter(item => item.changed);
    const result = showcasePanel('增量画像结果', `${data.masked_phone} · ${data.scenario.label}`, 'simulation-panel');
    const hero = el('div', 'profile-method-hero'); hero.append(el('span', '', '推演后的推荐接待模式'), el('strong', '', text(after.service_mode)), el('p', '', text(after.strategy_reason)));
    const event = el('div', 'simulation-event'); event.append(el('i', '', '＋'), el('span', '', text(data.scenario.event)));
    const flow = el('div', 'inference-flow');
    const changedDimensions = changedRows.filter(item => item.field !== '推荐接待模式').map(item => item.field);
    [
      ['新增来电情景', text(data.scenario.label)],
      ['画像变化', changedDimensions.length ? changedDimensions.join('、') : '当前画像保持稳定'],
      ['服务关注', (after.matched_facts || []).length ? after.matched_facts.join('、') : '未出现重点关注信号'],
      ['接待方式', text(after.service_mode)]
    ].forEach(([label, value], index) => {
      const stage = el('div', 'inference-stage'); stage.append(el('span', '', label), el('strong', '', value)); flow.append(stage);
      if (index < 3) flow.append(el('i', 'inference-arrow', '→'));
    });
    const comparison = el('div', 'comparison-grid');
    const beforeCard = el('div', 'comparison-card'); beforeCard.append(el('span', '', '当前接待模式'), el('strong', '', text(before.service_mode)), el('p', '', text(before.strategy_reason)));
    const afterCard = el('div', 'comparison-card after'); afterCard.append(el('span', '', '推演后接待模式'), el('strong', '', text(after.service_mode)), el('p', '', text(after.strategy_reason)));
    comparison.append(beforeCard, el('div', 'comparison-arrow', '→'), afterCard);
    const changes = el('div', 'change-grid');
    if (changedRows.length) changedRows.forEach(item => { const card = el('div', 'change-card changed'); const values = el('div', 'change-values'); values.append(el('b', '', text(item.before, '0')), el('i', '', '→'), el('b', '', text(item.after, '0'))); card.append(el('span', '', item.field), values); changes.append(card); });
    else changes.append(el('div', 'issue-empty', '当前为画像基线回放，各项画像信息保持不变。'));
    result.body.append(hero, event, flow, comparison, changes, el('div', 'strategy-output', `服务建议：${text(after.service_suggestion)} ${text(after.communication, '')}`)); root.append(result.panel);

    const grid = el('div', 'showcase-grid'); const profile = data.profile || {}; const current = showcasePanel('现有画像基线', data.masked_phone, 'showcase-profile-panel');
    let subject = text(profile.caller_type, '咨询主体待识别'); if (profile.caller_type === '企业') subject += profile.enterprise_identity && profile.enterprise_identity !== '无法判断' ? ` · ${profile.enterprise_identity}` : ' · 细化主体待判断';
    const identity = el('div', 'profile-identity'); identity.append(el('span', '', '最近咨询主体'), el('strong', '', subject), el('p', '', `历史来电覆盖：${dateText(profile.first_call_time)} 至 ${dateText(profile.latest_call_time)}`));
    const facets = el('div', 'profile-facets'); [['最近关注事项', profile.latest_question], ['专题与需求', `${text(profile.topic_category)} · ${text(profile.demand_category)}`]].forEach(([label, value]) => { const card = el('div', 'profile-facet'); card.append(el('label', '', label), el('strong', '', text(value))); facets.append(card); });
    const dimensions = el('div', 'profile-dimension-summary'); ((data.before.profile_model || {}).items || []).forEach(item => { const card = el('div', 'profile-dimension-item'); card.title = text(item.basis); card.append(el('span', '', item.name), el('strong', '', item.value)); dimensions.append(card); });
    const baseline = el('div', 'baseline-strategy'); baseline.append(el('label', '', '当前推荐接待模式'), el('strong', '', text(before.service_mode)), el('p', '', text(before.strategy_reason))); current.body.append(identity, facets, dimensions, baseline);

    const evidence = showcasePanel('历史证据回放', '选择来电查看画像贡献', 'evidence-panel'); const strip = el('div', 'rolling-strip');
    [['历史来电', (data.timeline || []).length], ['画像维度', (data.before.profile_model?.items || []).length], ['关注信号', (before.matched_facts || []).length], ['当前模式', text(before.service_mode)]].forEach(([label, value]) => { const card = el('div', 'rolling-signal'); card.append(el('strong', '', value), el('span', '', label)); strip.append(card); });
    const timeline = el('div', 'evidence-timeline'); const explain = el('div', 'evidence-explain', '选择一通来电，可查看它为当前画像提供的依据。');
    (data.timeline || []).slice(-8).reverse().forEach((item, index) => {
      const card = el('button', `evidence-item${index === 0 ? ' active' : ''}`); card.type = 'button'; card.append(el('time', '', `${dateText(item.call_time)} · 第${item.index}通`), el('strong', '', text(item.question)), el('span', '', (item.contributions || []).join(' · ')));
      const show = () => { timeline.querySelectorAll('.evidence-item').forEach(node => node.classList.toggle('active', node === card)); explain.replaceChildren(document.createTextNode(`本通画像贡献：${(item.contributions || []).join('；')}。`)); if (item.business_id) { const action = el('button', 'issue-action', '查看该通完整记录 →'); action.type = 'button'; action.addEventListener('click', () => openDetail(item.business_id)); explain.append(action); } };
      card.addEventListener('click', show); timeline.append(card); if (index === 0) setTimeout(show, 0);
    });
    if (!timeline.children.length) timeline.append(el('div', 'issue-empty', '当前画像暂无可回放的来电记录。'));
    evidence.body.append(strip, timeline, explain); grid.append(current.panel, evidence.panel); root.append(grid);

    const methodology = el('details', 'panel methodology-details'); methodology.append(el('summary', '', '查看画像形成说明')); const methodologyBody = el('div', 'showcase-panel-body');
    (state.showcaseCatalog?.methodology || []).forEach((item, index) => { const content = typeof item === 'string' ? item : `${item.title}：${item.description}`; const card = el('div', 'methodology-item', content); card.append(el('i', '', index + 1)); methodologyBody.append(card); }); methodology.append(methodologyBody);
    root.append(methodology, el('div', 'showcase-disclaimer', '本次为情景推演，结果仅供演示参考。'));
  }

  function renderGraph(data) {
    if (state.graphCleanup) state.graphCleanup();
    const root = document.querySelector('#profile-knowledge-content'); root.replaceChildren(); const catalog = state.showcaseCatalog; const taxonomy = catalog.taxonomy || {}; const panel = showcasePanel('多维画像三维关系图', data ? `${data.masked_phone} · 当前实例高亮` : '整体逻辑 · 可旋转探索');
    const toolbar = el('div', 'knowledge-graph-toolbar'); const toolbarNote = el('p', '', '专业程度、近期情绪和历史服务事实使用不同颜色，并共同连接到坐席接待模式。'); const toolbarActions = el('div', 'graph-toolbar-actions');
    const resetButton = el('button', 'graph-tool', '复位视角'); const rotationButton = el('button', 'graph-tool active', '暂停旋转'); const labelsButton = el('button', 'graph-tool active', '隐藏标签'); const overviewButton = el('button', 'graph-tool active', '显示全局'); const nextButton = el('button', 'graph-tool', '逐类聚焦 →'); const replayButton = data ? el('button', 'graph-tool replay', '重播推导') : null;
    [resetButton, rotationButton, labelsButton, overviewButton, nextButton, replayButton].filter(Boolean).forEach(button => button.type = 'button'); toolbarActions.append(...[resetButton, rotationButton, labelsButton, overviewButton, nextButton, replayButton].filter(Boolean)); toolbar.append(toolbarNote, toolbarActions);
    const stage = el('div', 'knowledge-graph-stage'); const canvas = el('canvas', 'knowledge-graph-canvas'); canvas.tabIndex = 0; canvas.setAttribute('role', 'img'); canvas.setAttribute('aria-label', '可旋转和缩放的三维画像关系图');
    const hint = el('div', 'graph-gesture-hint', '拖拽旋转 · 滚轮缩放 · 点击小球聚焦'); const legend = el('div', 'graph-depth-legend'); [['proficiency', '专业程度'], ['emotion', '近期情绪'], ['facts', '历史服务事实'], ['mode', '接待模式'], ['guidance', '服务建议']].forEach(([className, label]) => { const item = el('span'); item.append(el('i', className), document.createTextNode(label)); legend.append(item); });
    let derivationTitle = null, derivationCopy = null, derivationBar = null; const derivationStatus = data ? el('div', 'graph-derivation-status') : null; if (derivationStatus) { derivationTitle = el('strong', '', '准备画像推导'); derivationCopy = el('span', '', '正在读取该号码的历史画像信息…'); const track = el('div', 'derivation-progress'); derivationBar = el('i'); track.append(derivationBar); derivationStatus.append(derivationTitle, derivationCopy, track); }
    stage.append(...[canvas, hint, derivationStatus, legend].filter(Boolean)); panel.body.append(toolbar, stage); root.append(panel.panel, renderClassificationCatalog(data));
    const dimensions = taxonomy.dimensions || []; const modes = taxonomy.service_modes || []; const instance = data?.before?.profile_model; const active = new Set(); (instance?.items || []).forEach(item => (item.values || []).forEach(value => active.add(`${item.id}:${value}`))); if (data?.before?.result?.service_mode) active.add(`mode:${data.before.result.service_mode}`);
    const zoneConfig = {
      proficiency: {label: '专业程度', y: -185, z: -75},
      emotion: {label: '近期情绪', y: 0, z: 105},
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
    modes.forEach((mode, index) => { const id = pushNode({id: `mode:${mode.label}`, label: mode.label, kind: 'mode', group: 'mode', x: 245, y: (index - 1.5) * 125, z: Math.sin(index * 1.6) * 145, r: 17, current: active.has(`mode:${mode.label}`)}); const sourceHints = index === 0 ? ['emotion:不满', 'facts:等待推诿', 'facts:对坐席不满'] : index === 1 ? ['facts:历史工单', 'facts:异常中断', 'facts:联系后未解决'] : index === 2 ? ['proficiency:专业', 'proficiency:了解'] : ['proficiency:小白', 'proficiency:暂无法判断']; sourceHints.forEach(source => edges.push([source, id])); pushNode({id: `guide:${mode.label}`, label: mode.focus, kind: 'guidance', group: 'guidance', x: 500, y: (index - 1.5) * 125, z: Math.cos(index) * 120, r: 10, current: active.has(`mode:${mode.label}`)}); edges.push([id, `guide:${mode.label}`]); });
    const sceneCenterX = (Math.min(...nodes.map(node => node.x)) + Math.max(...nodes.map(node => node.x))) / 2;
    const nodeMap = new Map(nodes.map(node => [node.id, node])); const edgeKey = (source, target) => `${source}→${target}`; const activeCategoryIds = new Set(nodes.filter(node => node.kind === 'category' && node.current).map(node => node.id)); const activeModeId = nodes.find(node => node.kind === 'mode' && node.current)?.id || null; const activeGuideId = nodes.find(node => node.kind === 'guidance' && node.current)?.id || null; const activeRootIds = new Set(edges.filter(([, target]) => activeCategoryIds.has(target)).map(([source]) => source)); const inputEdgeKeys = new Set(edges.filter(([source, target]) => activeRootIds.has(source) && activeCategoryIds.has(target)).map(([source, target]) => edgeKey(source, target))); const ruleEdgeKeys = new Set(edges.filter(([source, target]) => activeCategoryIds.has(source) && target === activeModeId).map(([source, target]) => edgeKey(source, target))); const guideEdgeKeys = new Set(edges.filter(([source, target]) => source === activeModeId && target === activeGuideId).map(([source, target]) => edgeKey(source, target))); const instancePathIds = new Set([...activeRootIds, ...activeCategoryIds, activeModeId, activeGuideId].filter(Boolean));
    let rotX = -.12, rotY = -.3, zoom = 1, drag = false, px = 0, py = 0, dragDistance = 0, alive = true, frame = 0, autoRotate = !window.matchMedia('(prefers-reduced-motion: reduce)').matches, showLabels = true, focusGroup = null, focusNodeId = null, focusIndex = -1, lastProjected = new Map(), demoActive = Boolean(data), demoStart = performance.now(), demoPhase = -1;
    rotationButton.classList.toggle('active', autoRotate); rotationButton.textContent = autoRotate ? '暂停旋转' : '继续旋转';
    if (demoActive) { overviewButton.classList.remove('active'); replayButton?.classList.add('active'); toolbarNote.textContent = '正在演示该号码画像如何推导出推荐接待模式。'; }
    const context = canvas.getContext('2d'); const palette = {proficiency: ['#c8d5ff','#4968d3'], emotion: ['#ffc5cf','#c94f69'], facts: ['#a4efdf','#218a7c'], mode: ['#ffdc9f','#b46c1f'], guidance: ['#efb9df','#8c4777']};
    function resize() { const rect = canvas.getBoundingClientRect(); const ratio = Math.min(devicePixelRatio || 1, 2); canvas.width = rect.width * ratio; canvas.height = rect.height * ratio; context.setTransform(ratio, 0, 0, ratio, 0, 0); }
    function project(node) { let x = node.x - sceneCenterX, y = node.y, z = node.z; const cy = Math.cos(rotY), sy = Math.sin(rotY), cx = Math.cos(rotX), sx = Math.sin(rotX); const x1 = x * cy - z * sy, z1 = x * sy + z * cy; const y1 = y * cx - z1 * sx, z2 = y * sx + z1 * cx; const perspective = 760 / (900 + z2); const visualOffsetX = Math.min(52, canvas.clientWidth * .045); return {x: canvas.clientWidth / 2 - visualOffsetX + x1 * perspective * zoom, y: canvas.clientHeight / 2 + y1 * perspective * zoom, scale: perspective * zoom, depth: z2}; }
    const clamp = value => Math.max(0, Math.min(1, value));
    function demoProgress(elapsed) { return {input: clamp((elapsed - 450) / 1150), rule: clamp((elapsed - 1750) / 1500), guide: clamp((elapsed - 3550) / 950), total: clamp(elapsed / 4800)}; }
    function updateDerivationStatus(elapsed) {
      if (!derivationStatus || !derivationTitle || !derivationCopy || !derivationBar) return; const mode = text(data?.before?.result?.service_mode); const signature = ((instance?.items || []).map(item => `${item.name}：${item.value}`)).join(' · '); let phase = 0, title = '准备画像推导', copy = '正在读取该号码的历史画像信息…';
      if (elapsed >= 450) { phase = 1; title = '点亮当前画像'; copy = signature || '正在确认当前画像标签。'; }
      if (elapsed >= 1750) { phase = 2; title = '匹配接待模式'; copy = '沿有效画像关系逐步核对接待重点。'; }
      if (elapsed >= 3550) { phase = 3; title = `推荐模式：${mode}`; copy = text(data?.before?.result?.strategy_reason); }
      if (elapsed >= 4550) { phase = 4; title = '推导完成'; copy = `${mode} · ${text(data?.before?.result?.service_suggestion)}`; }
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
      if (!focusGroup) return new Set(nodes.map(node => node.id)); const ids = new Set(nodes.filter(node => node.group === focusGroup).map(node => node.id)); edges.forEach(([source, target]) => { if (ids.has(source)) ids.add(target); }); edges.forEach(([source, target]) => { if (ids.has(source) && nodeMap.get(source)?.kind === 'mode') ids.add(target); }); return ids;
    }
    function draw(timestamp = 0) {
      if (!alive) return; if (autoRotate && !drag) rotY += .0015; context.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
      const projected = new Map(nodes.map(node => [node.id, project(node)])); const focused = focusedNodeIds(); const elapsed = Math.max(0, timestamp - demoStart); const progress = demoProgress(elapsed); const hasFocus = Boolean(demoActive || focusGroup || focusNodeId); if (demoActive) updateDerivationStatus(elapsed);
      edges.forEach(([source, target], index) => {
        const p1 = projected.get(source), p2 = projected.get(target); if (!p1 || !p2) return; const key = edgeKey(source, target); const activeEdge = focused.has(source) && focused.has(target); let animationProgress = null;
        if (demoActive) { if (inputEdgeKeys.has(key)) animationProgress = progress.input; else if (ruleEdgeKeys.has(key)) animationProgress = progress.rule; else if (guideEdgeKeys.has(key)) animationProgress = progress.guide; }
        context.globalAlpha = demoActive ? (animationProgress === null ? .035 : .18) : hasFocus ? (activeEdge ? .7 : .06) : 1; context.strokeStyle = 'rgba(119,158,190,.35)'; context.lineWidth = 1; context.beginPath(); context.moveTo(p1.x,p1.y); context.lineTo(p2.x,p2.y); context.stroke();
        if (demoActive && animationProgress !== null && animationProgress > 0) { const endX = p1.x + (p2.x - p1.x) * animationProgress, endY = p1.y + (p2.y - p1.y) * animationProgress; context.globalAlpha = .92; context.strokeStyle = '#86e5d3'; context.lineWidth = 2.2; context.beginPath(); context.moveTo(p1.x,p1.y); context.lineTo(endX,endY); context.stroke(); context.fillStyle = '#d0fff6'; context.shadowBlur = 12; context.shadowColor = '#74e1ce'; context.beginPath(); context.arc(endX,endY,2.8,0,Math.PI*2); context.fill(); context.shadowBlur = 0; }
        else if (!demoActive && activeEdge && (!hasFocus || index % 2 === 0)) { const moving = (timestamp * .00016 + index * .093) % 1; const dotX = p1.x + (p2.x - p1.x) * moving, dotY = p1.y + (p2.y - p1.y) * moving; context.globalAlpha = hasFocus ? .95 : .55; context.fillStyle = '#a8f4e7'; context.beginPath(); context.arc(dotX,dotY,hasFocus ? 2.2 : 1.5,0,Math.PI*2); context.fill(); }
      });
      context.globalAlpha = 1; lastProjected = projected;
      [...nodes].sort((a,b) => project(b).depth - project(a).depth).forEach(node => {
        const point = projected.get(node.id); const isInput = activeRootIds.has(node.id) || activeCategoryIds.has(node.id); const isMode = node.id === activeModeId; const isGuide = node.id === activeGuideId; let reveal = 1;
        if (demoActive) { reveal = isInput ? progress.input : isMode ? progress.rule : isGuide ? progress.guide : .04; }
        const pulseTarget = node.current || focusNodeId === node.id || (demoActive && instancePathIds.has(node.id)); const pulse = pulseTarget ? 1 + Math.sin(timestamp * .004) * .07 : 1; const r = Math.max(5, node.r * point.scale * pulse); point.hitRadius = r; const [light,dark] = palette[node.group] || palette[node.kind] || palette.guidance;
        context.globalAlpha = demoActive ? Math.max(.04, reveal) : hasFocus && !focused.has(node.id) ? .16 : 1; const gradient = context.createRadialGradient(point.x-r*.35, point.y-r*.35, 1, point.x, point.y, r); gradient.addColorStop(0, light); gradient.addColorStop(1, dark); const highlighted = demoActive ? reveal > .7 && instancePathIds.has(node.id) : node.current || focusNodeId === node.id; context.shadowBlur = highlighted ? 19 : 5; context.shadowColor = isMode ? '#ffc66f' : isGuide ? '#eba7d5' : '#8fe7d8'; context.fillStyle = gradient; context.beginPath(); context.arc(point.x,point.y,r,0,Math.PI*2); context.fill(); context.shadowBlur = 0;
        if (highlighted) { const ringColor = isMode ? '#ffc66f' : isGuide ? '#e7a1d1' : '#8edfd0'; context.strokeStyle = ringColor; context.lineWidth = 2; context.beginPath(); context.arc(point.x,point.y,r+5,0,Math.PI*2); context.stroke(); }
        const showDemoLabel = demoActive && reveal > .5 && instancePathIds.has(node.id); if ((demoActive && showDemoLabel) || (!demoActive && (showLabels || node.current || (hasFocus && focused.has(node.id))))) { context.fillStyle = '#edf4ff'; context.font = `${node.current ? 700 : 600} 11px system-ui`; context.fillText(node.label.length > 18 ? `${node.label.slice(0,18)}…` : node.label, point.x+r+5, point.y+3); } context.globalAlpha = 1;
      }); frame = requestAnimationFrame(draw);
    }
    canvas.addEventListener('pointerdown', event => { drag = true; dragDistance = 0; px = event.clientX; py = event.clientY; canvas.setPointerCapture(event.pointerId); }); canvas.addEventListener('pointermove', event => { if (!drag) return; const dx = event.clientX - px, dy = event.clientY - py; dragDistance += Math.hypot(dx, dy); rotY += dx * .006; rotX = Math.max(-1, Math.min(1, rotX + dy * .004)); px = event.clientX; py = event.clientY; }); canvas.addEventListener('pointerup', event => { drag = false; canvas.releasePointerCapture(event.pointerId); if (dragDistance >= 5) return; const rect = canvas.getBoundingClientRect(), x = event.clientX - rect.left, y = event.clientY - rect.top; const clicked = [...nodes].reverse().find(node => { const point = lastProjected.get(node.id); return point && Math.hypot(point.x - x, point.y - y) <= (point.hitRadius || 7) + 7; }); if (!clicked) return; demoActive = false; replayButton?.classList.remove('active'); if (derivationTitle && derivationCopy) { derivationTitle.textContent = '手动关系聚焦'; derivationCopy.textContent = '再次点击当前小球可取消聚焦，或点击“重播推导”恢复演示。'; } focusNodeId = focusNodeId === clicked.id ? null : clicked.id; focusGroup = null; focusIndex = -1; overviewButton.classList.toggle('active', !focusNodeId); toolbarNote.textContent = focusNodeId ? `当前聚焦：${clicked.label}，相关画像依据和接待建议已高亮。` : '当前展示三个画像维度及其与四种接待模式的完整关系。'; }); canvas.addEventListener('wheel', event => { event.preventDefault(); zoom = Math.max(.6, Math.min(1.7, zoom * (event.deltaY > 0 ? .92 : 1.08))); }, {passive: false});
    resetButton.addEventListener('click', () => { rotX = -.12; rotY = -.3; zoom = 1; demoActive = false; replayButton?.classList.remove('active'); focusGroup = null; focusNodeId = null; focusIndex = -1; overviewButton.classList.add('active'); toolbarNote.textContent = '专业程度、近期情绪和历史服务事实通过颜色区分，并共同连接到坐席接待模式。'; });
    rotationButton.addEventListener('click', () => { autoRotate = !autoRotate; rotationButton.classList.toggle('active', autoRotate); rotationButton.textContent = autoRotate ? '暂停旋转' : '继续旋转'; });
    labelsButton.addEventListener('click', () => { showLabels = !showLabels; labelsButton.classList.toggle('active', showLabels); labelsButton.textContent = showLabels ? '隐藏标签' : '显示标签'; });
    overviewButton.addEventListener('click', () => { demoActive = false; replayButton?.classList.remove('active'); focusGroup = null; focusNodeId = null; focusIndex = -1; zoom = 1; overviewButton.classList.add('active'); toolbarNote.textContent = '当前展示三个画像维度及其与四种接待模式的完整关系。'; });
    nextButton.addEventListener('click', () => { const groups = dimensions.map(item => item.id); if (!groups.length) return; demoActive = false; replayButton?.classList.remove('active'); focusIndex = (focusIndex + 1) % groups.length; focusGroup = groups[focusIndex]; focusNodeId = null; zoom = 1.12; overviewButton.classList.remove('active'); toolbarNote.textContent = `当前聚焦：${zoneConfig[focusGroup]?.label || dimensions[focusIndex].name}，相关接待模式同步高亮。`; });
    replayButton?.addEventListener('click', () => { demoActive = true; demoStart = performance.now(); demoPhase = -1; focusGroup = null; focusNodeId = null; focusIndex = -1; rotX = -.12; rotY = -.3; zoom = 1; overviewButton.classList.remove('active'); replayButton.classList.add('active'); toolbarNote.textContent = '正在演示该号码画像如何推导出推荐接待模式。'; });
    const observer = new ResizeObserver(resize); observer.observe(canvas); requestAnimationFrame(() => { resize(); draw(); }); state.graphCleanup = () => { alive = false; cancelAnimationFrame(frame); observer.disconnect(); };
  }

  function renderClassificationCatalog(data) {
    const taxonomy = state.showcaseCatalog.taxonomy || {}; const panel = showcasePanel('完整分类与判定规则', data ? '当前号码命中项已在图中高亮' : '三维特征、五项事实、四种接待模式');
    const activeLabels = new Set(); ((data?.before?.profile_model || {}).items || []).forEach(item => (item.values || []).forEach(value => activeLabels.add(`${item.id}:${value}`))); const activeMode = data?.before?.result?.service_mode;
    const dimensions = el('section', 'taxonomy-section'); const dHead = el('div', 'taxonomy-section-head'); dHead.append(el('strong', '', '三维画像字段'), el('span', '', data ? '蓝色标签为当前画像' : '用于识别当前服务需求')); const dGrid = el('div', 'dimension-catalog'); (taxonomy.dimensions || []).forEach(dimension => { const hasCurrent = data && ((data.before.profile_model?.items || []).some(item => item.id === dimension.id)); const card = el('article', `dimension-card${hasCurrent ? ' active' : ''}`); const head = el('div', 'dimension-card-head'); head.append(el('strong', '', dimension.name), el('span', '', `${dimension.categories.length} 类`)); const tags = el('div', 'taxonomy-tags'); [...dimension.categories, dimension.unknown].forEach(category => tags.append(el('span', activeLabels.has(`${dimension.id}:${category}`) ? 'active' : '', category))); card.append(head, el('p', '', dimension.description), tags); dGrid.append(card); }); dimensions.append(dHead, dGrid);
    const modes = el('section', 'taxonomy-section'); const mHead = el('div', 'taxonomy-section-head'); mHead.append(el('strong', '', '四种坐席接待模式'), el('span', '', data ? '当前推荐模式同步突出' : '根据近期状态匹配接待重点')); const mGrid = el('div', 'service-mode-catalog'); (taxonomy.service_modes || []).forEach(mode => { const card = el('article', `service-mode-card${activeMode === mode.label ? ' active' : ''}`); card.append(el('strong', '', mode.label), el('p', '', mode.focus), el('div', 'composite-meta', `适用情形：${mode.rule}`), el('div', 'composite-meta', `沟通建议：${mode.communication}`)); mGrid.append(card); }); modes.append(mHead, mGrid); panel.body.append(dimensions, modes); return panel.panel;
  }

  function profileOption(item, index) {
    const option = el('option', '', `${String(index + 1).padStart(2, '0')} · ${item.masked_phone} · ${item.recommended_mode}`); option.value = item.profile_key; return option;
  }

  function filterShowcaseProfiles(query = '') {
    const select = document.querySelector('#showcase-profile-select'); const items = state.showcaseCatalog?.items || []; const normalized = query.trim().toLowerCase(); const previous = select.value;
    const matched = items.filter((item, index) => !normalized || [String(index + 1), String(index + 1).padStart(2, '0'), item.label, item.masked_phone, item.recommended_mode, item.proficiency_level, item.emotion_state].some(value => String(value || '').toLowerCase().includes(normalized)));
    select.replaceChildren(); matched.forEach(item => select.append(profileOption(item, items.indexOf(item)))); select.disabled = !matched.length;
    if (matched.some(item => item.profile_key === previous)) select.value = previous;
    const meta = document.querySelector('#showcase-index-meta'); meta.textContent = matched.length ? `已定位 ${matched.length} / ${items.length} 个号码画像` : '未找到匹配的号码画像';
    return previous !== select.value && Boolean(select.value);
  }

  async function loadShowcaseCatalog() {
    try {
      const catalog = await api('/api/showcase/catalog'); state.showcaseCatalog = catalog; const select = document.querySelector('#showcase-profile-select'); const knowledgeSelect = document.querySelector('#knowledge-profile-select'); filterShowcaseProfiles(); knowledgeSelect.replaceChildren(); (catalog.items || []).forEach((item, index) => knowledgeSelect.append(profileOption(item, index))); knowledgeSelect.disabled = !(catalog.items || []).length;
      const scenarios = document.querySelector('#showcase-scenarios'); scenarios.replaceChildren(); (catalog.scenarios || []).forEach((item, index) => { const button = el('button', `scenario-tab${item.id === state.showcaseScenario ? ' active' : ''}`); button.type = 'button'; const copy = el('div'); copy.append(el('strong', '', item.label), el('span', '', item.description)); button.append(el('i', '', String(index + 1).padStart(2, '0')), copy); button.addEventListener('click', () => { state.showcaseScenario = item.id; scenarios.querySelectorAll('button').forEach(node => node.classList.toggle('active', node === button)); loadShowcase(); }); scenarios.append(button); }); if (select.value) loadShowcase(); renderGraph(null);
    } catch (error) { document.querySelector('#showcase-status').textContent = `画像目录读取失败：${error.message}`; }
  }
  async function loadShowcase() { const key = document.querySelector('#showcase-profile-select').value; if (!key) return; const request = ++state.showcaseRequest; const status = document.querySelector('#showcase-status'); status.textContent = '正在回放历史信息并生成推演结果…'; try { const data = await api('/api/showcase', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({profile_key:key, scenario:state.showcaseScenario})}); if (request !== state.showcaseRequest) return; renderInference(data); status.textContent = `${data.masked_phone} · ${data.scenario.label} · 情景推演结果仅供参考`; } catch(error) { if (request === state.showcaseRequest) status.textContent = `推演失败：${error.message}`; } }
  document.querySelector('#showcase-profile-select').addEventListener('change', loadShowcase);
  let showcaseSearchTimer = 0; document.querySelector('#showcase-profile-search').addEventListener('input', event => { clearTimeout(showcaseSearchTimer); const changed = filterShowcaseProfiles(event.target.value); showcaseSearchTimer = setTimeout(() => { if (changed) loadShowcase(); }, 180); });
  document.querySelectorAll('[data-showcase-module]').forEach(button => button.addEventListener('click', () => { const knowledge = button.dataset.showcaseModule === 'knowledge'; document.querySelector('#showcase-inference-module').classList.toggle('hidden', knowledge); document.querySelector('#showcase-knowledge-module').classList.toggle('hidden', !knowledge); document.querySelectorAll('[data-showcase-module]').forEach(node => node.classList.toggle('active', node === button)); if (knowledge) renderGraph(null); }));
  document.querySelectorAll('[data-knowledge-mode]').forEach(button => button.addEventListener('click', async () => { const instance = button.dataset.knowledgeMode === 'instance'; document.querySelectorAll('[data-knowledge-mode]').forEach(node => node.classList.toggle('active', node === button)); document.querySelector('#knowledge-profile-wrap').classList.toggle('hidden', !instance); if (!instance) { document.querySelector('#knowledge-mode-status').textContent = '当前展示整体画像方法论，不关联具体号码。'; renderGraph(null); } else { await loadGraphInstance(); } }));
  async function loadGraphInstance() { const key = document.querySelector('#knowledge-profile-select').value; if (!key) return; try { const data = await api('/api/showcase', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({profile_key:key, scenario:'baseline'})}); document.querySelector('#knowledge-mode-status').textContent = `${data.masked_phone} · 仅高亮该号码当前画像，不影响增量推演。`; renderGraph(data); } catch(error) { document.querySelector('#knowledge-mode-status').textContent = `读取失败：${error.message}`; } }
  document.querySelector('#knowledge-profile-select').addEventListener('change', loadGraphInstance);

  async function loadUsers() {
    const body = document.querySelector('#user-body'); body.innerHTML = '<tr><td class="table-empty" colspan="6">正在读取用户…</td></tr>';
    try { const data = await api('/api/users'); body.replaceChildren(); data.items.forEach(user => { const row = el('tr'); const roleSelect = el('select'); roleSelect.innerHTML = '<option value="agent">坐席</option><option value="admin">管理员</option>'; roleSelect.value = user.role; roleSelect.addEventListener('change', () => updateUser(user.id, {role: roleSelect.value})); const status = el('span', `user-status ${user.is_active ? 'active' : 'disabled'}`, user.is_active ? '已启用' : '已停用'); const actions = el('div'); const toggle = el('button', 'button secondary compact', user.is_active ? '停用' : '启用'); toggle.addEventListener('click', () => updateUser(user.id, {is_active: !user.is_active})); const reset = el('button', 'button ghost compact', '重置密码'); reset.addEventListener('click', () => { const password = prompt('请输入至少8位的新密码'); if (password) updateUser(user.id, {password}); }); actions.append(toggle, reset); [user.username, user.display_name].forEach(value => { const cell = el('td', '', value); row.append(cell); }); const roleCell = el('td'); roleCell.append(roleSelect); row.append(roleCell); const statusCell = el('td'); statusCell.append(status); row.append(statusCell, el('td', '', dateText(user.created_at))); const actionCell = el('td'); actionCell.append(actions); row.append(actionCell); body.append(row); }); } catch(error) { body.innerHTML = `<tr><td class="table-empty" colspan="6">读取失败：${error.message}</td></tr>`; }
  }
  async function updateUser(userId, fields) { try { await api('/api/users/update', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({user_id:userId, ...fields})}); loadUsers(); } catch(error) { alert(error.message); loadUsers(); } }
  document.querySelector('#user-form').addEventListener('submit', async event => { event.preventDefault(); const notice = document.querySelector('#user-notice'); try { await api('/api/users/create', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({username:document.querySelector('#new-username').value.trim(), display_name:document.querySelector('#new-display-name').value.trim(), password:document.querySelector('#new-password').value, role:document.querySelector('#new-role').value})}); event.target.reset(); notice.textContent = '用户创建成功。'; notice.className = 'notice'; loadUsers(); } catch(error) { notice.textContent = error.message; notice.className = 'notice error'; } });
  document.querySelector('#refresh-users').addEventListener('click', loadUsers);

  restoreSession();
})();
