(() => {
  'use strict';

  const {dateText, el, selectOption, tableMessageRow} = window.TaxpayerUI;

  function renderLoading() { document.querySelector('#user-body').replaceChildren(tableMessageRow('正在读取用户…', 6)); }
  function renderError(message) { document.querySelector('#user-body').replaceChildren(tableMessageRow(`读取失败：${message}`, 6)); }
  function renderUsers(users, onUpdate) {
    const body = document.querySelector('#user-body'); body.replaceChildren();
    users.forEach(user => { const row = el('tr'); const role = el('select'); role.append(selectOption('agent', '坐席'), selectOption('admin', '管理员')); role.value = user.role; role.addEventListener('change', () => onUpdate(user.id, {role: role.value})); const status = el('span', `user-status ${user.is_active ? 'active' : 'disabled'}`, user.is_active ? '已启用' : '已停用'); const toggle = el('button', 'button secondary compact', user.is_active ? '停用' : '启用'); toggle.addEventListener('click', () => onUpdate(user.id, {is_active: !user.is_active})); const reset = el('button', 'button ghost compact', '重置密码'); reset.addEventListener('click', () => { const password = prompt('请输入至少8位的新密码'); if (password) onUpdate(user.id, {password}); }); const roleCell = el('td'); roleCell.append(role); const statusCell = el('td'); statusCell.append(status); const actions = el('div'); actions.append(toggle, reset); const actionCell = el('td'); actionCell.append(actions); row.append(el('td', '', user.username), el('td', '', user.display_name), roleCell, statusCell, el('td', '', dateText(user.created_at)), actionCell); body.append(row); });
  }

  window.TaxpayerUserManagement = Object.freeze({renderError, renderLoading, renderUsers});
})();
