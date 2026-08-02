(() => {
  'use strict';

  const {el, text} = window.TaxpayerUI;

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
  function resolutionRateItem(item, level) {
    const label = text(item?.label, '未识别主体');
    const hasRate = item?.resolved_rate !== null && item?.resolved_rate !== undefined;
    const rate = hasRate
      ? Math.max(0, Math.min(100, Number(item.resolved_rate)))
      : 0;
    const itemNode = el('article', `resolution-rate-item ${level}${hasRate ? '' : ' unknown'}`);
    itemNode.setAttribute('role', 'listitem');
    const head = el('div', 'resolution-rate-head');
    head.append(
      el('strong', 'resolution-subject-name', label),
      el('strong', 'resolution-rate-value', hasRate ? `${rate}%` : '—'),
    );
    const track = el('div', 'resolution-rate-track');
    track.setAttribute(
      'aria-label',
      hasRate ? `${label}已直接解决率 ${rate}%` : `${label}暂无已判定记录`,
    );
    if (hasRate) {
      track.setAttribute('role', 'meter');
      track.setAttribute('aria-valuemin', '0');
      track.setAttribute('aria-valuemax', '100');
      track.setAttribute('aria-valuenow', String(rate));
    } else {
      track.setAttribute('role', 'img');
    }
    const fill = el('i', 'resolution-rate-fill');
    fill.style.width = `${rate}%`;
    track.append(fill);
    itemNode.append(head, track);
    return itemNode;
  }

  function renderCallerResolutionComparison(target, rows, enterpriseRows = [], empty = '暂无已判定咨询主体记录') {
    target.replaceChildren();
    const values = Array.isArray(rows) ? rows : [];
    const identityValues = Array.isArray(enterpriseRows) ? enterpriseRows : [];
    if (!values.some(item => item?.resolved_rate !== null && item?.resolved_rate !== undefined)) {
      target.append(el('div', 'empty-chart', empty));
      return;
    }

    const board = el('div', 'resolution-comparison-board');
    const primary = el('section', 'resolution-tier resolution-primary-section');
    const primaryHead = el('div', 'resolution-section-head');
    primaryHead.append(
      el('h3', '', '一级咨询主体'),
      el('span', '', '已直接解决率'),
    );
    const primaryGrid = el('div', 'resolution-rate-grid resolution-primary-grid');
    primaryGrid.setAttribute('role', 'list');
    primaryGrid.setAttribute('aria-label', '一级咨询主体解决率');
    values.forEach(item => primaryGrid.append(resolutionRateItem(item, 'primary')));
    primary.append(primaryHead, primaryGrid);

    const identities = el('section', 'resolution-tier resolution-identity-section');
    const identityHead = el('div', 'resolution-section-head');
    identityHead.append(
      el('h3', '', '企业二级身份'),
      el('span', '', '仅展示已识别身份'),
    );
    const identityGrid = el('div', 'resolution-rate-grid resolution-identity-grid');
    identityGrid.setAttribute('role', 'list');
    identityGrid.setAttribute('aria-label', '企业二级身份解决率');
    identityValues.forEach(item => identityGrid.append(resolutionRateItem(item, 'identity')));
    if (!identityValues.length) {
      identityGrid.append(el('div', 'resolution-identity-empty', '暂无已识别的企业二级身份记录'));
    }
    identities.append(identityHead, identityGrid);

    board.append(primary, identities);
    target.append(board);
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

  function render(data) {
    const overview = data.overview || {};
    const context = document.querySelector('#dashboard-context');
    context.replaceChildren(el('strong', '', overview.data_date_range ? `数据范围：${overview.data_date_range}` : '当前暂无有效来电日期'), el('span', 'dashboard-context-note', '统计结果随数据库增量更新'));
    const metrics = document.querySelector('#dashboard-metrics');
    metrics.replaceChildren();
    statCard(metrics, '累计来电', overview.total_calls, '当前收录的来电记录', colors[0], 'phone');
    statCard(metrics, '直接解决率', overview.resolved_rate == null ? '—' : `${overview.resolved_rate}%`, '按已判断记录计算', colors[1], 'check');
    renderTrend(document.querySelector('#daily-chart'), data.daily_calls);
    renderDonut(document.querySelector('#caller-chart'), data.caller_types, ['#536bd3', '#f0a04b', '#8a64c7'], '来电记录');
    renderFacts(document.querySelector('#historical-facts'), data.historical_facts);
    renderCallerResolutionComparison(document.querySelector('#caller-resolution-chart'), data.caller_resolution_rates, data.enterprise_identity_resolution_rates);
    renderVerticalRateBars(document.querySelector('#unit-resolution-chart'), data.registration_unit_resolution, 'resolved_rate', '暂无已判定登记单位记录', true);
    renderHotspots(document.querySelector('#unresolved-hotspots'), data.unresolved_question_hotspots);
    const distributions = data.unresolved_distributions || {};
    renderUnresolvedRateDistribution(document.querySelector('#category-chart'), distributions.topics || data.question_categories, true);
    renderUnresolvedRateDistribution(document.querySelector('#demand-chart'), distributions.demands || data.demand_categories);
  }

  window.TaxpayerDashboard = Object.freeze({render});
})();
