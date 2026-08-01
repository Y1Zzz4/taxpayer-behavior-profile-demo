(() => {
  'use strict';

  const {el} = window.TaxpayerUI;

  function renderClassificationCatalog({data, catalog = {}, modeClass}) {
    const taxonomy = catalog.taxonomy || {};
    const content = document.createDocumentFragment();
    const activeLabels = new Set();
    ((data?.before?.profile_model || {}).items || []).forEach(item =>
      (item.values || []).forEach(value => activeLabels.add(`${item.id}:${value}`)),
    );
    const activeModeIds = new Set(
      (data?.before?.result?.mode_components || []).map(item => item.mode_id),
    );

    const dimensions = el('section', 'taxonomy-section');
    const dimensionHead = el('div', 'taxonomy-section-head');
    dimensionHead.append(
      el('strong', 'taxonomy-major-heading', '纳税人画像字段'),
      el('span', '', data ? '蓝色标签为当前画像' : '用于识别当前服务需求'),
    );
    const dimensionGrid = el('div', 'dimension-catalog');
    (taxonomy.dimensions || []).forEach(dimension => {
      const hasCurrent = data && (data.before.profile_model?.items || []).some(item => item.id === dimension.id);
      const card = el('article', `dimension-card${hasCurrent ? ' active' : ''}`);
      const head = el('div', 'dimension-card-head');
      head.append(el('strong', '', dimension.name), el('span', '', `${dimension.categories.length} 类`));
      const tags = el('div', 'taxonomy-tags');
      [...dimension.categories, dimension.unknown].forEach(category =>
        tags.append(el('span', activeLabels.has(`${dimension.id}:${category}`) ? 'active' : '', category)),
      );
      card.append(head, el('p', '', dimension.description), tags);
      dimensionGrid.append(card);
    });
    dimensions.append(dimensionHead, dimensionGrid);

    const modes = el('section', 'taxonomy-section');
    const modeHead = el('div', 'taxonomy-section-head');
    modeHead.append(
      el('strong', 'taxonomy-major-heading', '坐席接待方式'),
      el('span', '', data ? '每类彩色边框项为当前结果' : '每个类别选择一项，三个结果同时生效'),
    );
    const groupGrid = el('div', 'service-mode-groups');
    (taxonomy.service_mode_groups || []).forEach(group => {
      const groupCard = el('section', `service-mode-group ${modeClass({category_id: group.id})}`);
      const head = el('div', 'service-mode-group-head');
      head.append(el('strong', '', group.label), el('span', '', `${(group.modes || []).length} 种接待方式`));
      const grid = el('div', 'service-mode-catalog');
      (group.modes || []).forEach(mode => {
        const card = el('article', `service-mode-card${activeModeIds.has(mode.id) ? ' active' : ''}`);
        card.append(
          el('strong', '', mode.label),
          el('p', '', mode.focus),
          el('div', 'composite-meta', `判定规则：${mode.rule}`),
          el('div', 'composite-meta', `沟通建议：${mode.communication}`),
        );
        grid.append(card);
      });
      groupCard.append(head, el('p', 'service-mode-group-description', group.description), grid);
      groupGrid.append(groupCard);
    });
    modes.append(modeHead, groupGrid);
    content.append(dimensions, modes);
    return content;
  }

  function profileOption(item, index) {
    const rank = Number(item.index || index + 1);
    const option = el('option', '', `${String(rank).padStart(2, '0')} · ${item.masked_phone}`);
    option.value = item.profile_key;
    return option;
  }

  function replaceProfileOptions(select, items) {
    const previous = select.value;
    select.replaceChildren();
    items.forEach((item, index) => select.append(profileOption(item, index)));
    select.disabled = !items.length;
    if (items.some(item => item.profile_key === previous)) select.value = previous;
    return previous !== select.value && Boolean(select.value);
  }

  function profileSearchMeta(catalog, query = '') {
    const count = Number(catalog.summary?.profile_count || 0);
    const shown = (catalog.items || []).length;
    if (!shown) return '未找到匹配的号码画像';
    return query.trim()
      ? `找到 ${shown} 个匹配结果；最多展示5个`
      : `默认展示最近 ${shown} 个；其余 ${Math.max(0, count - shown)} 个可通过号码或序号搜索`;
  }

  window.TaxpayerShowcase = Object.freeze({
    profileSearchMeta,
    renderClassificationCatalog,
    replaceProfileOptions,
  });
})();
