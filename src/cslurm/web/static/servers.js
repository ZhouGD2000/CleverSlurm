"use strict";

const serversState = {data: null, busy: false, loading: false, install: null};
const discoveryState = {aliases: new Set(), version: 0, busy: false, environments: [], rootManual: false};
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
  $('server-save').disabled = data.management_read_only || serversState.busy || discoveryState.busy;
  $('server-detect').disabled = data.management_read_only || serversState.busy || discoveryState.busy;
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
  discoveryState.version++;
  discoveryState.busy = false;
  discoveryState.environments = [];
  discoveryState.rootManual = !!item.id;
  show($('server-environment-label'), false);
  show($('server-detection'), false);
  $('server-advanced').open = !!item.id;
  $('server-id').value = item.id || '';
  $('server-form-title').textContent = item.id ? `编辑 ${item.name}` : '添加服务器';
  const defaults = {name: '', alias: '', python: 'python3', root: '~/.cslurm', install_dir: '~/.local/share/cleverslurm'};
  for (const key of serverFields) $('server-' + key).value = item[key.replaceAll('-', '_')] ?? defaults[key.replaceAll('-', '_')];
  $('server-read-only').checked = item.read_only ?? true;
  show($('server-install-panel'), false);
  if (serversState.data) renderServerList();
}

async function loadSshAliases() {
  try {
    const result = await api('/api/servers/ssh');
    discoveryState.aliases = new Set(result.items.map(item => item.alias));
    $('server-ssh-options').replaceChildren(...result.items.map(item => new Option(item.alias, item.alias)));
    $('server-ssh-source').textContent = `SSH 配置：${result.config || '~/.ssh/config'} · ${result.items.length} 个别名` +
      (result.warnings.length ? '\n部分配置未读取，仍可手动输入别名。' : '');
  } catch (e) {
    $('server-ssh-source').textContent = `SSH 配置读取失败：${e.message}`;
  }
}
function selectEnvironment() {
  const item = discoveryState.environments[Number($('server-environment').value)];
  if (!item) return;
  $('server-python').value = item.python;
  if (!discoveryState.rootManual) $('server-root').value = item.root;
  describeEnvironment(item);
}
function describeEnvironment(item) {
  const status = item.installed ? `CleverSlurm ${item.version || 'source'} · ${item.package || ''}` : '此 Python 环境未安装 CleverSlurm';
  $('server-detection').textContent = `Python ${item.python_version} · ${item.python}\n${status}\n` +
    `数据目录${discoveryState.rootManual ? '（保留已填写值）' : item.root_source === 'default' ? '（默认值）' : '（环境变量）'}：${$('server-root').value}` +
    (!item.supported ? '\n此环境不满足 Python 3.10+ 要求。' : item.installed && !item.web ? '\n此安装未检测到网页模块。' : '');
}
async function detectServer() {
  if (discoveryState.busy || serversState.busy) return;
  const alias = $('server-alias').value.trim();
  if (!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(alias)) { serverError('请选择或输入有效的 SSH 别名。'); return; }
  const version = ++discoveryState.version;
  const pythonBefore = $('server-python').value;
  discoveryState.busy = true;
  discoveryState.environments = [];
  serverError('');
  show($('server-environment-label'), false);
  show($('server-detection'));
  $('server-detection').textContent = `正在检测 ${alias}…`;
  if (!$('server-name').value.trim()) $('server-name').value = alias;
  renderServerList();
  try {
    const result = await api('/api/servers/discover', {alias});
    if (version !== discoveryState.version || alias !== $('server-alias').value.trim()) return;
    discoveryState.environments = result.environments;
    const select = $('server-environment');
    select.replaceChildren(...result.environments.map((item, index) => new Option(
      `Python ${item.python_version} · ${item.installed ? 'CleverSlurm ' + item.version : '未安装'} · ${item.python}`, String(index))));
    const previous = result.environments.findIndex(item => item.python === pythonBefore);
    select.value = String($('server-id').value && previous >= 0 ? previous : 0);
    show($('server-environment-label'), result.environments.length > 0);
    if (result.environments.length) {
      // A manual interpreter edit made during detection must not be overwritten.
      if ($('server-python').value === pythonBefore) selectEnvironment();
      else $('server-detection').textContent = '环境检测完成；已保留手动填写的 Python。';
    } else {
      $('server-detection').textContent = '未检测到可用的 Python 环境，请在高级设置中指定路径。';
      $('server-advanced').open = true;
    }
    if (result.warnings?.length) $('server-detection').textContent += '\n部分环境未完成检测；结果不代表服务器上的全部安装。';
  } catch (e) {
    if (version !== discoveryState.version) return;
    $('server-detection').textContent = '检测失败，无法确定远端安装状态。';
    serverError(e.message);
    $('server-advanced').open = true;
  } finally {
    if (version === discoveryState.version) { discoveryState.busy = false; renderServerList(); }
  }
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
$('servers-open').addEventListener('click', () => { $('servers-dialog').showModal(); loadServers(); loadSshAliases(); });
$('servers-close').addEventListener('click', () => $('servers-dialog').close());
$('server-new').addEventListener('click', () => editServer());
$('server-detect').addEventListener('click', detectServer);
$('server-environment').addEventListener('change', selectEnvironment);
$('server-root').addEventListener('input', () => {
  discoveryState.rootManual = true;
  const item = discoveryState.environments[Number($('server-environment').value)];
  if (item) describeEnvironment(item);
});
$('server-alias').addEventListener('input', () => {
  discoveryState.version++;
  discoveryState.busy = false;
  discoveryState.environments = [];
  show($('server-environment-label'), false);
  show($('server-detection'), false);
  if (serversState.data) renderServerList();
});
$('server-alias').addEventListener('change', () => {
  if (discoveryState.aliases.has($('server-alias').value.trim())) detectServer();
});
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
