"use strict";

function healthTable(headers, rows) {
  const wrap = el('div', 'health-table-wrap');
  const table = el('table', 'health-table');
  const head = el('thead'), row = el('tr'), body = el('tbody');
  headers.forEach((label) => row.append(el('th', '', label)));
  head.append(row);
  for (const values of rows) {
    const tr = el('tr');
    for (const item of values) {
      const td = el('td');
      td.append(item instanceof Node ? item : document.createTextNode(value(item)));
      tr.append(td);
    }
    body.append(tr);
  }
  table.append(head, body);
  wrap.append(table);
  if (!rows.length) wrap.append(el('p', 'small-empty', '暂无记录'));
  return wrap;
}

async function loadHealth() {
  $('health-content').replaceChildren(el('p', '', '正在检查…'));
  $('health-backup').disabled = !settingsWritable() || state.busy;
  try {
    const data = await api('/api/health');
    const facts = el('dl', 'health-facts');
    const tracker = data.tracker;
    for (const [label, text] of [
      ['数据库', data.database_error || '完整性检查通过'],
      ['自动跟踪', data.cron_enabled === null ? '无法读取 cron' : data.cron_enabled ? '已启用' : '未启用'],
      ['最近跟踪成功', date(tracker.succeeded_at)],
      ['跟踪新鲜度', data.tracker_stale ? '超过 5 分钟或尚未成功' : '正常'],
      ['最近跟踪错误', tracker.error || '无'],
      ['可用磁盘', (data.disk_free_bytes / 1024 ** 3).toFixed(1) + ' GiB'],
      ['队列统计', JSON.stringify(data.queue)],
      ['通知统计', JSON.stringify(data.notifications)],
    ]) facts.append(el('dt', '', label), el('dd', '', text));
    const workRows = data.work.map((item) => {
      const retry = iconButton('refresh-cw', `重试后台工作 ${item.id}`, async () => {
        retry.disabled = true;
        await operate({action: 'work_retry', id: item.id});
        await loadHealth();
      });
      retry.disabled = item.status !== 'failed' || !settingsWritable() || state.busy;
      return [item.id, item.job_id, item.kind, item.status, item.attempts, item.last_error, retry];
    });
    $('health-content').replaceChildren(facts, el('h3', '', '后台工作 · 最近 100 条'),
      healthTable(['ID', '任务', '类型', '状态', '尝试', '错误', '重试'], workRows),
      el('h3', '', '模型调用 · 最近 100 条'),
      healthTable(['时间', '接口', '请求模型', '返回模型', '毫秒', '状态', '错误', 'Fallback'],
        data.calls.map((call) => [date(call.created_at, true), call.format, call.requested_model, call.response_model,
          call.elapsed_ms, call.status, call.error_code, call.fallback ? '是' : '否'])));
  } catch (e) { $('health-content').replaceChildren(el('p', 'error-banner', e.message)); }
}

$('health-open').addEventListener('click', () => { $('health-dialog').showModal(); loadHealth(); });
$('health-close').addEventListener('click', () => $('health-dialog').close());
$('health-reload').addEventListener('click', loadHealth);
$('health-backup').addEventListener('click', async () => {
  if (!settingsWritable() || state.busy) return;
  $('health-backup').disabled = true;
  try {
    const op = await api('/api/operations', {action: 'backup'});
    $('health-result').textContent = '正在创建备份…';
    for (;;) {
      await new Promise((resolve) => setTimeout(resolve, 700));
      const result = await api(`/api/operations/${op.id}`);
      if (result.status === 'running') continue;
      if (result.error) throw new Error(result.error);
      const saved = JSON.parse(result.result);
      $('health-result').textContent = `备份已校验：${saved.path}`;
      break;
    }
  } catch (e) { $('health-result').textContent = e.message; }
  finally { $('health-backup').disabled = !settingsWritable(); }
});

let advancedTimer;
for (const id of ['since', 'until', 'cwd-filter', 'tag-filter']) {
  $(id).addEventListener('input', () => {
    clearTimeout(advancedTimer);
    advancedTimer = setTimeout(() => { state.offset = 0; load(); }, 300);
  });
}
$('compare-close').addEventListener('click', () => $('compare-dialog').close());
$('compare-open').addEventListener('click', async () => {
  $('compare-dialog').showModal();
  $('compare-content').replaceChildren(el('p', '', '正在读取…'));
  try {
    const result = await api('/api/compare?' + new URLSearchParams({ids: [...state.comparison].join(',')}));
    const jobs = result.jobs.map((job) => ({...job, effective_chdir: job.effective_chdir || job.submit_cwd}));
    const fields = [['job_name','名称'], ['state','状态'], ['exit_code','退出码'], ['partition','分区'],
      ['elapsed','耗时'], ['max_rss','最大内存'], ['alloc_cpus','CPU 数'], ['nodelist','节点'],
      ['submitted_at','提交时间'], ['effective_chdir','工作目录'], ['command','命令 / 参数'],
      ['original_script_path','输入脚本'], ['git_commit','Git 提交'], ['tags','标签'],
      ['summary','提交摘要'], ['completion_summary','完成摘要']];
    $('compare-content').replaceChildren(healthTable(['字段', ...jobs.map((job) => job.job_id)],
      fields.map(([key, label]) => [label, ...jobs.map((job) => typeof job[key] === 'object' && job[key] !== null ? json(job[key]) : job[key])])));
  } catch (e) { $('compare-content').replaceChildren(el('p', 'error-banner', e.message)); }
});
