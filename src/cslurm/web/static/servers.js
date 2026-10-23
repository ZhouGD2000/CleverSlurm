"use strict";

const serversState = {data: null, busy: false, loading: false, install: null};
const serverFields = ['name', 'alias', 'python', 'root', 'install-dir'];
const serverStatuses = {
  disconnected: '未连接', connected: '已连接', not_installed: '所选 Python 中未安装',
  incompatible: '环境或版本不兼容', error: '连接失败', installing: '正在安装',
  installed: '已安装，等待连接', install_failed: '安装失败，已保留文件',
};
function serverError(message) {
  $('servers-error').textContent = message;
  show($('servers-error'), !!message);
}
function serverProfile(id) { return serversState.data?.items.find((item) => item.id === id); }
function serverName(id = state.workspace) {
  return id === 'local' ? '本机（cweb 所在主机）' : serverProfile(id)?.name || '远端服务器';
}
function renderServerList() {
  const data = serversState.data;
  if (!data) return;
  const select = $('workspace-select');
  select.replaceChildren(new Option('本机（cweb 所在主机）', 'local'));
  $('servers-list').replaceChildren();
  for (const item of data.items) {
    const status = item.connection.status;
    const option = new Option(item.name + (status === 'connected' ? '' : ' · ' + serverStatuses[status]), item.id);
    option.disabled = status !== 'connected';
    select.append(option);
    const row = el('div', 'server-row');
    const text = el('div', 'server-row-info');
    text.append(el('strong', '', item.name), el('code', '', item.alias),
      el('span', 'server-status', serverStatuses[status] || status));
    if (status === 'installing') text.append(el('small', '', item.connection.phase === 'building' ? '正在构建本地安装包' : '正在传输并离线安装'));
    text.append(el('small', 'mono', item.python), el('small', 'mono', item.root));
    if (item.connection.version) text.append(el('small', '', 'CleverSlurm ' + item.connection.version));
    if (item.connection.error) text.append(el('p', 'server-failure', item.connection.error));
    const tools = el('div', 'server-tools');
    const connect = iconButton('refresh-cw', status === 'connected' ? `重新连接 ${item.name}` : `连接 ${item.name}`, () => serverAction({action: 'connect', id: item.id}));
    const open = iconButton('arrow-right', `查看 ${item.name}`, () => { if (switchWorkspace(item.id)) $('servers-dialog').close(); });
    const edit = iconButton('settings', `编辑 ${item.name}`, () => editServer(item));
    const disconnect = iconButton('x', `断开 ${item.name}`, () => serverAction({action: 'disconnect', id: item.id}));
    const remove = iconButton('inbox', `移除连接 ${item.name}`, () => {
      if (confirm(`从本机列表移除 ${item.name}？远端安装、配置和数据均会保留。`))
        serverAction({action: 'remove', id: item.id, revision: data.revision});
    });
    for (const button of [connect, open, edit, disconnect, remove]) button.disabled = serversState.busy || status === 'installing';
    open.disabled ||= status !== 'connected';
    disconnect.disabled ||= status !== 'connected';
    edit.disabled ||= data.management_read_only;
    remove.disabled ||= data.management_read_only;
    tools.append(connect, open, edit, disconnect, remove);
    if (status === 'not_installed') {
      const install = el('button', 'subtle', '安装');
      install.prepend(icon('download'));
      install.disabled = data.management_read_only || serversState.busy;
      install.addEventListener('click', () => {
        serversState.install = item.id;
        $('server-install-target').textContent = `${item.alias} · ${item.install_dir} · 数据：${item.root}`;
        $('server-install-confirm').value = '';
        show($('server-install-panel'));
        $('server-install-confirm').focus();
      });
      tools.append(install);
    }
    row.append(text, tools);
    $('servers-list').append(row);
  }
  if (!data.items.length) $('servers-list').append(el('p', 'small-empty', '尚未添加 SSH 服务器'));
  select.value = state.workspace;
  $('server-fields').disabled = data.management_read_only || serversState.busy;
  $('server-save').disabled = data.management_read_only || serversState.busy;
  $('server-new').disabled = data.management_read_only || serversState.busy;
  $('server-install').disabled = data.management_read_only || serversState.busy;
  $('workspace-target').textContent = serverName();
}
async function loadServers() {
  if (serversState.loading || !state.token) return;
  serversState.loading = true;
  try {
    serversState.data = await api('/api/servers');
    renderServerList();
  } catch (e) { serverError(e.message); }
  finally { serversState.loading = false; }
}
function ensureServers() { if (!serversState.data && !serversState.loading) loadServers(); }
function editServer(item = {}) {
  $('server-id').value = item.id || '';
  $('server-form-title').textContent = item.id ? `编辑 ${item.name}` : '添加服务器';
  const defaults = {name: '', alias: '', python: 'python3', root: '~/.cslurm', install_dir: '~/.local/share/cleverslurm'};
  for (const key of serverFields) $('server-' + key).value = item[key.replaceAll('-', '_')] ?? defaults[key.replaceAll('-', '_')];
  $('server-read-only').checked = item.read_only ?? true;
  show($('server-install-panel'), false);
}
function switchWorkspace(id) {
  if (id === state.workspace) return true;
  if (state.busy || state.mutations || settingsState.busy) {
    toast('当前服务器有操作进行中，请等待完成后切换。');
    $('workspace-select').value = state.workspace;
    return false;
  }
  if (!settingsDiscard()) { $('workspace-select').value = state.workspace; return false; }
  if (id !== 'local' && serverProfile(id)?.connection.status !== 'connected') return false;
  state.workspace = id;
  state.workspaceVersion++;
  state.detailVersion++;
  state.selected = state.detail = state.overview = null;
  state.loading = state.reloadPending = false;
  state.offset = 0;
  state.partitions.clear(); state.statuses.clear(); state.comparison.clear();
  for (const field of ['search', 'since', 'until', 'cwd-filter', 'tag-filter', 'question', 'confirm-id']) $(field).value = '';
  for (const dialog of document.querySelectorAll('dialog[open]')) if (dialog.id !== 'servers-dialog') dialog.close();
  settingsState.data = null; settingsState.dirty = false;
  $('settings-fields').replaceChildren();
  $('settings-result').textContent = '';
  $('health-content').replaceChildren(); $('health-result').textContent = '';
  $('compare-content').replaceChildren(); $('chat').replaceChildren();
  $('compare-count').textContent = '0 / 6'; $('compare-open').disabled = true;
  $('jobs').replaceChildren(); $('detail').replaceChildren(el('p', 'small-empty', '正在读取所选服务器…'));
  $('host').textContent = serverName(); $('data-root').textContent = '—';
  for (const key of ['stat-total', 'stat-active', 'stat-completed', 'stat-failed', 'nav-all', 'nav-active', 'nav-failed']) $(key).textContent = '—';
  updateStateView(); renderPartitions(); renderServerList(); setBusy(false); error('');
  load();
  return true;
}
async function serverAction(body) {
  if (serversState.busy) return;
  const affected = body.id || body.profile?.id;
  if (affected === state.workspace && !switchWorkspace('local')) return;
  serversState.busy = true;
  serverError(''); renderServerList();
  try {
    serversState.data = await api('/api/servers', body);
    if (body.action === 'install') show($('server-install-panel'), false);
    if (body.action === 'save') editServer();
  } catch (e) { serverError(e.message); }
  finally { serversState.busy = false; renderServerList(); }
}
$('workspace-select').addEventListener('change', () => switchWorkspace($('workspace-select').value));
$('servers-open').addEventListener('click', () => { $('servers-dialog').showModal(); loadServers(); });
$('servers-close').addEventListener('click', () => $('servers-dialog').close());
$('server-new').addEventListener('click', () => editServer());
$('server-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const profile = {read_only: $('server-read-only').checked};
  for (const key of serverFields) profile[key.replaceAll('-', '_')] = $('server-' + key).value.trim();
  if ($('server-id').value) profile.id = $('server-id').value;
  serverAction({action: 'save', profile, revision: serversState.data.revision});
});
$('server-install').addEventListener('click', () => {
  const item = serverProfile(serversState.install);
  if (!item || $('server-install-confirm').value !== item.alias) { serverError('请输入完全一致的 SSH 别名确认安装。'); return; }
  serverAction({action: 'install', id: item.id, confirm_alias: item.alias});
});
setInterval(() => {
  if (!document.hidden && !serversState.busy && ($('servers-dialog').open || serversState.data?.items.some((item) => item.connection.status === 'installing'))) loadServers();
}, 2000);
ensureServers();
