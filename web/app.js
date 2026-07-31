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
  const titles = {workbench: '坐席辅助工作台', dashboard: '热线数据概览', history: '历史来电记录', showcase: '画像推演中心', users: '用户与权限'};
  const state = {
    user: null,
    phone: '',
    sessions: [],
    activeSessionId: null,
    sessionFilter: 'all',
    trajectoryFilter: 'all',
    callClockTimer: null,
    dashboardLoaded: false,
    history: {page: 1, totalPages: 0, phone: '', loaded: false},
    showcaseCatalog: null,
    knowledgeSearchRequest: 0,
    knowledgeGraphRequest: 0,
    knowledgeViewMode: 'overall',
    graphCleanup: null,
    showcaseResultHintShown: (() => {
      try { return window.sessionStorage.getItem('showcase-result-hint-seen') === '1'; }
      catch { return false; }
    })(),
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
    document.querySelector('#account-name').textContent = user.display_name;
    document.querySelector('#account-display-name').textContent = user.display_name;
    document.querySelector('#account-role').textContent = user.role_label;
    document.querySelector('#account-avatar').textContent = user.role === 'admin' ? '管' : '坐';
    document.querySelector('#account-users').classList.toggle('hidden', user.role !== 'admin');
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
  document.querySelector('#account-toggle').addEventListener('click', () => {
    const popover = document.querySelector('#account-popover'); const open = popover.classList.toggle('hidden');
    document.querySelector('#account-toggle').setAttribute('aria-expanded', String(!open));
  });
  document.querySelector('#account-users').addEventListener('click', () => { document.querySelector('#account-popover').classList.add('hidden'); showPage('users'); });
  document.addEventListener('click', event => { if (!event.target.closest('.account-menu')) { document.querySelector('#account-popover').classList.add('hidden'); document.querySelector('#account-toggle').setAttribute('aria-expanded', 'false'); } });

  function showPage(name, updateHash = true) {
    let page = pages.has(name) ? name : 'workbench';
    if (state.user?.role !== 'admin' && ['showcase', 'users'].includes(page)) page = 'workbench';
    document.body.classList.toggle('workbench-active', page === 'workbench');
    if (page !== 'workbench') document.querySelector('#caller-history-overlay').classList.add('hidden');
    else if (state.activeSessionId) setHistoryTab('trajectory');
    document.querySelectorAll('.page-view').forEach(node => node.classList.toggle('hidden', node.id !== `page-${page}`));
    document.querySelectorAll('.nav-link').forEach(node => {
      const active = node.dataset.page === page;
      node.classList.toggle('active', active);
      active ? node.setAttribute('aria-current', 'page') : node.removeAttribute('aria-current');
    });
    document.querySelector('#global-location-current').textContent = titles[page];
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

  function maskLocalPhone(phone) {
    const value = String(phone || '').replace(/\D/g, '');
    if (value.length >= 8) return `${value.slice(0, 3)}${'*'.repeat(value.length - 7)}${value.slice(-4)}`;
    return value ? `${value.slice(0, 1)}***${value.slice(-1)}` : '号码待确认';
  }

  function categoryValues(value, fallback = '待识别') {
    const values = String(value || '')
      .split(/[、，,；;|/]+/)
      .map(item => item.trim())
      .filter(Boolean);
    return values.length ? values : [fallback];
  }

  function renderOverview(profile = null, phone = state.phone) {
    const box = document.querySelector('#overview');
    box.replaceChildren();
    const subject = profile?.caller_type === '企业'
      ? (profile.enterprise_identity && profile.enterprise_identity !== '无法判断' ? `企业 · ${profile.enterprise_identity}` : '企业来电人')
      : text(profile?.caller_type, '新来电人');
    const heading = el('div', 'caller-summary-head');
    heading.append(el('div', 'caller-avatar', profile?.caller_type === '企业' ? '企' : '个'));
    const copy = el('div');
    copy.append(el('strong', '', subject), el('span', '', `${maskLocalPhone(phone)} · ${profile ? '已匹配历史画像' : '暂无历史画像'}`));
    const profileTags = el('div', 'caller-profile-tags');
    [
      ['业务专业度', profile?.proficiency_level || '暂无法判断'],
      ['近期情绪', profile?.emotion_state || '暂无法判断'],
    ].forEach(([label, value]) => {
      const tag = el('div');
      tag.append(el('small', '', label), el('strong', '', value));
      profileTags.append(tag);
    });
    heading.append(copy, profileTags);
    const recent = profile?.recent_workday_statistics || {};
    const latest = profile?.trajectories?.[0] || null;
    const latestUnresolved = profile?.latest_resolved === false;
    const focus = el('section', `precall-focus${latestUnresolved ? ' attention' : ''}`);
    focus.append(el('span', 'precall-focus-kicker', '上次来电信息'));
    const focusStatus = el('div', 'precall-focus-status');
    focusStatus.append(
      el('span', '', '状态：'),
      el('strong', '', !profile ? '无历史记录' : latestUnresolved ? '未直接解决' : profile.latest_resolved === true ? '已直接解决' : '待判断'),
    );
    [
      [latest?.taxpayer_dissatisfied, '对坐席不满'],
      [latest?.work_order, '历史工单'],
      [latest?.contact_unresolved, '联系部门未解决'],
      [latest?.abnormal_end, '异常中断'],
      [latest?.is_repeated_issue, '重复咨询'],
      [latest?.wait_pushback, '存在等待或推诿'],
    ].forEach(([active, label]) => {
      if (active) focusStatus.append(el('span', 'precall-focus-tag', label));
    });
    const focusQuestion = el(
      'div',
      'precall-focus-question',
      profile?.latest_question || '暂无历史咨询，本次先确认来电诉求',
    );
    const focusAnswer = el('div', 'precall-focus-answer');
    focusAnswer.append(
      el('span', '', '上次坐席答复'),
      el('p', '', profile?.latest_agent_answer || '暂无明确记录'),
    );
    focus.append(focusStatus, focusQuestion, focusAnswer);
    const metadata = el('div', 'precall-metadata');
    [
      ['最近来电', profile?.latest_call_time || '首次接入', false],
      ['登记单位', profile?.latest_registration_unit || '暂无', false],
      ['专题类别', profile?.latest_topic_category || '待识别', true],
      ['需求类别', profile?.latest_demand_category || '待识别', true],
    ].forEach(([label, value, category]) => {
      const item = el('div', `precall-metadata-item${category ? ' category' : ''}`);
      item.append(el('small', '', label));
      if (category) {
        const values = el('div', 'precall-metadata-values');
        categoryValues(value).forEach(entry => values.append(el('span', '', entry)));
        item.append(values);
      } else {
        item.append(el('span', '', value));
      }
      metadata.append(item);
    });
    heading.append(metadata);
    const stats = el('div', 'overview-signal-table');
    [
      ['历史来电', '历史来电', recent.call_count ?? profile?.total_call_count ?? 0],
      ['重复诉求', '重复诉求', recent.repeated_issue_count ?? 0],
      ['历史工单', '历史工单', recent.work_order_count ?? 0],
      ['存在联系相关部门或人员且未解决', '联系未解决', recent.contact_unresolved_count ?? 0],
      ['服务不满', '服务不满', recent.dissatisfaction_count ?? 0],
      ['未直接解决', '未直接解决', recent.unresolved_count ?? 0],
    ].forEach(([fullLabel, label, value]) => {
      const item = el('div', 'overview-signal');
      item.title = fullLabel;
      item.append(el('span', '', label), el('strong', '', String(value)));
      stats.append(item);
    });
    box.append(heading, focus, stats);
    document.querySelector('#question-topic').textContent = profile?.latest_topic_category || '专题待识别';
    document.querySelector('#question-demand').textContent = profile?.latest_demand_category || '需求待识别';
    document.querySelector('#overview-range').textContent = recent.start_date && recent.end_date
      ? `${recent.start_date} 至 ${recent.end_date}`
      : '近5个工作日 · 仅供参考';
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
    if (item.contact_unresolved) facts.push('存在联系相关部门或人员且未解决');
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

  const trajectoryFilterLabels = {
    all: '全部诉求',
    repeated: '重复咨询',
    unresolved: '未直接解决',
    order: '历史工单',
    followup: '后续追问',
    contact: '存在联系相关部门或人员且未解决',
    dissatisfaction: '服务不满',
  };

  function trajectoryGroups(profile) {
    const groups = new Map();
    (profile?.trajectories || []).forEach(item => {
      const question = text(
        item.is_repeated_issue && item.matched_previous_question
          ? item.matched_previous_question
          : item.core_question || item.question_category,
        '问题待进一步确认',
      );
      const key = question.replace(/[\s，。；、？！,.!?;]+/g, '').toLowerCase();
      const group = groups.get(key) || {
        question, items: [], repeated: false, unresolved: false,
        order: false, followup: false, contact: false, dissatisfaction: false,
      };
      group.items.push(item);
      group.repeated ||= item.is_repeated_issue === true;
      group.unresolved ||= item.resolved === false;
      group.order ||= item.work_order === true;
      group.followup ||= group.items.length > 1 || Boolean(item.matched_previous_call_time);
      group.contact ||= item.contact_unresolved === true;
      group.dissatisfaction ||= item.taxpayer_dissatisfied === true;
      groups.set(key, group);
    });
    return [...groups.values()].sort((a, b) => {
      const aTime = a.items[0]?.call_time || '';
      const bTime = b.items[0]?.call_time || '';
      return String(bTime).localeCompare(String(aTime));
    });
  }

  function renderHistoryFocus(profile) {
    const box = document.querySelector('#profile');
    const filters = document.querySelector('#trajectory-filters');
    box.className = 'trajectory-content';
    box.replaceChildren();
    filters.replaceChildren();
    const groups = trajectoryGroups(profile);
    Object.entries(trajectoryFilterLabels).forEach(([key, label]) => {
      const count = key === 'all' ? groups.length : groups.filter(group => group[key]).length;
      const button = el('button', `trajectory-filter${state.trajectoryFilter === key ? ' active' : ''}`, `${label} ${count}`);
      button.type = 'button';
      button.dataset.trajectoryFilter = key;
      button.addEventListener('click', () => {
        state.trajectoryFilter = key;
        renderHistoryFocus(profile);
      });
      filters.append(button);
    });
    const visible = state.trajectoryFilter === 'all' ? groups : groups.filter(group => group[state.trajectoryFilter]);
    if (!visible.length) {
      box.className = 'trajectory-content placeholder';
      box.textContent = groups.length ? '当前筛选条件下没有匹配的问题轨迹。' : '该号码暂无历史问题轨迹。';
      return;
    }
    const list = el('div', 'trajectory-list');
    visible.forEach((group, index) => {
      const latest = group.items[0];
      const earliest = group.items[group.items.length - 1];
      const card = el('article', 'trajectory-card');
      card.append(el('div', 'trajectory-marker', String(index + 1).padStart(2, '0')));
      const main = el('div', 'trajectory-main');
      main.append(el('strong', '', group.question));
      const answer = latest.resolved === false
        ? `最近一次未直接解决：${text(latest.unresolved_reason, '原因待补充')}`
        : `最近处理：${text(latest.agent_answer_summary, resolvedText(latest.resolved))}`;
      main.append(el('p', '', answer));
      const tags = el('div', 'trajectory-tags');
      if (group.repeated) tags.append(el('span', 'trajectory-tag repeated', '重复咨询'));
      if (group.unresolved) tags.append(el('span', 'trajectory-tag unresolved', '未直接解决'));
      if (group.order) tags.append(el('span', 'trajectory-tag order', '历史工单'));
      if (group.followup) tags.append(el('span', 'trajectory-tag followup', `后续追问 · ${group.items.length}次`));
      if (group.contact) tags.append(el('span', 'trajectory-tag unresolved', '存在联系相关部门或人员且未解决'));
      if (group.dissatisfaction) tags.append(el('span', 'trajectory-tag unresolved', '服务不满'));
      if (!tags.children.length) tags.append(el('span', 'trajectory-tag', '已直接答复'));
      main.append(tags);
      const meta = el('div', 'trajectory-meta');
      meta.append(el('time', '', dateText(latest.call_time)));
      if (group.followup) meta.append(el('div', '', `始于 ${dateText(earliest.call_time).slice(0, 10)}`));
      const followupAction = el('button', 'trajectory-action primary', '跟进此问题');
      followupAction.type = 'button';
      followupAction.addEventListener('click', () => startHistoryFollowup(group));
      const detailAction = el('button', 'trajectory-action', '查看最近一通 →');
      detailAction.type = 'button';
      detailAction.addEventListener('click', () => openDetail(latest.business_id));
      meta.append(followupAction, detailAction);
      card.append(main, meta);
      list.append(card);
    });
    box.append(list);
    document.querySelector('#view-current-history').classList.remove('hidden');
  }

  function bulletSection(parent, title, values) {
    if (!values || !values.length) return;
    const section = el('section', 'advice-section'); section.append(el('h3', '', title));
    const list = el('ul'); values.forEach(value => list.append(el('li', '', value))); section.append(list); parent.append(section);
  }

  function renderAdvice(advice) {
    const box = document.querySelector('#advice');
    box.className = 'precall-advice-body';
    box.replaceChildren();
    const summary = el('div', 'advice-summary', text(advice.advice_summary));
    summary.setAttribute('aria-label', '总体接待建议');
    const modes = el('div', 'precall-mode-list');
    modes.setAttribute('aria-label', '组合接待策略');
    (advice.service_modes || []).forEach(component => {
      const item = el('div', `precall-mode ${modeClass(component)}`.trim());
      item.title = text(component.basis, '依据当前历史信息确定。');
      item.append(el('strong', '', text(component.mode)));
      modes.append(item);
    });
    if (!modes.children.length) {
      const item = el('div', 'precall-mode');
      item.append(el('strong', '', text(advice.service_mode, '先确认本次诉求')));
      modes.append(item);
    }
    box.append(modes, summary);
    const badge = document.querySelector('#advice-badge'); badge.classList.remove('hidden');
    badge.textContent = advice.generation_status === 'model_generated' ? '智能实时建议' : '系统辅助建议';
    badge.className = `badge${advice.generation_status === 'model_generated' ? '' : ' fallback'}`;
  }

  function currentSession() {
    return state.sessions.find(session => session.id === state.activeSessionId) || null;
  }

  function persistCurrentSessionFields() {
    const session = currentSession();
    if (!session) return;
    if (session.processingMode === 'followup') {
      session.question = session.followupQuestion || '';
      return;
    }
    session.newQuestion = document.querySelector('#current-question').value.trim();
    session.question = session.newQuestion;
  }

  function sessionTime(session) {
    return new Date(session.completedAt || session.startedAt || session.createdAt).toLocaleTimeString(
      'zh-CN', {hour: '2-digit', minute: '2-digit', hour12: false},
    );
  }

  function renderSessionList() {
    const list = document.querySelector('#session-list');
    list.replaceChildren();
    const matchesFilter = session => state.sessionFilter === 'all'
      || (state.sessionFilter === 'completed' ? session.status === 'completed' : session.status !== 'completed');
    const sessions = state.sessions.filter(matchesFilter);
    document.querySelector('#inbox-count').textContent = String(state.sessions.filter(session => session.status !== 'completed').length);
    if (!sessions.length) {
      list.append(el('div', 'session-empty', state.sessions.length ? '当前筛选条件下暂无会话' : '暂无来电会话\n输入号码模拟第一通来电'));
      return;
    }
    [
      ['来电呼入', sessions.filter(session => session.status === 'ringing')],
      ['当前来电', sessions.filter(session => session.status === 'active')],
      ['待跟进', sessions.filter(session => session.status === 'pending')],
      ['已完成', sessions.filter(session => session.status === 'completed')],
    ].forEach(([label, items]) => {
      if (!items.length) return;
      const groupLabel = el('div', 'session-group-label');
      groupLabel.append(el('span', '', label), el('span', '', String(items.length)));
      list.append(groupLabel);
      items.forEach(session => {
        const button = el('button', `session-item ${session.status === 'completed' ? 'completed' : ''} ${session.status === 'ringing' ? 'ringing' : ''}${session.id === state.activeSessionId ? ' selected' : ''}`.trim());
        button.type = 'button';
        button.append(el('span', 'session-signal'));
        const copy = el('span', 'session-copy');
        copy.append(
          el('strong', '', maskLocalPhone(session.phone)),
          el('span', '', session.status === 'ringing' && session.profileLoading
            ? '呼入中 · 正在调取历史'
            : session.question || session.profile?.latest_question || '本次诉求待确认'),
        );
        button.append(copy, el('time', 'session-time', sessionTime(session)));
        const tags = el('span', 'session-tags');
        if (session.profile?.caller_type) tags.append(el('span', 'session-tag', session.profile.caller_type));
        if (session.status === 'ringing') tags.append(el('span', 'session-tag', '等待接听'));
        if (session.declined) tags.append(el('span', 'session-tag alert', '未接听'));
        if (session.resolved === true) tags.append(el('span', 'session-tag', '已解决'));
        if (session.resolved === false) tags.append(el('span', 'session-tag alert', '未直接解决'));
        if (session.workOrder) tags.append(el('span', 'session-tag alert', '已建工单'));
        if (session.profile?.recent_workday_statistics?.unresolved_count) tags.append(el('span', 'session-tag alert', '历史未解决'));
        if (tags.children.length) button.append(tags);
        button.addEventListener('click', () => selectSession(session.id));
        list.append(button);
      });
    });
  }

  function updateCallClock(session) {
    window.clearInterval(state.callClockTimer);
    state.callClockTimer = null;
    const clock = document.querySelector('#call-clock');
    if (!session || session.status !== 'active') {
      clock.classList.add('hidden');
      return;
    }
    clock.classList.remove('hidden');
    const update = () => {
      const seconds = Math.max(0, Math.floor((Date.now() - session.startedAt) / 1000));
      clock.querySelector('strong').textContent = `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
    };
    update();
    state.callClockTimer = window.setInterval(update, 1000);
  }

  function renderInteractionState(session) {
    document.querySelector('#interaction-state').textContent = session.status === 'completed'
      ? '已完成 · 可继续修改'
      : session.status === 'pending' ? '待跟进' : '处理中';
    const choices = [
      ['#mark-resolved', session.resolved === true],
      ['#mark-unresolved', session.resolved === false],
      ['#no-order', session.workOrder !== true],
      ['#create-order', session.workOrder === true],
    ];
    choices.forEach(([selector, active]) => {
      const button = document.querySelector(selector);
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    });
    const resultText = session.resolved === true
      ? '直接解决'
      : session.resolved === false ? '未直接解决' : '处理结果待确认';
    document.querySelector('#interaction-summary').textContent = `${resultText} · ${session.workOrder ? '创建工单' : '无需工单'}`;
    const completeButton = document.querySelector('#complete-session');
    completeButton.textContent = session.status === 'completed' ? '重新打开' : '完成本通';
    completeButton.disabled = session.status !== 'completed' && session.resolved === null;
    completeButton.title = completeButton.disabled ? '请先确认本通处理结果' : '';
  }

  function setHistoryTab(name) {
    const trajectoryActive = name === 'trajectory';
    document.querySelector('#trajectory-tab').classList.toggle('active', trajectoryActive);
    document.querySelector('#trajectory-tab').setAttribute('aria-selected', String(trajectoryActive));
    document.querySelector('#all-calls-tab').classList.toggle('active', !trajectoryActive);
    document.querySelector('#all-calls-tab').setAttribute('aria-selected', String(!trajectoryActive));
    document.querySelector('#trajectory-pane').classList.toggle('hidden', !trajectoryActive);
    document.querySelector('#caller-history-overlay').classList.toggle('hidden', trajectoryActive);
  }

  function renderHistoryVisibility(session = currentSession(), workspaceView = session?.workspaceView) {
    const collapsed = Boolean(session && workspaceView === 'processing' && session.historyCollapsed);
    document.querySelector('#workspace-content').classList.toggle('history-collapsed', collapsed);
    document.querySelector('.workspace-history').classList.toggle('collapsed', collapsed);
    const toggle = document.querySelector('#history-collapse-toggle');
    toggle.classList.toggle('hidden', workspaceView !== 'processing');
    toggle.textContent = collapsed ? '展开记录' : '收起记录';
    toggle.setAttribute('aria-expanded', String(!collapsed));
  }

  function setHistoryCollapsed(collapsed) {
    const session = currentSession();
    if (!session) return;
    session.historyCollapsed = Boolean(collapsed);
    renderHistoryVisibility(session);
  }

  function setWorkspaceView(name, remember = true) {
    const activeSession = currentSession();
    if (name === 'processing' && activeSession?.status === 'ringing') return;
    const assistActive = name !== 'processing';
    document.querySelector('#assist-view-tab').classList.toggle('active', assistActive);
    document.querySelector('#assist-view-tab').setAttribute('aria-selected', String(assistActive));
    document.querySelector('#processing-view-tab').classList.toggle('active', !assistActive);
    document.querySelector('#processing-view-tab').setAttribute('aria-selected', String(!assistActive));
    document.querySelector('#assist-view').classList.toggle('hidden', !assistActive);
    document.querySelector('#processing-view').classList.toggle('hidden', assistActive);
    if (remember) {
      const session = currentSession();
      if (session) session.workspaceView = assistActive ? 'assist' : 'processing';
    }
    renderHistoryVisibility(activeSession, assistActive ? 'assist' : 'processing');
  }

  function renderKnowledgeAnswer(session) {
    const question = session.processingMode === 'followup'
      ? session.followupIssue?.question || session.followupQuestion || ''
      : session.newQuestion || '';
    const standardAnswer = String(session.profile?.standard_answer || '').trim();
    const answerAvailable = standardAnswer && !standardAnswer.includes('暂未接入');
    const status = document.querySelector('#knowledge-answer-status');
    const box = document.querySelector('#knowledge-answer');
    if (answerAvailable && question) {
      status.textContent = '已匹配';
      status.className = 'available';
      box.textContent = standardAnswer;
      return;
    }
    status.textContent = '知识库待接入';
    status.className = '';
    box.textContent = question
      ? `已确认问题：“${question}”。知识库接入后，将根据该问题自动检索并展示标准答案。`
      : '确认本次问题或选择历史跟进问题后，将在此自动检索并展示标准答案。';
  }

  function renderProcessingMode(session) {
    const followup = session.processingMode === 'followup';
    document.querySelector('#new-issue-mode').classList.toggle('active', !followup);
    document.querySelector('#followup-mode').classList.toggle('active', followup);
    document.querySelector('#processing-mode-note').textContent = followup
      ? '从问题轨迹选择诉求并继续处理'
      : '系统识别本次诉求，坐席可校正后处置';
    document.querySelector('#current-question-label').textContent = followup ? '本次跟进问题' : '系统识别问题 · 可修改';
    const field = document.querySelector('#current-question');
    field.value = followup
      ? session.followupQuestion || '请从下方问题轨迹选择一项进行跟进'
      : session.newQuestion || '';
    field.readOnly = followup;
    field.placeholder = followup
      ? '请从下方问题轨迹选择一项进行跟进'
      : '等待通话内容识别，也可由坐席直接补充或校正';
    field.classList.toggle('readonly', followup);
    const context = document.querySelector('#followup-context');
    context.classList.toggle('hidden', !followup);
    document.querySelector('.issue-editor-card').classList.toggle('followup-active', followup);
    if (followup) {
      document.querySelector('#followup-context-title').textContent = session.followupIssue?.question || '尚未选择历史问题';
      document.querySelector('#followup-context-meta').textContent = session.followupIssue
        ? `${session.followupIssue.items.length} 条相关记录 · 可从下方问题轨迹重新选择`
        : '请从下方问题轨迹选择一项进行跟进';
    }
    renderKnowledgeAnswer(session);
  }

  function setProcessingMode(name) {
    const session = currentSession();
    if (!session) return;
    persistCurrentSessionFields();
    session.processingMode = name === 'followup' ? 'followup' : 'new';
    session.historyCollapsed = session.processingMode === 'new';
    session.question = session.processingMode === 'followup'
      ? session.followupQuestion || ''
      : session.newQuestion || '';
    if (session.processingMode === 'followup') setHistoryTab('trajectory');
    renderProcessingMode(session);
    renderHistoryVisibility(session, 'processing');
    renderSessionList();
  }

  function startHistoryFollowup(group) {
    const session = currentSession();
    if (!session) return;
    persistCurrentSessionFields();
    session.processingMode = 'followup';
    session.followupIssue = group;
    session.followupQuestion = group.question;
    session.question = group.question;
    session.historyCollapsed = true;
    renderProcessingMode(session);
    renderSessionList();
    if (session.status !== 'ringing') setWorkspaceView('processing');
  }

  function renderSelectedSession(session) {
    document.querySelector('#workspace-empty').classList.add('hidden');
    document.querySelector('#workspace-content').classList.remove('hidden');
    document.querySelector('.call-workspace').classList.toggle('completed-session', session.status === 'completed');
    const stateLabel = document.querySelector('#call-state-label');
    stateLabel.textContent = session.status === 'ringing'
      ? '来电呼入'
      : session.status === 'completed' ? (session.declined ? '未接来电' : '会话已完成') : session.status === 'pending' ? '等待跟进' : '正在通话';
    stateLabel.className = `call-state-label${session.status === 'ringing' ? ' incoming' : session.status === 'completed' ? ' completed' : session.status === 'pending' ? ' idle' : ''}`;
    document.querySelector('#workspace-title').textContent = maskLocalPhone(session.phone);
    const incomingActions = document.querySelector('#incoming-call-actions');
    incomingActions.classList.toggle('hidden', session.status !== 'ringing');
    const acceptButton = document.querySelector('#accept-call');
    acceptButton.disabled = session.profileLoading === true;
    acceptButton.textContent = session.profileLoading ? '正在调取历史…' : '接听并处理';
    const processingTab = document.querySelector('#processing-view-tab');
    processingTab.disabled = session.status === 'ringing';
    processingTab.title = session.status === 'ringing' ? '接听后进入本通处理' : '';
    renderOverview(session.profile, session.phone);
    renderInteractionState(session);
    renderProcessingMode(session);
    updateCallClock(session);
    if (session.profile) {
      renderHistoryFocus(session.profile);
      renderCallerHistory(session.profile);
    } else {
      document.querySelector('#trajectory-filters').replaceChildren();
      const profileBox = document.querySelector('#profile');
      profileBox.className = 'trajectory-content placeholder';
      profileBox.textContent = session.profileLoading ? '正在读取该号码的问题轨迹…' : '该号码暂无历史问题轨迹，本次按首次咨询接待。';
      document.querySelector('#caller-history-summary').textContent = '暂无历史来电';
      document.querySelector('#caller-history').replaceChildren(el('div', 'placeholder', '该号码暂无可展示的历史来电。'));
    }
    if (session.advice) renderAdvice(session.advice);
    else {
      const adviceBox = document.querySelector('#advice');
      adviceBox.className = 'placeholder';
      adviceBox.textContent = session.loading ? '正在调取历史信息并生成辅助建议…' : '本次暂未生成辅助建议。';
      document.querySelector('#advice-badge').classList.add('hidden');
    }
    const initialView = session.status === 'ringing' || session.status === 'completed'
      ? 'assist'
      : session.workspaceView || 'assist';
    setWorkspaceView(initialView, false);
  }

  function selectSession(id) {
    persistCurrentSessionFields();
    const session = state.sessions.find(item => item.id === id);
    if (!session) return;
    state.activeSessionId = id;
    state.phone = session.phone;
    state.trajectoryFilter = 'all';
    renderSessionList();
    renderSelectedSession(session);
  }

  document.querySelector('#lookup-form').addEventListener('submit', async event => {
    event.preventDefault();
    const phone = document.querySelector('#phone').value.trim();
    const notice = document.querySelector('#notice');
    if (!phone) { notice.textContent = '请输入来电号码。'; notice.className = 'notice error'; return; }
    const normalizedPhone = phone.normalize('NFKC').replace(/[\s\-－—()（）]/g, '');
    if (!/^\d+$/.test(normalizedPhone)) {
      notice.textContent = '来电号码必须为数字，可包含常见空格、横线或括号。';
      notice.className = 'notice error';
      return;
    }
    persistCurrentSessionFields();
    const engaged = state.sessions.find(session => session.status === 'active' || session.status === 'ringing');
    if (engaged) {
      notice.textContent = engaged.status === 'ringing' ? '已有一通来电等待接听。' : '请先完成当前通话，再模拟新的来电。';
      notice.className = 'notice error';
      return;
    }
    const session = {
      id: `call-${Date.now()}`,
      phone: normalizedPhone,
      status: 'ringing',
      createdAt: Date.now(),
      startedAt: null,
      completedAt: null,
      profile: null,
      advice: null,
      question: '',
      newQuestion: '',
      followupQuestion: '',
      followupIssue: null,
      resolved: null,
      workOrder: false,
      workspaceView: 'assist',
      processingMode: 'new',
      historyCollapsed: false,
      profileLoading: true,
      loading: true,
    };
    state.sessions.unshift(session);
    state.activeSessionId = session.id;
    state.phone = normalizedPhone;
    renderSessionList();
    renderSelectedSession(session);
    const button = document.querySelector('#submit');
    button.disabled = true;
    notice.textContent = '模拟来电呼入，正在调取历史信息…';
    notice.className = 'notice';
    const profilePromise = api('/api/profile', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({phone: normalizedPhone}),
    });
    const advicePromise = api('/api/advice', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({phone: normalizedPhone}),
    }).then(value => ({value})).catch(error => ({error}));
    try {
      const profileResult = await profilePromise;
      session.profile = profileResult.found ? profileResult.profile : null;
      session.profileLoading = false;
      if (state.activeSessionId === session.id) renderSelectedSession(session);
      renderSessionList();
      notice.textContent = profileResult.found ? '历史信息已展示，可以接听。' : '当前号码无历史记录，可以接听。';
    } catch (error) {
      session.profileLoading = false;
      if (state.activeSessionId === session.id) renderSelectedSession(session);
      renderSessionList();
      notice.textContent = `历史读取失败，仍可接听：${error.message}`;
      notice.className = 'notice error';
    }
    const adviceResult = await advicePromise;
    session.advice = adviceResult.value?.advice || null;
    session.loading = false;
    if (state.activeSessionId === session.id) renderSelectedSession(session);
    renderSessionList();
    document.querySelector('#phone').value = '';
    button.disabled = false;
  });

  function renderCallerHistory(profile) {
    const trajectories = Array.isArray(profile.trajectories) ? profile.trajectories : [];
    const counts = {resolved: trajectories.filter(item => item.resolved === true).length, unresolved: trajectories.filter(item => item.resolved === false).length, unknown: trajectories.filter(item => item.resolved !== true && item.resolved !== false).length};
    document.querySelector('#caller-history-summary').textContent = `历史来电 ${trajectories.length} 次 · 已直接解决 ${counts.resolved} · 未直接解决 ${counts.unresolved} · 待判断 ${counts.unknown}`;
    const box = document.querySelector('#caller-history'); box.replaceChildren();
    if (!trajectories.length) { box.append(el('div', 'placeholder', '暂无可展示的历史来电。')); }
    else {
      const list = el('div', 'caller-history-list');
      const header = el('div', 'caller-history-header'); header.append(el('span', '', '来电时间 / 结果'), el('span', '', '咨询事项与处理摘要'), el('span', '', '服务线索'), el('span', '', ''));
      list.append(header);
      trajectories.forEach(item => {
        const row = el('article', `caller-history-row ${item.resolved === false ? 'unresolved' : ''}`);
        const meta = el('div', 'caller-history-meta'); meta.append(el('time', '', dateText(item.call_time)), el('span', `history-status ${item.resolved === false ? 'unresolved' : item.resolved === true ? 'resolved' : 'unknown'}`, resolvedText(item.resolved)));
        const content = el('div', 'caller-history-content'); content.append(el('strong', '', text(item.core_question || item.question_category, '问题待归类'))); const summary = item.resolved === false ? text(item.unresolved_reason, '未解决原因暂未记录') : text(item.agent_answer_summary, item.resolved === true ? '已直接解决，未形成答复提炼。' : '处理结果待进一步判断。'); content.append(el('p', '', summary));
        const facts = el('div', 'caller-history-facts'); if (item.work_order) facts.append(el('span', '', '历史工单')); if (item.contact_unresolved) facts.append(el('span', '', '存在联系相关部门或人员且未解决')); if (item.is_repeated_issue) facts.append(el('span', '', '重复诉求')); if (item.abnormal_end) facts.append(el('span', '', '异常中断'));
        const actions = el('div', 'caller-history-actions');
        const followup = el('button', 'button compact', '跟进');
        followup.type = 'button';
        followup.addEventListener('click', () => startHistoryFollowup({
          question: text(item.core_question || item.question_category, '问题待进一步确认'),
          items: [item],
        }));
        const detail = el('button', 'button secondary compact', '详情');
        detail.type = 'button';
        detail.addEventListener('click', () => openDetail(item.business_id));
        actions.append(followup, detail);
        row.append(meta, content, facts, actions);
        list.append(row);
      }); box.append(list);
    }
  }

  document.querySelector('#view-current-history').addEventListener('click', () => {
    setHistoryTab('calls');
  });
  function closeCallerHistory() { setHistoryTab('trajectory'); }
  document.querySelector('#caller-history-close').addEventListener('click', closeCallerHistory);
  document.querySelector('#trajectory-tab').addEventListener('click', () => {
    setHistoryCollapsed(false);
    setHistoryTab('trajectory');
  });
  document.querySelector('#all-calls-tab').addEventListener('click', () => {
    setHistoryCollapsed(false);
    setHistoryTab('calls');
  });
  document.querySelector('#history-collapse-toggle').addEventListener('click', () => {
    const session = currentSession();
    if (!session) return;
    setHistoryCollapsed(!session.historyCollapsed);
  });
  document.querySelector('#assist-view-tab').addEventListener('click', () => setWorkspaceView('assist'));
  document.querySelector('#processing-view-tab').addEventListener('click', () => setWorkspaceView('processing'));
  document.querySelector('#advice-glance').addEventListener('click', () => setWorkspaceView('assist'));
  document.querySelector('#new-issue-mode').addEventListener('click', () => setProcessingMode('new'));
  document.querySelector('#followup-mode').addEventListener('click', () => setProcessingMode('followup'));
  document.querySelector('#accept-call').addEventListener('click', () => {
    const session = currentSession();
    if (!session || session.status !== 'ringing' || session.profileLoading) return;
    session.status = 'active';
    session.startedAt = Date.now();
    session.workspaceView = 'processing';
    session.historyCollapsed = true;
    renderSessionList();
    renderSelectedSession(session);
    setWorkspaceView('processing');
    document.querySelector('#processing-view-tab').focus({preventScroll: true});
  });
  document.querySelector('#decline-call').addEventListener('click', () => {
    const session = currentSession();
    if (!session || session.status !== 'ringing') return;
    session.status = 'completed';
    session.declined = true;
    session.completedAt = Date.now();
    session.workspaceView = 'assist';
    renderSessionList();
    renderSelectedSession(session);
  });

  document.querySelectorAll('.inbox-filter').forEach(button => button.addEventListener('click', () => {
    state.sessionFilter = button.dataset.sessionFilter;
    document.querySelectorAll('.inbox-filter').forEach(item => item.classList.toggle('active', item === button));
    renderSessionList();
  }));
  document.querySelector('#current-question').addEventListener('input', () => {
    const session = currentSession();
    if (!session || session.processingMode === 'followup') return;
    persistCurrentSessionFields();
    renderKnowledgeAnswer(session);
    renderSessionList();
  });
  document.querySelector('#mark-resolved').addEventListener('click', () => {
    const session = currentSession();
    if (!session) return;
    session.resolved = true;
    renderInteractionState(session);
    renderSessionList();
  });
  document.querySelector('#mark-unresolved').addEventListener('click', () => {
    const session = currentSession();
    if (!session) return;
    session.resolved = false;
    renderInteractionState(session);
    renderSessionList();
  });
  document.querySelector('#no-order').addEventListener('click', () => {
    const session = currentSession();
    if (!session) return;
    session.workOrder = false;
    renderInteractionState(session);
    renderSessionList();
  });
  document.querySelector('#create-order').addEventListener('click', () => {
    const session = currentSession();
    if (!session) return;
    session.workOrder = true;
    renderInteractionState(session);
    renderSessionList();
  });
  document.querySelector('#complete-session').addEventListener('click', () => {
    const session = currentSession();
    if (!session) return;
    persistCurrentSessionFields();
    if (session.status === 'completed') {
      state.sessions.filter(item => item.status === 'active').forEach(item => item.status = 'pending');
      session.status = 'active';
      session.declined = false;
      session.startedAt = Date.now();
      session.completedAt = null;
      session.workspaceView = 'assist';
    } else {
      session.status = 'completed';
      session.completedAt = Date.now();
      session.workspaceView = 'assist';
    }
    renderSessionList();
    renderSelectedSession(session);
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
    const list = el('div', 'stacked-list category-card-list'); values.forEach(item => {
      const value = Number(item.value || 0); const share = item.share ?? Math.round(value / Math.max(total, 1) * 100);
      const row = el('div', 'stacked-row'); const head = el('div', 'stacked-head'); head.append(el('strong', '', item.label), el('span', 'category-share', `占比 ${share}%`)); row.append(head, el('span', 'category-call-count', `${value} 通记录`));
      // The card itself represents category prominence.  Keep the bar at full width so
      // its three colors read only as this category's resolution composition.
      const track = el('div', 'stacked-track'); const filled = el('div', 'stacked-total'); filled.style.width = '100%';
      [['resolved', 'resolved', '已解决'], ['unresolved', 'unresolved', '未直接解决'], ['unknown', 'unknown', '待判断']].forEach(([key, className, label]) => { const rate = item[`${key}_share`] ?? (value ? Number(item[key] || 0) / value * 100 : 0); const part = el('i', `stacked-segment ${className}`); part.style.width = `${rate}%`; part.title = `${label}：${rate}%`; filled.append(part); }); track.append(filled);
      const meta = el('div', 'stacked-meta'); [['legend-resolved', '已解决', item.resolved_share], ['legend-unresolved', '未直接解决', item.unresolved_share], ['legend-unknown', '待判断', item.unknown_share]].forEach(([className, label, rate]) => { const itemMeta = el('span'); itemMeta.append(el('i', className), document.createTextNode(`${label} ${rate ?? 0}%`)); meta.append(itemMeta); });
      row.append(track, meta);
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
  function renderRateBars(target, rows, rateKey, empty = '暂无可展示数据') {
    target.replaceChildren(); const values = (rows || []).filter(item => item[rateKey] !== null && item[rateKey] !== undefined);
    if (!values.length) { target.append(el('div', 'empty-chart', empty)); return; }
    const list = el('div', 'rate-bar-list'); values.forEach(item => { const rate = Math.max(0, Math.min(100, Number(item[rateKey] || 0))); const row = el('div', 'rate-bar-row'); const fill = el('i', 'rate-bar-fill'); fill.style.width = `${rate}%`; const track = el('div', 'rate-bar-track'); track.append(fill); row.append(el('strong', '', text(item.label)), track, el('span', '', `${rate}%`)); list.append(row); }); target.append(list);
  }
  function renderCallerResolutionComparison(target, rows, enterpriseRows = [], empty = '暂无已判定咨询主体记录') {
    target.replaceChildren(); const values = rows || [];
    if (!values.length || !values.some(item => item.resolved_rate !== null && item.resolved_rate !== undefined)) { target.append(el('div', 'empty-chart', empty)); return; }
    const board = el('div', 'resolution-comparison-board'); const primary = el('section', 'resolution-primary-section'); const primaryHead = el('div', 'resolution-section-head'); primaryHead.append(el('strong', '', '一级咨询主体'), el('span', '', '已直接解决率')); const primaryPlot = el('div', 'resolution-primary-plot'); const primaryGrid = el('div', 'resolution-primary-grid'); [100, 75, 50, 25, 0].forEach(rate => primaryGrid.append(el('span', '', `${rate}%`))); const primaryColumns = el('div', 'resolution-primary-columns'); values.forEach((item, index) => { const hasRate = item.resolved_rate !== null && item.resolved_rate !== undefined; const rate = hasRate ? Math.max(0, Math.min(100, Number(item.resolved_rate))) : 0; const column = el('article', `resolution-primary-column ${index === 0 ? 'personal' : 'enterprise'}${hasRate ? '' : ' unknown'}`); const value = el('span', 'resolution-primary-value', hasRate ? `${rate}%` : '—'); const track = el('div', 'resolution-primary-column-track'); const fill = el('i'); fill.style.height = `${rate}%`; track.append(fill); column.append(value, track, el('strong', '', text(item.label))); primaryColumns.append(column); }); primaryPlot.append(primaryGrid, primaryColumns); primary.append(primaryHead, primaryPlot);
    const identities = el('section', 'resolution-identity-section'); const identityHead = el('div', 'resolution-section-head'); identityHead.append(el('strong', '', '企业二级身份'), el('span', '', '仅统计已识别身份')); const identityList = el('div', 'resolution-identity-list'); (enterpriseRows || []).forEach(item => { const hasRate = item.resolved_rate !== null && item.resolved_rate !== undefined; const rate = hasRate ? Math.max(0, Math.min(100, Number(item.resolved_rate))) : 0; const row = el('div', `resolution-identity-row${hasRate ? '' : ' unknown'}`); const track = el('div', 'resolution-identity-track'); const fill = el('i'); fill.style.width = `${rate}%`; track.append(fill); row.append(el('strong', '', text(item.label)), track, el('span', '', hasRate ? `${rate}%` : '—')); identityList.append(row); }); if (!identityList.childElementCount) identityList.append(el('div', 'empty-chart compact', '暂无已识别且已判定的企业二级身份记录')); identities.append(identityHead, identityList); board.append(primary, identities); target.append(board);
  }
  function renderVerticalRateBars(target, rows, rateKey, empty = '暂无可展示数据', large = false) {
    target.replaceChildren(); const values = rows || [];
    if (!values.length) { target.append(el('div', 'empty-chart', empty)); return; }
    const ns = 'http://www.w3.org/2000/svg'; const svgNode = (tag, attrs = {}) => { const node = document.createElementNS(ns, tag); Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value)); return node; };
    const width = large ? 1200 : 900, height = large ? 390 : 300, left = 52, right = 22, top = 28, bottom = large ? 76 : 62, plotWidth = width - left - right, plotHeight = height - top - bottom, step = plotWidth / Math.max(values.length, 1), barWidth = Math.max(14, Math.min(large ? 62 : 46, step * .64));
    const shell = el('div', `vertical-rate-shell${large ? ' large' : ''}`); const svg = svgNode('svg', {class: `vertical-rate-chart${large ? ' large' : ''}`, viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': '解决率竖向柱状图'});
    for (let index = 0; index <= 4; index += 1) { const y = top + plotHeight - plotHeight * index / 4; svg.append(svgNode('line', {class: 'vertical-rate-grid', x1: left, x2: width - right, y1: y, y2: y})); const axis = svgNode('text', {class: 'vertical-rate-axis', x: left - 8, y: y + 3, 'text-anchor': 'end'}); axis.textContent = `${index * 25}%`; svg.append(axis); }
    values.forEach((item, index) => { const rate = item[rateKey]; const hasRate = rate !== null && rate !== undefined; const value = hasRate ? Math.max(0, Math.min(100, Number(rate))) : 0; const x = left + step * (index + .5), barHeight = value / 100 * plotHeight, y = top + plotHeight - barHeight; const bar = svgNode('rect', {class: 'vertical-rate-bar', x: x - barWidth / 2, y: hasRate ? y : top + plotHeight - 3, width: barWidth, height: hasRate ? Math.max(barHeight, 2) : 3, rx: 4}); if (!hasRate) bar.setAttribute('fill', '#d0d5dd'); const title = svgNode('title'); title.textContent = hasRate ? `${item.label}：解决率 ${value}%` : `${item.label}：暂无已判定记录`; bar.append(title); svg.append(bar); const valueLabel = svgNode('text', {class: 'vertical-rate-value', x, y: Math.max(top + 11, y - 8), 'text-anchor': 'middle'}); valueLabel.textContent = hasRate ? `${value}%` : '—'; const label = svgNode('text', {class: 'vertical-rate-label', x, y: height - (large ? 31 : 25), 'text-anchor': 'middle'}); const labelText = String(item.label || '未分类'); const labelLimit = large ? 9 : 7; label.textContent = labelText.length > labelLimit ? `${labelText.slice(0, labelLimit)}…` : labelText; svg.append(valueLabel, label); });
    shell.append(svg); target.append(shell);
  }
  function rateDistributionRow(item) {
    const rate = item.unresolved_rate; const hasRate = rate !== null && rate !== undefined; const value = hasRate ? Math.max(0, Math.min(100, Number(rate))) : 0; const labelText = text(item.label, '未形成明确分类'); const row = el('div', 'rate-bar-row distribution-rate-row'); const label = el('strong', '', labelText); label.title = labelText; label.setAttribute('aria-label', labelText); const fill = el('i', 'rate-bar-fill'); fill.style.width = `${value}%`; const track = el('div', 'rate-bar-track'); track.append(fill); row.append(label, track, el('span', '', hasRate ? `${value}%` : '—')); return row;
  }
  function renderUnresolvedRateDistribution(target, rows, drilldown = false) {
    target.replaceChildren(); const hasPositiveRate = item => Number(item?.unresolved_rate) > 0; const values = (rows || []).filter(hasPositiveRate); if (!values.length) { target.append(el('div', 'empty-chart', '暂无未直接解决分类')); return; }
    const list = el('div', 'rate-distribution-list'); values.forEach(item => { const itemWrap = el('section', 'rate-distribution-item'); itemWrap.append(rateDistributionRow(item)); const childrenRows = (item.children || []).filter(hasPositiveRate); if (drilldown && childrenRows.length) { const toggle = el('button', 'topic-drill-toggle', '展开二级专题'); toggle.type = 'button'; toggle.setAttribute('aria-expanded', 'false'); const children = el('div', 'rate-child-list hidden'); childrenRows.forEach(child => children.append(rateDistributionRow(child))); toggle.addEventListener('click', () => { const expanded = toggle.getAttribute('aria-expanded') === 'true'; toggle.setAttribute('aria-expanded', String(!expanded)); toggle.textContent = expanded ? '展开二级专题' : '收起二级专题'; children.classList.toggle('hidden', expanded); }); itemWrap.append(toggle, children); } list.append(itemWrap); }); target.append(list);
  }
  function renderHotspots(target, groups) {
    target.replaceChildren(); const labels = [['all', '全量未解决热点问题 Top5'], ['personal', '个人咨询未解决热点问题 Top5'], ['enterprise', '企业咨询未解决热点问题 Top5']];
    labels.forEach(([key, title]) => { const section = el('section', 'hotspot-section'); section.append(el('h3', '', title)); const list = el('ol', 'hotspot-list'); const rows = (groups?.[key] || []).filter(item => String(item.label || '').trim()); rows.length ? rows.forEach(item => list.append(el('li', '', String(item.label).trim()))) : list.append(el('li', 'empty', '暂无可展示的未直接解决问题')); section.append(list); target.append(section); });
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
    if (overview.resolved_rate != null) { const unresolved = (data.resolution_status || []).find(item => item.label === '未直接解决'); insights.push(`已判断记录的直接解决率为 ${overview.resolved_rate}%，其中 ${unresolved?.value || 0} 条未直接解决，应优先核验是否需要后续衔接。`); }
    const highestUnresolvedCategory = [...categories].sort((a, b) => Number(b.unresolved || 0) - Number(a.unresolved || 0))[0];
    if (highestUnresolvedCategory?.unresolved) insights.push(`“${highestUnresolvedCategory.label}”有 ${highestUnresolvedCategory.unresolved} 条未直接解决，是当前最值得优先复盘的专题。`);
    const activeFact = [...facts].sort((a, b) => Number(b.share || 0) - Number(a.share || 0))[0]; if (activeFact?.share) insights.push(`“${activeFact.label}”占总来电 ${activeFact.share}%，接听时应优先调取其历史节点，避免重复登记或重复解释。`);
    if (demands.length) insights.push(`高频需求为“${demands[0].label}”；可据此补齐标准答复、转办条件和一次性告知清单。`);
    if (!insights.length && overview.total_calls) insights.push(`当前共收录 ${overview.total_calls} 条来电记录，数据覆盖 ${text(overview.data_date_range, '当前有效日期')}。`);
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
      renderFacts(document.querySelector('#historical-facts'), data.historical_facts);
      renderCallerResolutionComparison(document.querySelector('#caller-resolution-chart'), data.caller_resolution_rates, data.enterprise_identity_resolution_rates);
      renderVerticalRateBars(document.querySelector('#unit-resolution-chart'), data.registration_unit_resolution, 'resolved_rate', '暂无已判定登记单位记录', true);
      renderHotspots(document.querySelector('#unresolved-hotspots'), data.unresolved_question_hotspots);
      const distributions = data.unresolved_distributions || {}; renderUnresolvedRateDistribution(document.querySelector('#category-chart'), distributions.topics || data.question_categories, true); renderUnresolvedRateDistribution(document.querySelector('#demand-chart'), distributions.demands || data.demand_categories);
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
        detailSection('重点分析信息', detail.extracted, [['核心问题', 'core_question', true], ['坐席答复提炼', 'agent_answer_summary', true], ['一级专题', 'topic_category'], ['二级专题', 'secondary_topic'], ['需求类别', 'demand_category'], ['解决情况', 'resolved', false, resolvedText], ['未解决原因', 'unresolved_reason', true], ['业务专业度', 'proficiency_level'], ['业务专业度依据', 'proficiency_basis', true], ['近期情绪状态', 'emotion_state'], ['近期情绪状态依据', 'emotion_basis', true], ['存在联系相关部门或人员且未解决', 'contact_unresolved', false, value => value ? '是' : '否'], ['服务不满', 'taxpayer_dissatisfied', false, value => value ? '是' : '否']]),
        detailSection('人工登记与原始信息', detail.original, [['业务内容', 'business_content', true], ['答复内容', 'answer_content', true], ['登记日期', 'registration_time'], ['通话开始', 'call_start_time'], ['通话结束', 'call_end_time'], ['坐席工号', 'agent_id'], ['坐席姓名', 'agent_name'], ['登记单位', 'registration_unit'], ['登记处理方式', 'handling_method'], ['业务类别', 'business_category'], ['满意度', 'satisfaction'], ['呼叫流水号', 'call_serial_number'], ['转写结果', 'transcript', true]])
      );
    } catch (error) { content.textContent = `详情读取失败：${error.message}`; }
  }
  function closeDetail() { document.querySelector('#detail-overlay').classList.add('hidden'); document.body.classList.remove('drawer-open'); }
  document.querySelector('#detail-close').addEventListener('click', closeDetail); document.querySelector('#detail-overlay').addEventListener('click', event => event.target.id === 'detail-overlay' && closeDetail());

  function showcasePanel(title, note = '', className = '') { const panel = el('article', `panel${className ? ` ${className}` : ''}`); const head = el('div', 'panel-head'); const titleWrap = el('div', 'panel-title'); titleWrap.append(el('h2', '', title)); head.append(titleWrap); if (note) head.append(el('span', 'panel-head-note', note)); const body = el('div', 'showcase-panel-body'); panel.append(head, body); return {panel, body}; }

  function renderGraph(data) {
    if (state.graphCleanup) state.graphCleanup();
    const root = document.querySelector('#profile-knowledge-content'); root.replaceChildren();
    const taxonomy = state.showcaseCatalog.taxonomy || {}; const dimensions = taxonomy.dimensions || []; const modeGroups = taxonomy.service_mode_groups || [];
    const panel = showcasePanel(data ? '历史证据推导链' : '多维画像关系图', data ? `${data.masked_phone} · 当前号码链路` : '整体逻辑 · 可旋转探索');
    const toolbar = el('div', 'knowledge-graph-toolbar'); const toolbarNote = el('p', '', '路径：确认来电人 → 查看历史依据 → 三维画像 → 具体接待方式。'); const actions = el('div', 'graph-toolbar-actions');
    const resetButton = el('button', 'graph-tool', '复位视角'); const rotationButton = el('button', 'graph-tool active', '暂停旋转'); const labelsButton = el('button', 'graph-tool active', '隐藏标签'); const overviewButton = el('button', 'graph-tool', data ? '显示全局' : '回到全局'); const nextButton = el('button', 'graph-tool', '逐类聚焦 →'); const replayButton = data ? el('button', 'graph-tool replay', '开始分步推导 →') : null; const fullReplayButton = data ? el('button', 'graph-tool', '完整推导 ▶') : null;
    const resultWrap = data ? el('div', 'result-trigger-wrap hidden') : null; const resultAnchor = data ? el('span', 'result-trigger-anchor') : null; const resultButton = data ? el('button', 'graph-tool graph-result-trigger', '生成接待建议') : null; const evidenceButton = data ? el('button', 'graph-tool', '复查推导依据') : null;
    [resetButton, rotationButton, labelsButton, overviewButton, nextButton, replayButton, fullReplayButton, resultButton, evidenceButton].filter(Boolean).forEach(button => button.type = 'button'); if (resultButton) { resultButton.setAttribute('aria-haspopup', 'dialog'); resultButton.setAttribute('aria-expanded', 'false'); resultAnchor.append(resultButton); resultWrap.append(resultAnchor, evidenceButton); }
    actions.append(...[resetButton, rotationButton, labelsButton, overviewButton, nextButton, replayButton, fullReplayButton, resultWrap].filter(Boolean)); toolbar.append(toolbarNote, actions);
    const status = data ? el('div', 'graph-derivation-status external') : null; let statusTitle = null, statusCopy = null, statusBar = null;
    if (status) { status.setAttribute('aria-live', 'polite'); statusTitle = el('strong', '', '分层推导说明'); statusCopy = el('span', '', '逐步确认号码、证据、画像与具体接待方式。'); const track = el('div', 'derivation-progress'); statusBar = el('i'); track.append(statusBar); status.append(statusTitle, statusCopy, track); }
    const stage = el('div', 'knowledge-graph-stage'); const canvas = el('canvas', 'knowledge-graph-canvas'); canvas.tabIndex = 0; canvas.setAttribute('role', 'img'); canvas.setAttribute('aria-label', '从历史来电证据到具体接待方式的三维推导图。');
    const hint = el('div', 'graph-gesture-hint', '拖拽旋转 · 滚轮缩放 · 点击节点聚焦'); const lineKey = data ? el('div', 'graph-line-key') : null; if (lineKey) lineKey.append(el('strong', '', '线条说明：'), document.createTextNode('呼吸亮线＝当前号码的有效推导路径；分步播放时仅当前步骤增强。'));
    const legend = el('div', 'graph-depth-legend'); [['proficiency', '业务专业度'], ['emotion', '近期情绪状态'], ['facts', '历史服务事实'], ['mode-expression', '表达方式'], ['mode-emotion', '情绪响应'], ['mode-continuity', '业务应对']].forEach(([className, label]) => { const item = el('span'); item.append(el('i', className), document.createTextNode(label)); legend.append(item); });
    stage.append(...[canvas, hint, lineKey, legend].filter(Boolean)); panel.body.append(...[toolbar, status, stage].filter(Boolean)); root.append(panel.panel, renderClassificationCatalog(data));

    const active = new Set(); (data?.before?.profile_model?.items || []).forEach(item => (item.values || []).forEach(value => active.add(`${item.id}:${value}`))); (data?.before?.result?.mode_components || []).forEach(item => active.add(`mode:${item.mode_id}`));
    const zones = {proficiency:{label:'业务专业度', y:-215,z:65}, emotion:{label:'近期情绪状态',y:0,z:85}, facts:{label:'历史服务事实',y:160,z:-105}};
    const modeZones = {information_delivery:{label:'表达方式',y:-210,z:65,group:'mode_information_delivery'}, emotion_response:{label:'情绪响应',y:0,z:85,group:'mode_emotion_response'}, matter_continuity:{label:'业务应对',y:160,z:-105,group:'mode_matter_continuity'}};
    const evidence = data?.derivation_evidence || {};
    const eventText = event => { const time = text(event?.call_time, '').slice(5, 16).replace('T', ' '); return [time, text(event?.label, ''), text(event?.question, '')].filter(Boolean).join(' · '); };
    const sourceText = source => { const time = text(source?.call_time, '').slice(5, 16).replace('T', ' '); return time ? `${time} · ` : ''; };
    const evidenceDetails = {
      proficiency: `${sourceText(evidence?.proficiency?.source)}${text(evidence?.proficiency?.basis, '根据近期业务表达与办理节点认知判断。')}`,
      emotion: `${sourceText(evidence?.emotion?.source)}${text(evidence?.emotion?.basis, '根据近期可观察到的表达判断。')}`,
      facts: (evidence?.facts?.events || []).slice(0, 2).map(eventText).join('；') || '最近五个工作日的明确服务事实。'
    };
    const categoryPositions = {
      proficiency: {专业:{x:92,y:-58,z:34},了解:{x:38,y:0,z:92},小白:{x:92,y:58,z:28},暂无法判断:{x:-10,y:0,z:-72}},
      emotion: {平稳:{x:90,y:-83,z:34},焦虑:{x:36,y:-25,z:88},不满:{x:90,y:33,z:26},暂无法判断:{x:-12,y:-25,z:-76}},
      facts: {对坐席不满:{x:64,y:-51,z:50},历史工单:{x:108,y:-3,z:6},'存在联系相关部门或人员且未解决':{x:86,y:91,z:-52},异常中断:{x:48,y:127,z:-82},等待推诿:{x:-12,y:62,z:-36},近五个工作日未命中:{x:4,y:16,z:88}}
    };
    const modePositions = {
      direct:{x:340,y:-260,z:90},explain:{x:340,y:-210,z:55},guide:{x:340,y:-160,z:20},
      steady:{x:340,y:-66,z:105},stabilize:{x:340,y:0,z:70},repair:{x:340,y:66,z:35},
      followup:{x:340,y:145,z:-78},clarify:{x:340,y:215,z:-118}
    };
    const nodes = []; const edges = []; const modeSpaces = []; const push = node => { nodes.push(node); return node.id; }; const link = (source, target, step, lane = '') => edges.push({source, target, step, lane});
    const callerId = push({id:'caller', label:data ? text(evidence?.caller?.masked_phone, data.masked_phone) : '待确认号码', detail:data ? '已从历史来电库确认' : '选择号码后确认来电人', kind:'database', group:'caller', x:-575,y:0,z:0,r:20,current:Boolean(data)});
    dimensions.forEach((dimension, index) => {
      const zone = zones[dimension.id] || {label:dimension.name,y:(index - 1) * 185,z:0}; const evidenceId = push({id:`evidence:${dimension.id}`,label:`${zone.label}依据`,detail:evidenceDetails[dimension.id] || '可用证据不足。',kind:'evidence',group:dimension.id,x:-385,y:zone.y,z:zone.z,r:15,current:Boolean(data)}); const dimensionId = push({id:`dim:${dimension.id}`,label:zone.label,kind:'dimension',group:dimension.id,x:-155,y:zone.y,z:zone.z,r:18}); link(callerId,evidenceId,'evidence',dimension.id); link(evidenceId,dimensionId,'profile',dimension.id);
      const categories = [...(dimension.categories || [])]; if (dimension.unknown) categories.push(dimension.unknown); categories.forEach((category, categoryIndex) => { const position = categoryPositions[dimension.id]?.[category] || {x:50,y:(categoryIndex - (categories.length - 1) / 2) * 30,z:0}; const id = push({id:`${dimension.id}:${category}`,label:category,kind:'category',group:dimension.id,x:position.x,y:zone.y + position.y,z:zone.z + position.z,r:12,current:active.has(`${dimension.id}:${category}`)}); link(dimensionId,id,'profile',dimension.id); });
    });
    const directTargets = {
      'proficiency:专业':['mode:direct'], 'proficiency:了解':['mode:explain'], 'proficiency:小白':['mode:guide'], 'proficiency:暂无法判断':['mode:guide'],
      'emotion:不满':['mode:repair'], 'emotion:焦虑':['mode:stabilize'], 'emotion:平稳':['mode:steady'], 'emotion:暂无法判断':['mode:steady'], 'facts:对坐席不满':['mode:repair'],
      'facts:历史工单':['mode:followup'], 'facts:异常中断':['mode:followup'], 'facts:存在联系相关部门或人员且未解决':['mode:followup'], 'facts:等待推诿':['mode:repair'], 'facts:近五个工作日未命中':['mode:clarify']
    };
    modeGroups.forEach((modeGroup, groupIndex) => {
      const zone = modeZones[modeGroup.id] || {label:modeGroup.label,y:(groupIndex - 1) * 185,z:0,group:`mode_${modeGroup.id}`}; const modeIds = [];
      (modeGroup.modes || []).forEach((mode, modeIndex) => { const id = `mode:${mode.id}`; const position = modePositions[mode.id] || {x:325,y:zone.y + (modeIndex - ((modeGroup.modes || []).length - 1) / 2) * 42,z:zone.z}; modeIds.push(id); push({id,label:mode.label,kind:'mode',group:zone.group,x:position.x,y:position.y,z:position.z,r:13,current:active.has(id)}); });
      modeSpaces.push({label:zone.label, group:zone.group, modeIds, x:325,y:zone.y,z:zone.z});
    });
    Object.entries(directTargets).forEach(([source, targets]) => targets.forEach(target => { if (nodes.some(node => node.id === source) && nodes.some(node => node.id === target)) link(source,target,'mode',source.split(':')[0]); }));
    const edgeKey = edge => `${edge.source}→${edge.target}`; const activeCategories = new Set(nodes.filter(node => node.kind === 'category' && node.current).map(node => node.id)); const activeModes = new Set(nodes.filter(node => node.kind === 'mode' && node.current).map(node => node.id)); const activeModeEdges = new Set(edges.filter(edge => edge.step === 'mode' && activeCategories.has(edge.source) && activeModes.has(edge.target)).map(edgeKey)); const activeProfileNodes = new Set([...nodes.filter(node => node.kind === 'category' && node.current).map(node => node.id), ...nodes.filter(node => node.kind === 'dimension').map(node => node.id), ...nodes.filter(node => node.kind === 'evidence').map(node => node.id), callerId]); const sceneCenterX = (Math.min(...nodes.map(node => node.x)) + Math.max(...nodes.map(node => node.x))) / 2;
    const palette = {caller:['#b9e5ff','#27759c'], proficiency:['#c8d5ff','#4968d3'], emotion:['#ffc5cf','#c94f69'], facts:['#a4efdf','#218a7c'], mode_information_delivery:['#bde9ff','#347fa8'], mode_emotion_response:['#efccff','#8755b7'], mode_matter_continuity:['#ffdaa9','#bd7428']};
    let rotX=-.22, rotY=-.48, zoom=1.05, drag=false, px=0, py=0, dragDistance=0, alive=true, frame=0, lastFrame=0, autoRotate=!window.matchMedia('(prefers-reduced-motion: reduce)').matches, showLabels=true, showGlobal=!data, focusGroup=null, focusNodeId=null, focusIndex=-1, focusedCache=null, lastProjected=new Map(), demoActive=false, demoStep=0, demoStart=performance.now(), demoPhase=-1, resultOverlay=null, evidenceOverlay=null, fullReplayTimer=0, fullReplayActive=false, modalTimer=0, evidenceTimer=0, resultHintTimer=0;
    const context = canvas.getContext('2d');
    function syncRotationButton(){rotationButton.classList.toggle('active',autoRotate);rotationButton.textContent=autoRotate?'暂停旋转':'继续旋转';rotationButton.setAttribute('aria-pressed',String(autoRotate));}
    function syncLabelsButton(){labelsButton.classList.toggle('active',showLabels);labelsButton.textContent=showLabels?'隐藏标签':'显示标签';labelsButton.setAttribute('aria-pressed',String(showLabels));}
    function syncOverviewButton(){const overviewActive=data?showGlobal:!focusGroup&&!focusNodeId;overviewButton.classList.toggle('active',overviewActive);overviewButton.textContent=data?(showGlobal?'仅看当前号码':'显示全局'):'回到全局';overviewButton.disabled=!data&&overviewActive;overviewButton.setAttribute('aria-pressed',String(overviewActive));}
    function syncReplayButton(){if(!replayButton)return;const labels={0:'开始分步推导 →',1:'下一步：业务专业度依据 →',2:'继续形成业务专业度标签 →',3:'下一步：近期情绪依据 →',4:'继续形成近期情绪标签 →',5:'下一步：历史服务事实 →',6:'继续形成历史服务事实标签 →',7:'下一步：匹配接待方式 →',8:'重新开始推导'};replayButton.textContent=labels[demoStep]||'继续推导 →';replayButton.classList.toggle('active',demoActive);}
    syncRotationButton();syncLabelsButton();syncOverviewButton();syncReplayButton();
    function resize() { const rect = canvas.getBoundingClientRect(); const ratio = Math.min(devicePixelRatio || 1, 1.5); canvas.width = rect.width * ratio; canvas.height = rect.height * ratio; context.setTransform(ratio,0,0,ratio,0,0); }
    function project(node, transform) { const x=node.x-sceneCenterX,y=node.y,z=node.z; const x1=x*transform.cy-z*transform.sy,z1=x*transform.sy+z*transform.cy,y1=y*transform.cx-z1*transform.sx,z2=y*transform.sx+z1*transform.cx,perspective=760/(900+z2); return {x:transform.centerX+x1*perspective*zoom,y:transform.centerY+y1*perspective*zoom,scale:perspective*zoom,depth:z2}; }
    const clamp = value => Math.max(0,Math.min(1,value));
    function progress(elapsed) { const reveal=clamp(elapsed/700); const laneProgress = (evidenceStep, profileStep) => ({evidence:demoStep>evidenceStep?1:demoStep===evidenceStep?reveal:0,profile:demoStep>profileStep?1:demoStep===profileStep?reveal:0}); return {caller:demoStep>1?1:demoStep===1?reveal:0, proficiency:laneProgress(2,3), emotion:laneProgress(4,5), facts:laneProgress(6,7), mode:demoStep===8?reveal:0,total:Math.min(1,(Math.max(demoStep-1,0)+reveal)/8)}; }
    function updateStatus(elapsed=0) { if (!status || !statusTitle || !statusCopy || !statusBar) return; const phase=demoStep; const steps={1:['第 1 步：确认来电人',`历史来电库中已定位 ${text(evidence?.caller?.masked_phone, data?.masked_phone || '当前号码')}。`],2:['第 2 步：查看业务专业度依据','先展示本次判断所依据的历史来电记录与业务表达。'],3:['第 3 步：形成业务专业度标签','依据确认后，仅点亮当前号码命中的业务专业度标签。'],4:['第 4 步：查看近期情绪依据','先展示近期情绪状态的判断依据。'],5:['第 5 步：形成近期情绪标签','依据确认后，仅点亮当前号码命中的近期情绪标签。'],6:['第 6 步：查看历史服务事实','展示最近五个工作日中事实发生的来电与时间。'],7:['第 7 步：形成历史服务事实标签','依据确认后，仅点亮当前号码实际命中的服务事实。'],8:['第 8 步：匹配具体接待方式','连线直接落到命中的具体接待方式；三类方式仅作为分区标题。']}; const [title,copy]=steps[phase] || ['分层推导说明','逐步确认号码、证据、画像与具体接待方式。']; if (phase!==demoPhase) { demoPhase=phase; statusTitle.textContent=title; statusCopy.textContent=copy; } statusBar.style.width=`${progress(elapsed).total*100}%`; }
    function clearEvidenceOverlay() { window.clearTimeout(evidenceTimer); evidenceOverlay?.remove(); evidenceOverlay=null; }
    function clearResultHint() { window.clearTimeout(resultHintTimer); resultAnchor?.querySelector('.result-trigger-hint')?.remove(); }
    function showResultHint() { if (!resultAnchor || state.showcaseResultHintShown) return; clearResultHint(); const hint=el('span','result-trigger-hint','接待建议已收起，可点击这里再次查看'); hint.setAttribute('role','status'); resultAnchor.append(hint); state.showcaseResultHintShown=true; try { window.sessionStorage.setItem('showcase-result-hint-seen','1'); } catch {} resultHintTimer=window.setTimeout(()=>hint.remove(),4200); }
    function clearOverlay() { resultOverlay?.remove(); resultOverlay=null; clearEvidenceOverlay(); document.body.classList.remove('result-card-open'); resultButton?.setAttribute('aria-expanded','false'); }
    function closeResultCard() { if (!resultOverlay || !resultButton || !resultWrap) { clearOverlay(); return; } const overlay=resultOverlay,card=overlay.querySelector('.derivation-result-card'); if (!card) { clearOverlay(); return; } resultWrap.classList.remove('hidden'); resultButton.classList.add('available'); const finish=()=>{ if(resultOverlay===overlay) resultOverlay=null; overlay.remove(); document.body.classList.remove('result-card-open'); resultButton.setAttribute('aria-expanded','false'); resultButton.focus({preventScroll:true}); showResultHint(); }; overlay.classList.add('closing'); if (window.matchMedia('(prefers-reduced-motion: reduce)').matches || typeof card.animate!=='function') { finish(); return; } const target=resultButton.getBoundingClientRect(),source=card.getBoundingClientRect(),dx=target.left+target.width/2-(source.left+source.width/2),dy=target.top+target.height/2-(source.top+source.height/2); card.animate([{opacity:1,transform:'translate(0, 0) scale(1)'},{opacity:.12,transform:`translate(${dx}px, ${dy}px) scale(.08)`}],{duration:440,easing:'cubic-bezier(.4,0,.2,1)',fill:'forwards'}).finished.then(finish,finish); }
    function openResultCard() { if (!data || !resultButton || !resultWrap) return; clearResultHint(); clearOverlay(); resultWrap.classList.remove('hidden'); resultButton.classList.remove('available'); const byInput=Object.fromEntries((data.before?.profile_model?.items || []).map(item=>[item.id,item])); const byOutput=Object.fromEntries((data.before?.result?.mode_components || []).map(item=>[item.category_id,item])); const routes=[['proficiency','业务专业度','information_delivery','表达方式','#6685f2'],['emotion','近期情绪状态','emotion_response','情绪响应','#a76bd7'],['facts','历史服务事实','matter_continuity','业务应对','#38aa94']]; const overlay=el('div','derivation-result-overlay'); overlay.setAttribute('role','dialog'); overlay.setAttribute('aria-modal','true'); const card=el('article','derivation-result-card'); const head=el('header','derivation-result-head'); const copy=el('div'); copy.append(el('span','',`${text(data.masked_phone,'当前号码')} · 推导完成`),el('h2','','本次坐席接待建议')); const close=el('button','derivation-result-close','×'); close.type='button'; close.setAttribute('aria-label','关闭接待建议'); head.append(copy,close); const strategy=el('section','derivation-result-strategy'); strategy.append(el('small','','建议采用的组合接待策略'),el('strong','',text(data.before?.result?.service_mode,'结果待确认'))); const routeGrid=el('div','derivation-result-routes'); routes.forEach(([input,inputLabel,output,outputLabel,color])=>{const item=el('article','derivation-result-route'); item.style.setProperty('--route-color',color); const source=el('div','derivation-route-source');source.append(el('small','',inputLabel),el('strong','',text(byInput[input]?.value,'暂无法判断')));const target=el('div','derivation-route-target');target.append(el('small','',outputLabel),el('strong','',text(byOutput[output]?.mode,'待匹配')));item.append(source,el('span','derivation-route-arrow','→'),target);routeGrid.append(item);}); const advice=el('section','derivation-result-advice'); advice.append(el('small','','坐席接待重点'),el('p','',text(data.before?.result?.service_suggestion,'请先确认本次实际诉求，再结合历史信息调整接待方式。'))); card.append(head,strategy,routeGrid,advice,el('p','derivation-result-boundary','以上结果用于辅助接待；坐席仍需确认本次实际诉求、最新进展与政策口径。')); overlay.append(card); document.body.append(overlay); document.body.classList.add('result-card-open'); resultButton.setAttribute('aria-expanded','true'); resultOverlay=overlay; requestAnimationFrame(()=>overlay.classList.add('open')); close.addEventListener('click',closeResultCard); close.focus({preventScroll:true}); }
    function openAdviceConfirmation() { if (!data || resultOverlay) return; resultWrap?.classList.remove('hidden'); const overlay=el('div','derivation-confirm-overlay'); overlay.setAttribute('role','dialog'); overlay.setAttribute('aria-modal','true'); const card=el('section','derivation-confirm-card'); card.append(el('span','derivation-confirm-eyebrow','推导已完成'),el('h2','','是否生成接待建议？'),el('p','','已完成从历史来电依据到具体接待方式的匹配。生成后将展示供坐席直接使用的组合建议。')); const buttons=el('div','derivation-confirm-actions'); const cancel=el('button','graph-tool','暂不生成'); const confirm=el('button','graph-tool graph-result-trigger','生成接待建议'); cancel.type='button';confirm.type='button';buttons.append(cancel,confirm);card.append(buttons);overlay.append(card);document.body.append(overlay);resultOverlay=overlay;requestAnimationFrame(()=>overlay.classList.add('open'));cancel.addEventListener('click',()=>{clearOverlay();resultButton?.classList.add('available');});confirm.addEventListener('click',openResultCard);confirm.focus({preventScroll:true}); }
    function evidenceContent(lane) { const model = (data?.before?.profile_model?.items || []).find(item => item.id === lane); const source = evidence?.[lane]?.source; const factEvents = evidence?.facts?.events || []; const colors = {proficiency:'#526bcc',emotion:'#bb5470',facts:'#238574'}; const title = `${zones[lane]?.label || ''}依据`; const basis = lane === 'facts' ? '最近五个工作日内的明确服务事实，会与对应来电时间一同展示。' : text(evidence?.[lane]?.basis, '可用证据不足。'); const sourceRows = lane === 'facts' ? factEvents.slice(0, 4).map(event => ({label:eventText(event),businessId:event.business_id})) : [{label:[text(source?.call_time, '').slice(0, 16).replace('T', ' '), text(source?.question, '已登记来电')].filter(Boolean).join(' · '),businessId:source?.business_id}]; return {title,basis,sourceRows:sourceRows.filter(row=>row.label),value:text(model?.value,'暂无法判断'),color:colors[lane]}; }
    function openEvidenceDialog(lane, nextStep, reviewOnly=false) { if (!data) { applyDemoStep(nextStep); return; } clearEvidenceOverlay(); const content=evidenceContent(lane); const overlay=el('div','derivation-evidence-overlay'); overlay.setAttribute('role','dialog'); overlay.setAttribute('aria-modal','true'); const card=el('section','derivation-evidence-card'); card.style.setProperty('--evidence-color',content.color); const close=el('button','derivation-modal-close','×');close.type='button';close.setAttribute('aria-label',reviewOnly?'返回判断依据选择':'确认依据并继续推导');card.append(close,el('small','',`${content.title} · 当前判断：${content.value}`),el('h2','',content.title),el('p','',content.basis)); content.sourceRows.forEach(row=>{const source=el('div','derivation-evidence-source');source.append(el('strong','',lane==='facts'?'历史事实发生来电':'判断所依据的历史来电'),el('span','',row.label));if(row.businessId){const detail=el('button','derivation-evidence-link','查看该通来电记录 ↗');detail.type='button';detail.addEventListener('click',()=>{if(fullReplayActive)stopFullReplay();window.clearTimeout(evidenceTimer);toolbarNote.textContent='已暂停自动推导，可在关闭来电详情后继续核对依据。';openDetail(row.businessId);});source.append(detail);}card.append(source);});overlay.append(card);document.body.append(overlay);evidenceOverlay=overlay;requestAnimationFrame(()=>overlay.classList.add('open'));const continueFlow=()=>{if(evidenceOverlay!==overlay)return;clearEvidenceOverlay();if(reviewOnly){toolbarNote.textContent='已返回判断依据选择，可继续复查其他依据。';openEvidenceReview();return;}applyDemoStep(nextStep);if(fullReplayActive)scheduleAutoStep(nextStep);};close.addEventListener('click',continueFlow);close.focus({preventScroll:true});if(fullReplayActive&&!reviewOnly)evidenceTimer=window.setTimeout(continueFlow,1700); }
    function openEvidenceReview() { if (!data) return; clearResultHint(); clearOverlay(); const overlay=el('div','derivation-confirm-overlay'); overlay.setAttribute('role','dialog'); overlay.setAttribute('aria-modal','true'); const card=el('section','derivation-confirm-card derivation-review-card'); const close=el('button','derivation-modal-close','×');close.type='button';close.setAttribute('aria-label','关闭判断依据复查');card.append(close,el('span','derivation-confirm-eyebrow','推导依据复查'),el('h2','','选择要复查的判断依据'),el('p','','可随时回看各项判断来源，并直接打开对应的历史来电记录。')); const options=el('div','derivation-evidence-review-list'); [['proficiency','业务专业度依据'],['emotion','近期情绪依据'],['facts','历史服务事实']].forEach(([lane,label])=>{const button=el('button','graph-tool',label);button.type='button';button.addEventListener('click',()=>{clearOverlay();openEvidenceDialog(lane,0,true);});options.append(button);}); const closeReview=()=>{clearOverlay();toolbarNote.textContent='依据窗口已关闭；可继续查看推导结果。';evidenceButton?.focus({preventScroll:true});};close.addEventListener('click',closeReview); card.append(options);overlay.append(card);document.body.append(overlay);resultOverlay=overlay;requestAnimationFrame(()=>overlay.classList.add('open'));close.focus({preventScroll:true}); }
    function focusedIds() { if (focusNodeId) { const ids=new Set([focusNodeId]); edges.forEach(edge=>{if(edge.source===focusNodeId||edge.target===focusNodeId){ids.add(edge.source);ids.add(edge.target);}}); return ids; } if (!focusGroup) return new Set(nodes.map(node=>node.id)); const ids=new Set(nodes.filter(node=>node.group===focusGroup).map(node=>node.id)); for(let pass=0;pass<3;pass+=1) edges.forEach(edge=>{if(ids.has(edge.source)||ids.has(edge.target)){ids.add(edge.source);ids.add(edge.target);}}); return ids; }
    function nodeReveal(node,p) { if (!demoActive) return 1; if(node.kind==='database')return p.caller; if(node.kind==='evidence')return p[node.group]?.evidence || 0; if(node.kind==='dimension'||node.kind==='category')return p[node.group]?.profile || 0; if(node.kind==='mode')return p.mode; return .04; }
    function edgeReveal(edge,p) { if (edge.step === 'evidence') return p[edge.lane]?.evidence || 0; if (edge.step === 'profile') return p[edge.lane]?.profile || 0; if (edge.step === 'mode') return p.mode; return 0; }
    function drawDatabase(point,node,light,dark,reveal) { const w=Math.max(78,86*point.scale),h=Math.max(42,48*point.scale); context.globalAlpha=Math.max(.04,reveal); context.fillStyle=dark;context.strokeStyle=light;context.lineWidth=1.5; context.beginPath();context.ellipse(point.x,point.y-h/2,w/2,h/5,0,0,Math.PI*2);context.fill();context.stroke();context.fillRect(point.x-w/2,point.y-h/2,w,h);context.strokeRect(point.x-w/2,point.y-h/2,w,h);context.beginPath();context.ellipse(point.x,point.y+h/2,w/2,h/5,0,0,Math.PI);context.stroke();if(reveal>.45&&showLabels){context.fillStyle='#eefbff';context.font=`800 ${Math.max(10,13*point.scale)}px system-ui`;context.textAlign='center';context.fillText(node.label,point.x,point.y+3);context.fillStyle='#c1dae8';context.font=`${Math.max(8,9*point.scale)}px system-ui`;context.fillText('已确认来电人',point.x,point.y+17);context.textAlign='start';}point.hitRadius=Math.max(30,w/2); }
    function drawEvidence(point,node,light,reveal) { const w=Math.max(132,178*point.scale),h=Math.max(45,52*point.scale);context.globalAlpha=Math.max(.04,reveal);context.fillStyle='rgba(8,21,39,.9)';context.strokeStyle=light;context.lineWidth=node.current?1.8:1;context.beginPath();if(typeof context.roundRect==='function')context.roundRect(point.x-w/2,point.y-h/2,w,h,7);else context.rect(point.x-w/2,point.y-h/2,w,h);context.fill();context.stroke();if(showLabels){context.fillStyle='#e8f1ff';context.font=`700 ${Math.max(9,12*point.scale)}px system-ui`;context.textAlign='center';context.fillText(node.label,point.x,point.y-6);context.fillStyle='#b6c8dd';context.font=`${Math.max(8,10*point.scale)}px system-ui`;const detail=node.detail.length>24?`${node.detail.slice(0,24)}…`:node.detail;context.fillText(detail,point.x,point.y+11);context.textAlign='start';}point.hitRadius=w/2; }
    function drawGraphLabel(node, point, alpha=1) { const fontSize=node.kind==='dimension'?14:12,chunk=node.label.length>12?10:15,lines=node.label.match(new RegExp(`.{1,${chunk}}`,'g'))||[node.label],lineHeight=fontSize+4;context.font=`${node.current?750:650} ${fontSize}px system-ui`;const width=Math.max(...lines.map(line=>context.measureText(line).width)),height=lines.length*lineHeight,preferLeft=node.group==='facts'||(node.kind==='mode'&&point.x>canvas.clientWidth*.67),radius=point.hitRadius||8;let x=preferLeft?point.x-radius-12:point.x+radius+12;x=preferLeft?Math.max(width+8,x):Math.min(canvas.clientWidth-width-8,x);const top=Math.max(8,Math.min(canvas.clientHeight-height-8,point.y-height/2));context.globalAlpha=.42*alpha;context.strokeStyle=node.current?'#d7fff7':'#a9bfd8';context.lineWidth=1;context.beginPath();context.moveTo(point.x+(preferLeft?-radius:radius),point.y);context.lineTo(x+(preferLeft?4:-4),point.y);context.stroke();context.globalAlpha=alpha;context.fillStyle=node.current?'#f7fffd':'#edf4ff';context.textAlign=preferLeft?'right':'left';context.shadowColor='rgba(2,8,20,.96)';context.shadowBlur=alpha>.5?6:2;lines.forEach((line,index)=>context.fillText(line,x,top+lineHeight*(index+1)-3));context.shadowBlur=0;context.textAlign='start'; }
    function drawEdgeBreath(a,b,phase,alpha=.8) { const breath=(Math.sin(phase)+1)/2;context.globalAlpha=alpha*(.12+.12*breath);context.strokeStyle='#63d8c5';context.lineWidth=4+breath*3;context.lineCap='round';context.beginPath();context.moveTo(a.x,a.y);context.lineTo(b.x,b.y);context.stroke();context.globalAlpha=alpha*(.34+.34*breath);context.strokeStyle='#b6fff0';context.lineWidth=1.1+breath*1.35;context.beginPath();context.moveTo(a.x,a.y);context.lineTo(b.x,b.y);context.stroke();context.lineCap='butt';context.globalAlpha=1; }
    function draw(timestamp=0) { if(!alive)return; if(document.hidden||timestamp-lastFrame<32){frame=requestAnimationFrame(draw);return;}lastFrame=timestamp;if(autoRotate&&!drag)rotY+=.003;context.clearRect(0,0,canvas.clientWidth,canvas.clientHeight);const transform={cy:Math.cos(rotY),sy:Math.sin(rotY),cx:Math.cos(rotX),sx:Math.sin(rotX),centerX:canvas.clientWidth/2-Math.min(42,canvas.clientWidth*.035),centerY:canvas.clientHeight*.46};const projected=new Map(nodes.map(node=>[node.id,project(node,transform)]));const p=progress(Math.max(0,timestamp-demoStart)),focused=focusedCache||(focusedCache=focusedIds()),hasFocus=Boolean(demoActive||focusGroup||focusNodeId);if(demoActive)updateStatus(Math.max(0,timestamp-demoStart));
      modeSpaces.forEach(space=>{const points=space.modeIds.map(id=>projected.get(id)).filter(Boolean);if(!points.length)return;const minX=Math.min(...points.map(point=>point.x))-24,maxX=Math.max(...points.map(point=>point.x))+24,minY=Math.min(...points.map(point=>point.y))-31,maxY=Math.max(...points.map(point=>point.y))+24,[light]=palette[space.group]||['#8fa7cf'];context.globalAlpha=!demoActive||p.mode>.04?.11:.025;context.fillStyle=light;context.strokeStyle=light;context.lineWidth=1;context.beginPath();if(typeof context.roundRect==='function')context.roundRect(minX,minY,maxX-minX,maxY-minY,12);else context.rect(minX,minY,maxX-minX,maxY-minY);context.fill();context.globalAlpha=!demoActive||p.mode>.35?.85:.08;context.font='800 11px system-ui';context.fillStyle='#f1f6ff';context.fillText(space.label,minX+10,minY+16);context.globalAlpha=1;});
      edges.forEach((edge,index)=>{const a=projected.get(edge.source),b=projected.get(edge.target);if(!a||!b)return;const key=edgeKey(edge),isCurrent=edge.step==='mode'?activeModeEdges.has(key):edge.step==='profile'?activeProfileNodes.has(edge.source)&&activeProfileNodes.has(edge.target):Boolean(data),isContextEdge=Boolean(data&&!showGlobal&&!isCurrent);const reveal=edgeReveal(edge,p);context.globalAlpha=isContextEdge?(demoActive?.022:.055):demoActive?(reveal?(isCurrent ? .2 : .018):.018):(hasFocus?(focused.has(edge.source)&&focused.has(edge.target)&&(isCurrent||!data||showGlobal) ? .7 : .04):.55);context.strokeStyle='rgba(119,158,190,.52)';context.lineWidth=isContextEdge?.7:1;context.beginPath();context.moveTo(a.x,a.y);context.lineTo(b.x,b.y);context.stroke();if(demoActive&&isCurrent&&reveal>0){const ex=a.x+(b.x-a.x)*reveal,ey=a.y+(b.y-a.y)*reveal;context.globalAlpha=.76;context.strokeStyle='#86e5d3';context.lineWidth=1.8;context.beginPath();context.moveTo(a.x,a.y);context.lineTo(ex,ey);context.stroke();if(reveal<.995)drawEdgeBreath(a,{x:ex,y:ey},timestamp*.008+index*.7,.96);}else if(!demoActive&&(isCurrent||!data)){const stepPhase={evidence:0,profile:1.8,mode:3.6}[edge.step]||0;drawEdgeBreath(a,b,timestamp*.0027+stepPhase+index*.14,hasFocus ? .84 : .66);}});
      lastProjected=projected;[...nodes].sort((a,b)=>projected.get(b.id).depth-projected.get(a.id).depth).forEach(node=>{const point=projected.get(node.id),reveal=nodeReveal(node,p),[light,dark]=palette[node.group]||['#c8d5ff','#4968d3'],laneProgress=p[node.group]||{},isAlternative=Boolean(data&&(node.kind==='category'||node.kind==='mode')&&!node.current&&!showGlobal),highlighted=node.current||(demoActive&&((node.kind==='database'&&p.caller>.7)||(node.kind==='evidence'&&laneProgress.evidence>.7)||(node.kind==='category'&&node.current&&laneProgress.profile>.7)||(node.kind==='mode'&&node.current&&p.mode>.7)));if(node.kind==='database'){drawDatabase(point,node,light,dark,reveal);}else if(node.kind==='evidence'){drawEvidence(point,node,light,reveal);}else {const radius=Math.max(5,node.r*point.scale*(highlighted?1+Math.sin(timestamp*.004)*.06:1));point.hitRadius=radius;const nodeAlpha=isAlternative?(demoActive?.075:.14):demoActive?Math.max(.04,reveal):(hasFocus&&!focused.has(node.id)?.16:1);const gradient=context.createRadialGradient(point.x-radius*.35,point.y-radius*.35,1,point.x,point.y,radius);gradient.addColorStop(0,light);gradient.addColorStop(1,dark);context.fillStyle=gradient;context.shadowBlur=highlighted?18:0;context.shadowColor=light;if(highlighted&&demoActive&&reveal>0){const ascent=Math.max(4,radius+10);context.globalAlpha=Math.min(.8,reveal);context.strokeStyle=light;context.lineWidth=2;context.beginPath();context.moveTo(point.x,point.y+ascent);context.lineTo(point.x,point.y+ascent-(ascent*reveal));context.stroke();}context.globalAlpha=nodeAlpha;context.beginPath();context.arc(point.x,point.y,radius,0,Math.PI*2);context.fill();context.shadowBlur=0;if(highlighted){context.strokeStyle=light;context.lineWidth=2;context.beginPath();context.arc(point.x,point.y,radius+4,0,Math.PI*2);context.stroke();}}const show=showLabels&&(demoActive?isAlternative||reveal>.55:true);if(show&&node.kind!=='evidence'&&node.kind!=='database'){context.globalAlpha=1;drawGraphLabel(node,point,isAlternative?(demoActive?.18:.32):1);}context.globalAlpha=1;});frame=requestAnimationFrame(draw); }
    function stopFullReplay(){window.clearTimeout(fullReplayTimer);window.clearTimeout(evidenceTimer);fullReplayActive=false;fullReplayButton?.classList.remove('active');if(fullReplayButton)fullReplayButton.textContent='完整推导 ▶';}
    function applyDemoStep(step){demoStep=Math.max(1,Math.min(8,step));demoActive=true;showGlobal=false;demoStart=performance.now();demoPhase=-1;focusGroup=null;focusNodeId=null;focusIndex=-1;focusedCache=null;syncOverviewButton();syncReplayButton();updateStatus(0);toolbarNote.textContent=`正在查看推导第 ${demoStep} 步；图像视角保持不变，可继续拖拽观察。`;window.clearTimeout(modalTimer);if(demoStep<8){clearOverlay();resultWrap?.classList.add('hidden');}if([2,4,6].includes(demoStep)){const lane={2:'proficiency',4:'emotion',6:'facts'}[demoStep];modalTimer=window.setTimeout(()=>openEvidenceDialog(lane,demoStep+1),760);}if(demoStep===8){resultWrap?.classList.remove('hidden');modalTimer=window.setTimeout(openAdviceConfirmation,760);if(fullReplayActive)scheduleAutoStep(8);}}
    function resetDemo(){stopFullReplay();demoActive=false;demoStep=0;demoPhase=-1;window.clearTimeout(modalTimer);clearResultHint();clearOverlay();resultWrap?.classList.add('hidden');syncReplayButton();syncOverviewButton();updateStatus(0);}
    function advance(){stopFullReplay();if(evidenceOverlay)return;if(demoStep>=8){resetDemo();toolbarNote.textContent='可点击“开始分步推导”逐层讲解，或点击“完整推导”连续播放。';return;}applyDemoStep(demoStep+1);}
    function scheduleAutoStep(step){if(!fullReplayActive)return;const nextByStep={1:2,3:4,5:6,7:8};const next=nextByStep[step];if(!next){if(step===8)fullReplayTimer=window.setTimeout(()=>{fullReplayActive=false;fullReplayButton?.classList.remove('active');if(fullReplayButton)fullReplayButton.textContent='重新完整推导 ▶';},1550);return;}const interval=window.matchMedia('(prefers-reduced-motion: reduce)').matches?80:1450;fullReplayTimer=window.setTimeout(()=>{if(fullReplayActive)applyDemoStep(next);},interval);}
    function playAll(){if(fullReplayActive){stopFullReplay();return;}resetDemo();fullReplayActive=true;fullReplayButton?.classList.add('active');if(fullReplayButton)fullReplayButton.textContent='停止完整推导';applyDemoStep(1);scheduleAutoStep(1);}
    const nodeCanReceivePointer=node=>!(data&&!showGlobal&&(node.kind==='category'||node.kind==='mode')&&!node.current);
    canvas.addEventListener('pointerdown',event=>{drag=true;dragDistance=0;px=event.clientX;py=event.clientY;canvas.setPointerCapture(event.pointerId);});
    canvas.addEventListener('pointermove',event=>{if(!drag)return;const dx=event.clientX-px,dy=event.clientY-py;dragDistance+=Math.hypot(dx,dy);rotY+=dx*.006;rotX=Math.max(-1,Math.min(1,rotX+dy*.004));px=event.clientX;py=event.clientY;});
    canvas.addEventListener('pointerup',event=>{drag=false;if(canvas.hasPointerCapture(event.pointerId))canvas.releasePointerCapture(event.pointerId);if(dragDistance>=5)return;const rect=canvas.getBoundingClientRect(),x=event.clientX-rect.left,y=event.clientY-rect.top,clicked=[...nodes].reverse().find(node=>{if(!nodeCanReceivePointer(node))return false;const point=lastProjected.get(node.id);return point&&Math.hypot(point.x-x,point.y-y)<=(point.hitRadius||7)+7;});if(!clicked)return;focusNodeId=focusNodeId===clicked.id?null:clicked.id;focusGroup=null;focusIndex=-1;focusedCache=null;syncOverviewButton();toolbarNote.textContent=focusNodeId?`当前聚焦：${clicked.label}，相关依据与接待方式已高亮。`:data&&!showGlobal?'当前仅展示该号码的实际推导链。':'当前展示完整推导关系。';});
    canvas.addEventListener('pointercancel',()=>{drag=false;dragDistance=0;});
    canvas.addEventListener('wheel',event=>{event.preventDefault();zoom=Math.max(.6,Math.min(1.7,zoom*(event.deltaY>0?.92:1.08)));},{passive:false});
    resetButton.addEventListener('click',()=>{rotX=-.22;rotY=-.48;zoom=1.05;toolbarNote.textContent='已复位视角；当前推导、接待建议和依据复查状态均已保留。';});
    rotationButton.addEventListener('click',()=>{autoRotate=!autoRotate;syncRotationButton();});
    labelsButton.addEventListener('click',()=>{showLabels=!showLabels;syncLabelsButton();});
    overviewButton.addEventListener('click',()=>{focusGroup=null;focusNodeId=null;focusIndex=-1;focusedCache=null;zoom=1.05;if(data)showGlobal=!showGlobal;syncOverviewButton();toolbarNote.textContent=data?(showGlobal?'已显示完整分类关系；当前号码命中路径仍以呼吸亮线突出。':'已返回当前号码视图，仅保留实际命中的推导链。'):'已返回完整关系视图。';});
    nextButton.addEventListener('click',()=>{const groups=[...dimensions.map(item=>item.id),...modeGroups.map(item=>modeZones[item.id]?.group||`mode_${item.id}`)];if(!groups.length)return;if(data)showGlobal=true;focusIndex=(focusIndex+1)%groups.length;focusGroup=groups[focusIndex];focusNodeId=null;focusedCache=null;zoom=1.14;syncOverviewButton();const label=dimensions.find(item=>item.id===focusGroup)?.name||modeGroups.find(item=>(modeZones[item.id]?.group||`mode_${item.id}`)===focusGroup)?.label||focusGroup;toolbarNote.textContent=`当前聚焦：${label}，点击“${data?'仅看当前号码':'回到全局'}”退出聚焦。`;});
    replayButton?.addEventListener('click',advance);fullReplayButton?.addEventListener('click',playAll);resultButton?.addEventListener('click',openResultCard);evidenceButton?.addEventListener('click',openEvidenceReview);
    const keyHandler=event=>{if(event.key!=='Escape')return;const detailOverlay=document.querySelector('#detail-overlay');if(detailOverlay&&!detailOverlay.classList.contains('hidden')){event.preventDefault();closeDetail();return;}if(evidenceOverlay){event.preventDefault();evidenceOverlay.querySelector('.derivation-modal-close')?.click();return;}if(!resultOverlay)return;event.preventDefault();if(resultOverlay.classList.contains('derivation-result-overlay')){closeResultCard();return;}resultOverlay.querySelector('.derivation-modal-close')?.click();if(resultOverlay){clearOverlay();if(demoStep===8){resultWrap?.classList.remove('hidden');resultButton?.classList.add('available');resultButton?.focus({preventScroll:true});}}};
    document.addEventListener('keydown',keyHandler);const observer=new ResizeObserver(resize);observer.observe(canvas);requestAnimationFrame(()=>{resize();draw();});state.graphCleanup=()=>{alive=false;cancelAnimationFrame(frame);stopFullReplay();window.clearTimeout(modalTimer);clearResultHint();document.removeEventListener('keydown',keyHandler);clearOverlay();observer.disconnect();};
  }

  function renderClassificationCatalog(data) {
    const taxonomy = state.showcaseCatalog.taxonomy || {}; const summary = state.showcaseCatalog.summary || {}; const panel = showcasePanel('完整分类与判定规则', data ? '当前画像与三个分项模式已同步突出' : `三维特征、${summary.fact_count || 0} 项公开事实、三类 ${summary.mode_count || 0} 项接待方式`);
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
      if (state.knowledgeViewMode === 'instance' && select.value && (changed || query.trim())) await loadGraphInstance();
    } catch (error) { if (request === state.knowledgeSearchRequest) meta.textContent = `检索失败：${error.message}`; }
  }

  async function loadShowcaseCatalog() {
    try {
      const catalog = await api('/api/showcase/catalog?limit=5'); state.showcaseCatalog = catalog; const knowledgeSelect = document.querySelector('#knowledge-profile-select'); replaceProfileOptions(knowledgeSelect, catalog.items || []); document.querySelector('#knowledge-index-meta').textContent = profileSearchMeta(catalog); renderGraph(null);
    } catch (error) { document.querySelector('#knowledge-mode-status').textContent = `画像目录读取失败：${error.message}`; }
  }
  let knowledgeSearchTimer = 0; document.querySelector('#knowledge-profile-search').addEventListener('input', event => { clearTimeout(knowledgeSearchTimer); knowledgeSearchTimer = setTimeout(() => searchProfiles(event.target.value), 260); });
  document.querySelectorAll('[data-knowledge-mode]').forEach(button => button.addEventListener('click', async () => {
    const instance = button.dataset.knowledgeMode === 'instance';
    state.knowledgeViewMode = instance ? 'instance' : 'overall';
    document.querySelectorAll('[data-knowledge-mode]').forEach(node => node.classList.toggle('active', node === button));
    document.querySelector('#knowledge-profile-wrap').classList.toggle('hidden', !instance);
    if (!instance) {
      state.knowledgeGraphRequest += 1;
      document.querySelector('#knowledge-mode-status').textContent = '当前展示整体画像方法论，不关联具体号码。';
      renderGraph(null);
    } else {
      await loadGraphInstance();
    }
  }));
  async function loadGraphInstance() {
    const select = document.querySelector('#knowledge-profile-select'); const key = select.value;
    if (!key || state.knowledgeViewMode !== 'instance') {
      if (state.knowledgeViewMode === 'instance') document.querySelector('#knowledge-mode-status').textContent = '暂无可展示的号码画像。';
      return;
    }
    const request = ++state.knowledgeGraphRequest;
    document.querySelector('#knowledge-mode-status').textContent = '正在读取号码画像…';
    try {
      const data = await api('/api/showcase', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({profile_key:key})});
      if (request !== state.knowledgeGraphRequest || state.knowledgeViewMode !== 'instance' || select.value !== key) return;
      document.querySelector('#knowledge-mode-status').textContent = `${data.masked_phone} · 当前仅展示该号码的历史证据与推导链。`;
      renderGraph(data);
    } catch(error) {
      if (request === state.knowledgeGraphRequest && state.knowledgeViewMode === 'instance') document.querySelector('#knowledge-mode-status').textContent = `读取失败：${error.message}`;
    }
  }
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
