"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  token: sessionStorage.getItem("cweb-token") || "",
  offset: 0,
  limit: 25,
  total: 0,
  selected: null,
  detail: null,
  tab: "summary",
  kind: "submission",
  overview: null,
  busy: false,
  loading: false,
  reloadPending: false,
  detailVersion: 0,
  partitions: new Set(),
  statuses: new Set(),
  comparison: new Set(),
  workspace: 'local',
  workspaceVersion: 0,
  mutations: 0,
};
const fromLink = new URLSearchParams(location.hash.slice(1)).get("token");
if (fromLink) {
  state.token = fromLink;
  sessionStorage.setItem("cweb-token", fromLink);
  history.replaceState(null, "", location.pathname);
}
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};
const icon = (name) => {
  const img = document.createElement("img");
  img.src = `/icons/${name}.svg`;
  img.alt = "";
  return img;
};
const show = (node, visible = true) => {
  node.hidden = !visible;
};
const value = (v) =>
  v === null || v === undefined || v === "" ? "—" : String(v);
const json = (v) => (typeof v === "string" ? v : JSON.stringify(v, null, 2));
const date = (v, short = false) => {
  if (!v) return "—";
  const d = new Date(v);
  return Number.isNaN(d.getTime())
    ? String(v)
    : d.toLocaleString(
        "zh-CN",
        short
          ? {
              month: "2-digit",
              day: "2-digit",
              hour: "2-digit",
              minute: "2-digit",
              hour12: false,
            }
          : { hour12: false },
      );
};
function badge(v) {
  return el(
    "span",
    "badge " + (String(v).match(/^[A-Z_]+$/) ? v : ""),
    value(v),
  );
}
function error(message) {
  $("error").textContent = message;
  show($("error"), !!message);
}
function toast(message, persistent = false) {
  clearTimeout(toast.timer);
  $("toast").textContent = message;
  show($("toast"));
  if (!persistent)
    toast.timer = setTimeout(() => show($("toast"), false), 6000);
}
function auth(message = "") {
  show($("app"), false);
  show($("auth"));
  $("auth-error").textContent = message;
}
async function api(path, body) {
  const management = path === '/api/servers';
  const workspace = management ? 'local' : state.workspace;
  const version = state.workspaceVersion;
  if (body && !management) state.mutations++;
  try {
  const response = await fetch(path, {
    signal: AbortSignal.timeout(workspace === 'local' && !management ? 20000 : 90000),
    method: body ? "POST" : "GET",
    headers: {
      Authorization: `Bearer ${state.token}`,
      'X-CSlurm-Server': workspace,
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  const data = await response.json();
  if (!management && version !== state.workspaceVersion) {
    const error = new Error('服务器已切换'); error.staleWorkspace = true; throw error;
  }
  if (!response.ok) {
    if (response.status === 403 && data.error.includes("access link"))
      auth("访问令牌已失效，请使用 cweb 启动时显示的新链接。");
    throw new Error(data.error || `HTTP ${response.status}`);
  }
  return data;
  } finally {
    if (body && !management) state.mutations--;
  }
}
function iconButton(name, label, callback) {
  const button = el("button", "icon-button");
  button.type = "button";
  button.title = label;
  button.setAttribute("aria-label", label);
  button.append(icon(name));
  button.addEventListener("click", callback);
  return button;
}
function setBusy(busy) {
  state.busy = busy;
  const disabled =
    busy ||
    !state.overview ||
    state.overview.read_only ||
    state.overview.demo ||
    state.overview.database_error;
  for (const id of ["refresh", "ask-send"]) $(id).disabled = disabled;
  $("refresh").classList.toggle("busy", busy);
  for (const button of document.querySelectorAll("[data-operation]"))
    button.disabled = disabled || button.dataset.blocked === "true";
}
function renderCheckboxFilter(id, selected, names, noun, labels = {}) {
  const options = $(id + "-options");
  const all = "全部" + noun;
  const key = JSON.stringify(names);
  if (options.dataset.options !== key) {
    options.dataset.options = key;
    options.replaceChildren(...["", ...names].map((name) => {
      const label = el("label", "checkbox-option");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = name;
      label.append(checkbox, el("span", "", name ? labels[name] || name : all));
      return label;
    }));
  }
  for (const input of options.querySelectorAll("input"))
    input.checked = input.value ? selected.has(input.value) : selected.size === 0;
  const titles = [...selected].map((name) => labels[name] || name);
  $(id + "-caption").textContent = selected.size === 0
    ? all : selected.size === 1 ? titles[0] : `${noun} (${selected.size})`;
  $(id + "-button").title = titles.join(", ") || all;
}
function renderPartitions() {
  const names = [...new Set([...(state.overview?.partitions || []), ...state.partitions])].sort();
  renderCheckboxFilter("partition", state.partitions, names, "分区");
}
function renderStates() {
  const names = [...new Set([
    "active", "failed", "RUNNING", "PENDING", "COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "UNKNOWN",
    ...Object.keys(state.overview?.states || {}), ...state.statuses,
  ])];
  renderCheckboxFilter("state", state.statuses, names, "状态", {active: "活动任务", failed: "异常退出"});
}
async function load() {
  if (!state.token) return;
  if (state.loading) {
    state.reloadPending = true;
    return;
  }
  state.loading = true;
  const workspaceVersion = state.workspaceVersion;
  try {
    const query = new URLSearchParams({
      search: $("search").value.trim(),
      state: [...state.statuses].join(","),
      partition: [...state.partitions].join(","),
      offset: state.offset,
      limit: state.limit,
      since: $('since').value,
      until: $('until').value,
      cwd: $('cwd-filter').value.trim(),
      tag: $('tag-filter').value.trim(),
    });
    const overview = await api("/api/overview");
    state.overview = overview;
    if (overview.database_error) {
      state.selected = null;
      state.detail = null;
      $("host").textContent = overview.host;
      $("data-root").textContent = overview.root;
      $("mode").textContent = "DB ERROR";
      show($("auth"), false);
      show($("app"));
      $("jobs").replaceChildren();
      $("detail").replaceChildren(
        el("p", "small-empty", "数据库异常，暂不可读取任务详情。"),
      );
      $("page-info").textContent = "数据库不可用";
      for (const id of [
        "stat-total",
        "stat-active",
        "stat-completed",
        "stat-failed",
        "nav-all",
        "nav-active",
        "nav-failed",
      ])
        $(id).textContent = "—";
      setBusy(false);
      throw new Error(
        "数据库完整性检查失败或数据库不可用。已停止读取任务和执行操作，请在服务器上备份、修复后重启 cweb。",
      );
    }
    const result = await api("/api/jobs?" + query);
    state.overview = overview;
    state.total = result.total;
    for (const [id, val] of [
      ["stat-total", overview.total],
      ["stat-active", overview.active],
      ["stat-completed", overview.completed],
      ["stat-failed", overview.failed],
      ["nav-all", overview.total],
      ["nav-active", overview.active],
      ["nav-failed", overview.failed],
    ])
      $(id).textContent = val.toLocaleString();
    $("host").textContent = overview.host;
    $("host").title = overview.host;
    $("data-root").textContent = overview.root;
    $("mode").textContent = overview.demo
      ? "DEMO"
      : overview.read_only
        ? "READ ONLY"
        : state.workspace === 'local' ? "LOCAL" : "SSH";
    $("notice").textContent = overview.demo
      ? "演示数据 · 与实际 Slurm、AI 和通知隔离，操作已禁用"
      : overview.read_only
        ? "只读模式 · 所有网页操作已禁用"
        : "";
    show($("notice"), !!$("notice").textContent);
    renderPartitions();
    renderStates();
    if (state.offset && !result.items.length && result.total <= state.offset) {
      state.offset = 0;
      state.loading = false;
      return load();
    }
    renderJobs(result.items);
    if (!result.items.length && !state.selected) $('detail').replaceChildren(el('p', 'small-empty', '没有可显示的任务'));
    $("page-info").textContent = result.total
      ? `${state.offset + 1}–${state.offset + result.items.length} / ${result.total} 条记录`
      : "0 条记录";
    $("previous").disabled = state.offset === 0;
    $("next").disabled = state.offset + state.limit >= result.total;
    $("connection").classList.remove("offline");
    $("connection").replaceChildren(el("i"), document.createTextNode("已连接"));
    $("record-time").textContent =
      "记录更新 " + date(overview.updated_at, true);
    $("read-time").textContent =
      "读取于 " + new Date().toLocaleTimeString("zh-CN", { hour12: false });
    $("clock").textContent = new Date().toLocaleDateString("zh-CN");
    show($("auth"), false);
    show($("app"));
    error("");
    setBusy(state.busy);
    if (typeof ensureServers === 'function') ensureServers();
    if (!state.selected && result.items.length)
      await selectJob(result.items[0].job_id, false);
    if (overview.active_operation && !state.busy)
      watch(overview.active_operation);
  } catch (e) {
    if (workspaceVersion !== state.workspaceVersion) return;
    error(e.message);
    $("connection").classList.add("offline");
    $("connection").replaceChildren(
      el("i"),
      document.createTextNode("连接异常"),
    );
    if ($("app").hidden) auth(e.message);
  } finally {
    if (workspaceVersion !== state.workspaceVersion) return;
    state.loading = false;
    if (state.reloadPending) {
      state.reloadPending = false;
      load();
    }
  }
}
function renderJobs(items) {
  $("jobs").replaceChildren();
  show($("empty"), !items.length);
  for (const job of items) {
    const row = el("tr", job.job_id === state.selected ? "selected" : "");
    row.dataset.job = job.job_id;
    const name = el("td"),
      link = el("button", "job-link", job.job_id);
    link.addEventListener("click", () => selectJob(job.job_id));
    link.setAttribute(
      "aria-label",
      `查看任务 ${job.job_id} ${job.job_name || ""}`,
    );
    const check = el('input', 'compare-check');
    check.type = 'checkbox';
    check.setAttribute('aria-label', `对比任务 ${job.job_id}`);
    check.checked = state.comparison.has(job.job_id);
    check.addEventListener('change', () => {
      if (check.checked && state.comparison.size >= 6) {
        check.checked = false;
        toast('最多选择 6 个任务');
        return;
      }
      if (check.checked) state.comparison.add(job.job_id); else state.comparison.delete(job.job_id);
      $('compare-count').textContent = `${state.comparison.size} / 6`;
      $('compare-open').disabled = state.comparison.size < 2;
    });
    name.append(check, link, el("span", "job-name", job.job_name || "未命名任务"));
    name.title = job.job_name || job.job_id;
    const status = el("td");
    status.append(badge(job.state));
    const partition = el("td", "mono", value(job.partition));
    partition.append(el("span", "cell-secondary", value(job.nodelist)));
    const elapsed = el("td", "mono", value(job.elapsed));
    const submitted = el("td", "mono", date(job.submitted_at, true));
    submitted.title = date(job.submitted_at);
    row.append(name, status, partition, elapsed, submitted);
    row.addEventListener("click", (e) => {
      if (!e.target.closest("button,input")) selectJob(job.job_id);
    });
    $("jobs").append(row);
  }
}
async function selectJob(id, scroll = true) {
  const version = ++state.detailVersion;
  state.selected = id;
  document
    .querySelectorAll("#jobs tr")
    .forEach((row) => row.classList.toggle("selected", row.dataset.job === id));
  $("detail").replaceChildren(el("p", "small-empty", "正在读取任务…"));
  try {
    const detail = await api(`/api/jobs/${encodeURIComponent(id)}`);
    if (version !== state.detailVersion) return;
    state.detail = detail;
    renderDetail();
    if (scroll && innerWidth <= 700)
      $("detail").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) {
    if (version === state.detailVersion)
      $("detail").replaceChildren(el("p", "small-empty", e.message));
  }
}
function fact(label, v, wide = false) {
  const node = el("div", "fact" + (wide ? " wide" : ""));
  node.append(el("dt", "", label), el("dd", "", value(v)));
  return node;
}
function sectionTitle(label) {
  return el("h3", "section-title", label);
}
function renderDetail() {
  if (!state.detail) return;
  const d = state.detail,
    job = d.job,
    root = $("detail");
  root.replaceChildren();
  const header = el("div", "detail-header"),
    top = el("div", "detail-top", "任务详情");
  const tools = el("div");
  tools.append(
    iconButton("download", "导出任务 JSON", () => {
      const url = URL.createObjectURL(
        new Blob([JSON.stringify(state.detail, null, 2)], {
          type: "application/json",
        }),
      );
      const a = document.createElement("a");
      a.href = url;
      a.download = `job-${job.job_id}.json`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }),
    iconButton("refresh-cw", "刷新详情", () => selectJob(job.job_id, false)),
  );
  top.append(tools);
  const title = el("div", "detail-title");
  title.append(el("h2", "mono", "#" + job.job_id), badge(job.state));
  header.append(top, title, el("p", "detail-name", value(job.job_name)));
  root.append(header);
  const tabs = el("div", "detail-tabs");
  tabs.setAttribute("role", "tablist");
  for (const [key, label] of [
    ["summary", "概览"],
    ["logs", "日志"],
    ["files", "文件"],
    ["commands", "命令"],
    ["events", "事件"],
    ["notifications", "通知"],
  ]) {
    const button = el("button", state.tab === key ? "active" : "", label);
    button.setAttribute("role", "tab");
    button.setAttribute("aria-selected", String(state.tab === key));
    button.addEventListener("click", () => {
      state.tab = key;
      renderDetail();
    });
    tabs.append(button);
  }
  root.append(tabs);
  const body = el("div", "detail-body");
  body.setAttribute("role", "tabpanel");
  root.append(body);
  if (state.tab === "summary") renderSummary(body, job, d);
  else if (state.tab === "logs") renderLogs(body, job.job_id);
  else
    renderRecords(body, state.tab, d[state.tab], d[state.tab + "_truncated"]);
  setBusy(state.busy);
}
function renderSummary(body, job, d) {
  body.append(sectionTitle("运行信息"));
  const grid = el("dl", "fact-grid");
  grid.append(
    fact("分区", job.partition),
    fact("节点", job.nodelist),
    fact("运行时长", job.elapsed),
    fact("峰值内存", job.max_rss),
    fact(
      "退出码 / 派生退出码",
      `${value(job.exit_code)} / ${value(job.derived_exit_code)}`,
    ),
    fact("CPU / 节点数", `${value(job.alloc_cpus)} / ${value(job.nodes)}`),
    fact("提交时间", date(job.submitted_at), true),
    fact("工作目录", job.effective_chdir || job.submit_cwd, true),
  );
  body.append(grid);
  if (d.analysis.length) {
    const analysis = d.analysis[0];
    const program = el("div", "detail-section");
    program.append(
      sectionTitle("程序分析"),
      el(
        "p",
        "summary-text",
        `${value(analysis.semantic_status)} · ${value(analysis.failure_category)}`,
      ),
    );
    body.append(program);
  }
  const summary = el("div", "detail-section"),
    title = sectionTitle("AI 摘要");
  title.append(icon("sparkles"));
  summary.append(title);
  const controls = el("div", "summary-controls"),
    kind = el("select");
  kind.setAttribute("aria-label", "摘要类型");
  kind.append(
    new Option("提交摘要", "submission"),
    new Option("完成摘要", "completion"),
  );
  kind.value = state.kind;
  kind.addEventListener("change", () => {
    state.kind = kind.value;
    renderDetail();
  });
  const generate = el("button", "subtle", "更新摘要");
  generate.prepend(icon("sparkles"));
  generate.dataset.operation = "summarize";
  generate.addEventListener("click", () =>
    operate({ action: "summarize", job_id: job.job_id, kind: state.kind }),
  );
  controls.append(kind, generate);
  summary.append(controls);
  const data =
    state.kind === "completion" ? job.completion_summary : job.summary;
  if (!data) summary.append(el("p", "small-empty", "尚无已保存的摘要"));
  else {
    const text =
      typeof data === "string"
        ? data
        : data.one_line_summary ||
          data.human_summary ||
          data.short_summary ||
          data.raw_ai_summary ||
          "";
    if (text) summary.append(el("p", "summary-text", text));
    if (typeof data !== "string") {
      const details = el("details");
      details.append(
        el("summary", "summary-note", "完整摘要数据"),
        el("pre", "code-block", json(data)),
      );
      summary.append(details);
    }
  }
  summary.append(
    el("p", "summary-note", "AI 摘要基于记录；调度状态不等同于科学计算结果。"),
  );
  body.append(summary);
  const command = el("div", "detail-section");
  command.append(
    sectionTitle("提交命令"),
    el("pre", "code-block", value(job.command)),
  );
  body.append(command);
  const footer = el("div", "detail-footer"),
    cancel = el("button", "danger", "取消任务");
  cancel.dataset.operation = "cancel";
  const active = [
    "PENDING",
    "RUNNING",
    "CONFIGURING",
    "COMPLETING",
    "SUSPENDED",
    "REQUEUED",
    "RESIZING",
  ].includes(job.state);
  const allowed =
    state.overview?.allow_cancel &&
    active &&
    /^\d+(?:_\d+)?$/.test(job.job_id) &&
    !(job.array_spec && !job.job_id.includes("_"));
  cancel.dataset.blocked = String(!allowed);
  cancel.title = allowed
    ? "需要输入 Job ID 确认"
    : "未开启取消权限、非活动任务或数组父任务";
  cancel.addEventListener("click", () => {
    $("cancel-id").textContent = job.job_id;
    $("confirm-id").value = "";
    $("cancel-dialog").showModal();
  });
  footer.append(el("span", "", "单任务操作"), cancel);
  body.append(footer);
}
async function renderLogs(body, id) {
  body.append(el("p", "small-empty", "正在读取日志…"));
  try {
    const logs = await api(`/api/jobs/${encodeURIComponent(id)}/logs`);
    if (!body.isConnected) return;
    body.replaceChildren();
    if (!logs.items.length)
      body.append(el("p", "small-empty", "尚未记录 stdout / stderr 路径"));
    for (const log of logs.items) {
      body.append(
        el("h3", "log-header", log.stream),
        el("p", "log-path", log.path),
        el("pre", "code-block log-text", log.error || log.text || "日志为空"),
      );
      if (log.truncated)
        body.append(
          el("p", "summary-note", "仅显示末尾 200 行，最多读取 128 KiB。"),
        );
    }
  } catch (e) {
    if (body.isConnected)
      body.replaceChildren(el("p", "small-empty", e.message));
  }
}
function renderRecords(body, key, records, truncated) {
  if (!records.length) body.append(el("p", "small-empty", "尚无记录"));
  if (truncated) body.append(el("p", "summary-note", "仅显示最近 200 条记录"));
  for (const row of records) {
    const item = el("div", "record-item");
    if (key === "files")
      item.append(
        el("h3", "mono", row.relpath || row.path),
        el("p", "", row.path),
        el(
          "div",
          "record-meta",
          `${value(row.role)} · ${value(row.source)} · ${row.size == null ? "大小未知" : `${row.size.toLocaleString()} bytes`}`,
        ),
      );
    if (key === "commands")
      item.append(
        el("h3", "", value(row.executable)),
        el("pre", "code-block", value(row.argv)),
        el("p", "", row.entry_file_abs || row.entry_file || row.cwd || ""),
        el("div", "record-meta", `${value(row.source)} · ${date(row.time)}`),
      );
    if (key === "events")
      item.append(
        el("h3", "mono", value(row.event_type)),
        el("div", "record-meta", date(row.event_time)),
        el("p", "", row.note || row.raw_output || row.command || ""),
      );
    if (key === "notifications")
      item.append(
        el("h3", "", value(row.title)),
        el(
          "div",
          "record-meta",
          `${value(row.status)} · ${value(row.mode)} · ${date(row.created_at)}`,
        ),
        el("p", "", row.body || ""),
        el("p", "", row.last_error || ""),
      );
    body.append(item);
  }
}
function chat(role, text) {
  $("chat").querySelector(".chat-intro")?.remove();
  const message = el("div", "chat-message " + role);
  message.append(
    el("strong", "", role === "user" ? "你" : "CleverSlurm"),
    document.createTextNode(text),
  );
  $("chat").append(message);
  $("chat").scrollTop = $("chat").scrollHeight;
}
async function operate(body) {
  if (state.busy) return;
  setBusy(true);
  try {
    const op = await api("/api/operations", body);
    if (body.action === "ask") {
      chat("user", body.question);
      $("question").value = "";
    }
    await watch(op.id);
  } catch (e) {
    error(e.message);
    toast(e.message);
    setBusy(false);
  }
}
async function watch(id) {
  setBusy(true);
  toast("后台操作进行中，任务记录仍可浏览…", true);
  try {
    while (true) {
      const op = await api(`/api/operations/${id}`);
      if (op.status !== "running") {
        if (op.status === "failed") {
          error(op.error);
          toast(op.error);
          if (op.action === "ask") chat("assistant", op.error);
        } else {
          toast(
            op.action === "cancel"
              ? "取消请求已发送，最终状态以 Slurm 同步结果为准。"
              : "操作已完成",
          );
          if (op.action === "ask") {
            chat("assistant", op.result);
            if (!$("ask-dialog").open) $("ask-dialog").showModal();
          }
        }
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 1500));
    }
  } catch (e) {
    error(e.message);
    toast("操作状态暂不可用，请确认服务器状态后再重试。");
  } finally {
    setBusy(false);
    await load();
    if (state.selected) await selectJob(state.selected, false);
  }
}
function updateStateView() {
  const view = state.statuses.size === 0 ? "all"
    : state.statuses.size === 1 ? [...state.statuses][0] : "custom";
  $("view-title").textContent =
    {
      all: "全部任务",
      active: "活动任务",
      failed: "异常任务",
      COMPLETED: "已完成任务",
    }[view] || "任务记录";
  document
    .querySelectorAll(".nav[data-view]")
    .forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  renderStates();
}
function filter(view) {
  state.statuses = new Set(view === "all" ? [] : [view]);
  updateStateView();
  state.offset = 0;
  load();
}
document
  .querySelectorAll("[data-view]")
  .forEach((button) =>
    button.addEventListener("click", () => filter(button.dataset.view)),
  );
$("auth-form").addEventListener("submit", (e) => {
  e.preventDefault();
  let token = $("access-token").value.trim();
  try {
    token =
      new URLSearchParams(new URL(token).hash.slice(1)).get("token") || token;
  } catch (_) {}
  state.token = token;
  sessionStorage.setItem("cweb-token", token);
  $("access-token").value = "";
  load();
});
$("filters").addEventListener("submit", (e) => {
  e.preventDefault();
  state.offset = 0;
  load();
});
let searchTimer;
$("search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => {
    state.offset = 0;
    load();
  }, 300);
});
for (const [id, key] of [["state", "statuses"], ["partition", "partitions"]]) {
  $(id + "-options").addEventListener("change", (event) => {
    const input = event.target;
    if (!input.matches('input[type="checkbox"]')) return;
    if (!input.value) state[key].clear();
    else if (input.checked) state[key].add(input.value);
    else state[key].delete(input.value);
    if (id === "state") updateStateView();
    else renderPartitions();
    state.offset = 0;
    load();
  });
  document.addEventListener("click", (event) => {
    if (!$(id).contains(event.target)) $(id).open = false;
  });
  $(id).addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      $(id).open = false;
      $(id + "-button").focus();
    }
  });
}
$("clear-filters").addEventListener("click", () => {
  $("search").value = "";
  for (const id of ['since', 'until', 'cwd-filter', 'tag-filter']) $(id).value = '';
  state.partitions.clear();
  renderPartitions();
  filter("all");
});
$("page-size").addEventListener("change", () => {
  state.limit = Number($("page-size").value);
  state.offset = 0;
  load();
});
$("previous").addEventListener("click", () => {
  state.offset = Math.max(0, state.offset - state.limit);
  load();
});
$("next").addEventListener("click", () => {
  state.offset += state.limit;
  load();
});
$("reload").addEventListener("click", () => load());
$("refresh").addEventListener("click", () => operate({ action: "refresh" }));
$("ask-open").addEventListener("click", () => $("ask-dialog").showModal());
$("ask-form").addEventListener("submit", (e) => {
  e.preventDefault();
  if ($("question").value.trim())
    operate({ action: "ask", question: $("question").value.trim() });
});
$("cancel-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const id = $("cancel-id").textContent;
  if ($("confirm-id").value !== id) {
    $("confirm-id").setCustomValidity("请输入完全一致的 Job ID");
    $("confirm-id").reportValidity();
    return;
  }
  $("cancel-dialog").close();
  operate({
    action: "cancel",
    job_id: id,
    confirm_job_id: $("confirm-id").value,
  });
});
$("confirm-id").addEventListener("input", () =>
  $("confirm-id").setCustomValidity(""),
);
document
  .querySelectorAll(".dialog-close")
  .forEach((b) =>
    b.addEventListener("click", () => b.closest("dialog").close()),
  );
document.addEventListener("keydown", (e) => {
  if (e.key === "/" && !e.target.closest("input,textarea,select,dialog")) {
    e.preventDefault();
    $("search").focus();
  }
});
setInterval(async () => {
  if ($("auto-refresh").checked && !document.hidden && !$("app").hidden) {
    await load();
    if (
      state.selected &&
      !state.busy &&
      !document.querySelector("dialog[open]")
    )
      await selectJob(state.selected, false);
  }
}, 15000);
if (state.token) load();
else auth();
