(() => {
  'use strict';

  const text = (value, fallback = '暂无记录') => (
    value === null || value === undefined || value === ''
      ? fallback
      : String(value)
  );

  const dateText = value => (
    value ? String(value).replace('T', ' ').slice(0, 16) : '时间未记录'
  );

  const el = (tag, className = '', content) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (content !== undefined) node.textContent = content;
    return node;
  };

  // API errors must enter table placeholders as text, never through an HTML sink.
  const tableMessageRow = (message, columnCount) => {
    const row = el('tr');
    const cell = el('td', 'table-empty', message);
    cell.colSpan = columnCount;
    row.append(cell);
    return row;
  };

  const selectOption = (value, label) => {
    const option = el('option', '', label);
    option.value = value;
    return option;
  };

  window.TaxpayerUI = Object.freeze({
    dateText,
    el,
    selectOption,
    tableMessageRow,
    text,
  });
})();
