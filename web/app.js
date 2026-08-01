(() => {
  'use strict';

  const {ApiError, requestJson} = window.TaxpayerAPI;
  const {el, tableMessageRow, text} = window.TaxpayerUI;
  const pages = new Set(['workbench', 'dashboard', 'history', 'showcase', 'users']);
  const titles = {workbench: '12366坐席接待助手', dashboard: '热线数据概览', history: '历史来电记录', showcase: '画像推演中心', users: '用户与权限'};
  const state = {
    user: null,
    phone: '',
    dashboardLoaded: false,
    history: {page: 1, totalPages: 0, phone: '', loaded: false},
    showcaseCatalog: null,
    knowledgeSearchRequest: 0,
    knowledgeGraphRequest: 0,
    knowledgeViewMode: 'overall',
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
    try {
      return await requestJson(url, options);
    } catch (error) {
      if (
        error instanceof ApiError
        && error.status === 401
        && url !== '/api/auth/login'
      ) showLogin();
      throw error;
    }
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
    if (page !== 'workbench') document.querySelector('#caller-history-overlay').classList.add('hidden');
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
    if (page === 'showcase') {
      if (!state.showcaseCatalog) loadShowcaseCatalog();
      else if (!state.graphCleanup) {
        if (state.knowledgeViewMode === 'overall') renderGraph(null);
        else loadGraphInstance();
      }
    }
    if (page === 'users') loadUsers();
    if (page !== 'showcase' && state.graphCleanup) { state.graphCleanup(); state.graphCleanup = null; }
    window.scrollTo({top: 0, behavior: 'smooth'});
  }
  document.querySelectorAll('.nav-link').forEach(node => node.addEventListener('click', () => showPage(node.dataset.page)));
  window.addEventListener('hashchange', () => state.user && showPage(location.hash.slice(1), false));

  document.querySelector('#lookup-form').addEventListener('submit', async event => {
    event.preventDefault();
    const phone = document.querySelector('#phone').value.trim();
    const notice = document.querySelector('#notice');
    if (!phone) { notice.textContent = '请输入来电号码。'; notice.className = 'notice error'; return; }
    const button = document.querySelector('#submit'); button.disabled = true; notice.textContent = '正在调取历史画像并生成接待建议…'; notice.className = 'notice';
    try {
      const result = await api('/api/profile', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({phone})});
      state.phone = phone;
      if (result.found) window.TaxpayerWorkbench.renderProfile(result.profile, openDetail);
      else window.TaxpayerWorkbench.renderMissingProfile();
      const advice = await api('/api/advice', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({phone})});
      window.TaxpayerWorkbench.renderAdvice(advice.advice); notice.textContent = result.found ? '历史信息已调取，接待建议已实时生成。' : '未匹配到历史信息，已生成保守接待建议。';
    } catch (error) { notice.textContent = `读取失败：${error.message}`; notice.className = 'notice error'; }
    finally { button.disabled = false; }
  });

  document.querySelector('#view-current-history').addEventListener('click', () => {
    document.querySelector('#caller-history-overlay').classList.remove('hidden');
  });
  function closeCallerHistory() { document.querySelector('#caller-history-overlay').classList.add('hidden'); }
  document.querySelector('#caller-history-close').addEventListener('click', closeCallerHistory);
  document.querySelector('#caller-history-overlay').addEventListener('click', event => event.target.id === 'caller-history-overlay' && closeCallerHistory());

  async function loadDashboard(force = false) {
    if (state.dashboardLoaded && !force) return;
    const metrics = document.querySelector('#dashboard-metrics'); metrics.replaceChildren(el('div', 'stat-card', '正在汇总数据…'));
    try {
      const data = await api('/api/dashboard');
      window.TaxpayerDashboard.render(data);
      state.dashboardLoaded = true;
    } catch (error) { metrics.replaceChildren(el('div', 'stat-card', `读取失败：${error.message}`)); }
  }
  document.querySelector('#refresh-dashboard').addEventListener('click', () => loadDashboard(true));

  async function loadHistory(page = 1) {
    const body = document.querySelector('#history-body');
    body.replaceChildren(tableMessageRow('正在读取来电记录…', 4));
    try {
      const data = await api('/api/history', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({page, page_size: 10, phone: state.history.phone || null})});
      state.history.page = data.page; state.history.totalPages = data.total_pages; state.history.loaded = true; body.replaceChildren();
      window.TaxpayerHistory.renderPage(data, openDetail);
    } catch (error) {
      window.TaxpayerHistory.renderPageError(error.message);
    }
  }
  document.querySelector('#history-filter').addEventListener('submit', event => { event.preventDefault(); state.history.phone = document.querySelector('#history-phone').value.trim(); loadHistory(1); });
  document.querySelector('#history-clear').addEventListener('click', () => { document.querySelector('#history-phone').value = ''; state.history.phone = ''; loadHistory(1); });
  document.querySelector('#history-prev').addEventListener('click', () => state.history.page > 1 && loadHistory(state.history.page - 1));
  document.querySelector('#history-next').addEventListener('click', () => state.history.page < state.history.totalPages && loadHistory(state.history.page + 1));

  async function openDetail(businessId) {
    const overlay = document.querySelector('#detail-overlay'); const content = document.querySelector('#detail-content'); overlay.classList.remove('hidden'); document.body.classList.add('drawer-open'); content.replaceChildren(el('div', 'loading', '正在读取详情…'));
    try {
      const result = await api('/api/history/detail', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({business_id: businessId})}); if (!result.found) throw new Error('记录不存在'); const detail = result.detail;
      window.TaxpayerHistory.renderDetail(detail);
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
    stage.append(...[canvas, hint, lineKey, legend].filter(Boolean)); panel.body.append(...[toolbar, status, stage].filter(Boolean)); const catalogSummary=state.showcaseCatalog.summary || {}; const catalogPanel=showcasePanel('完整分类与判定规则', data ? '当前画像与三个分项模式已同步突出' : `三维特征、${catalogSummary.fact_count || 0} 项公开事实、三类 ${catalogSummary.mode_count || 0} 项接待方式`); catalogPanel.body.append(window.TaxpayerShowcase.renderClassificationCatalog({data, catalog:state.showcaseCatalog, modeClass})); root.append(panel.panel, catalogPanel.panel);

    state.graphCleanup = window.TaxpayerShowcaseGraph.createGraph({canvas, data, taxonomy, toolbarNote, resetButton, rotationButton, labelsButton, overviewButton, nextButton, replayButton, fullReplayButton, resultWrap, resultAnchor, resultButton, evidenceButton, status, statusTitle, statusCopy, statusBar, onOpenDetail: openDetail, onCloseDetail: closeDetail});
  }

  async function searchProfiles(query) {
    const request = ++state.knowledgeSearchRequest;
    const select = document.querySelector('#knowledge-profile-select'); const meta = document.querySelector('#knowledge-index-meta');
    meta.textContent = '正在检索号码画像…';
    try {
      const catalog = await api(`/api/showcase/catalog?limit=5&q=${encodeURIComponent(query.trim())}`); if (request !== state.knowledgeSearchRequest) return;
      state.showcaseCatalog = {...state.showcaseCatalog, ...catalog, items:catalog.items}; const changed = window.TaxpayerShowcase.replaceProfileOptions(select, catalog.items || []); meta.textContent = window.TaxpayerShowcase.profileSearchMeta(catalog, query);
      if (state.knowledgeViewMode === 'instance' && select.value && (changed || query.trim())) await loadGraphInstance();
    } catch (error) { if (request === state.knowledgeSearchRequest) meta.textContent = `检索失败：${error.message}`; }
  }

  async function loadShowcaseCatalog() {
    try {
      const catalog = await api('/api/showcase/catalog?limit=5'); state.showcaseCatalog = catalog; const knowledgeSelect = document.querySelector('#knowledge-profile-select'); window.TaxpayerShowcase.replaceProfileOptions(knowledgeSelect, catalog.items || []); document.querySelector('#knowledge-index-meta').textContent = window.TaxpayerShowcase.profileSearchMeta(catalog); renderGraph(null);
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
    window.TaxpayerUserManagement.renderLoading();
    try {
      const data = await api('/api/users');
      window.TaxpayerUserManagement.renderUsers(data.items, updateUser);
    } catch (error) {
      window.TaxpayerUserManagement.renderError(error.message);
    }
  }
  async function updateUser(userId, fields) { try { await api('/api/users/update', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({user_id:userId, ...fields})}); loadUsers(); } catch(error) { alert(error.message); loadUsers(); } }
  document.querySelector('#user-form').addEventListener('submit', async event => { event.preventDefault(); const notice = document.querySelector('#user-notice'); try { await api('/api/users/create', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({username:document.querySelector('#new-username').value.trim(), display_name:document.querySelector('#new-display-name').value.trim(), password:document.querySelector('#new-password').value, role:document.querySelector('#new-role').value})}); event.target.reset(); notice.textContent = '用户创建成功。'; notice.className = 'notice'; loadUsers(); } catch(error) { notice.textContent = error.message; notice.className = 'notice error'; } });
  document.querySelector('#refresh-users').addEventListener('click', loadUsers);

  restoreSession();
})();
