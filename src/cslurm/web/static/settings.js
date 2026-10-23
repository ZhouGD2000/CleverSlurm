"use strict";

const settingsState = {data: null, kind: "ai", id: "current", dirty: false, busy: false, models: []};
const settingsDialog = $("settings-dialog");
function settingsWritable() {
  return state.overview && !state.overview.read_only && !state.overview.demo;
}
function settingsButtons() {
  const disabled = settingsState.busy || !settingsWritable() || !settingsState.data;
  $("settings-fields").disabled = disabled;
  $("settings-name").disabled = disabled;
  $("settings-save").disabled = disabled;
  $("settings-add").disabled = disabled;
  $("settings-activate").disabled = disabled || settingsState.dirty || settingsState.id === "new";
  $("settings-test").disabled = disabled || settingsState.dirty || settingsState.id === "new";
  $("settings-copy").disabled = disabled || settingsState.dirty || settingsState.id === "new";
  $("settings-reload").disabled = settingsState.busy;
  $("settings-profile").disabled = settingsState.busy;
  if ($("settings-fetch-models")) $("settings-fetch-models").disabled = disabled;
  for (const tab of document.querySelectorAll("[data-settings-kind]")) tab.disabled = settingsState.busy;
}
function settingsDiscard() {
  return !settingsState.busy && (!settingsState.dirty || confirm("放弃未保存的配置修改？"));
}
function renderSettings() {
  settingsState.models = [];
  const {data, kind, id} = settingsState;
  const profiles = data.groups[kind];
  const profile = profiles.find((p) => p.id === id) || {values: {}, secrets: {}, name: ""};
  $("settings-profile").replaceChildren(...profiles.map((p) => {
    const option = el("option", "", p.id === "current" ? "当前配置" : p.name);
    option.value = p.id;
    return option;
  }));
  if (id === "new") {
    const option = el("option", "", "新配置");
    option.value = "new";
    $("settings-profile").append(option);
  }
  $("settings-profile").value = id;
  show($("settings-name-row"), id !== "current");
  $("settings-name").value = profile.name || "";
  $("settings-name").required = id !== "current";
  show($("settings-add"), kind !== "notification");
  show($("settings-copy"), kind !== "notification");
  show($("settings-activate"), id !== "current" && kind !== "notification");
  show($("settings-test"), kind !== "notification");
  $("settings-test").textContent = kind === "feishu" ? "发送测试消息" : "测试连接";
  $("settings-overrides").textContent = data.environment_overrides.length
    ? "环境变量覆盖配置文件：" + data.environment_overrides.join(", ") : "";
  show($("settings-overrides"), data.environment_overrides.length > 0);
  $("settings-mode").textContent = state.overview?.demo ? "演示模式：配置不可修改" : "只读模式：配置不可修改";
  show($("settings-mode"), !settingsWritable());
  for (const tab of document.querySelectorAll("[data-settings-kind]"))
    tab.setAttribute("aria-selected", String(tab.dataset.settingsKind === kind));
  $("settings-fields").replaceChildren(...data.fields[kind].map((f) => {
    const label = el("label", "settings-field", f.label);
    let input;
    if (f.choices) {
      input = el("select");
      for (const choice of f.choices) {
        const option = el("option", "", choice === "" ? "默认 / 不设置" : choice === "true" ? "开启" : choice === "false" ? "关闭" : choice);
        option.value = choice;
        input.append(option);
      }
    } else {
      input = el("input");
      input.type = f.secret ? "password" : f.kind === "bool" ? "checkbox" : f.bounds ? "number" : "text";
      input.autocomplete = "off";
      if (f.bounds) {
        input.min = f.bounds[0]; input.max = f.bounds[1];
        input.step = f.kind === "float" ? "any" : "1";
      }
    }
    input.name = f.key;
    input.id = "settings-field-" + f.key;
    label.htmlFor = input.id;
    input.dataset.configField = "true";
    input.value = f.secret ? "" : profile.values[f.key] || (f.choices ? f.default : "");
    if (f.kind === "bool") input.checked = (profile.values[f.key] || f.default) === "true";
    if (!f.choices && f.kind !== "bool") input.placeholder = f.default || "未设置";
    if (f.secret) input.placeholder = profile.secrets[f.key] ? "已配置（留空保留）" : "未配置";
    label.append(input);
    if (kind === "ai" && f.key === "model") {
      const group = el("div", "settings-model-field");
      const row = el("div", "settings-model-input");
      const fetch = iconButton("download", "获取可选模型", fetchSettingsModels);
      fetch.id = "settings-fetch-models";
      row.append(input, fetch);
      label.append(row);
      const picker = el("div", "settings-model-picker");
      picker.id = "settings-model-picker";
      picker.hidden = true;
      const search = el("input");
      search.id = "settings-model-search";
      search.type = "search";
      search.placeholder = "搜索模型";
      search.setAttribute("aria-label", "搜索可选模型");
      search.addEventListener("input", renderModelChoices);
      const select = el("select");
      select.id = "settings-model-choice";
      select.setAttribute("aria-label", "选择可用模型");
      select.addEventListener("change", () => {
        if (!select.value) return;
        input.value = select.value;
        settingsState.dirty = true;
        settingsButtons();
      });
      const status = el("span", "settings-model-status");
      status.id = "settings-model-status";
      status.setAttribute("role", "status");
      picker.append(search, select, status);
      group.append(label, picker);
      return group;
    }
    if (f.secret && profile.secrets[f.key]) {
      const clearLabel = el("label", "settings-clear", "清除已保存值");
      const clear = el("input");
      clear.type = "checkbox";
      clear.dataset.clearSecret = f.key;
      clearLabel.prepend(clear);
      // Separate labels prevent the clear checkbox from stealing field focus.
      const group = el("div", "settings-secret");
      group.append(label, clearLabel);
      return group;
    }
    return label;
  }));
  settingsState.dirty = false;
  settingsDialog.scrollTop = 0;
  $("settings-result").textContent = "";
  settingsButtons();
}
async function loadSettings() {
  settingsState.busy = true;
  settingsButtons();
  try {
    settingsState.data = await api("/api/settings");
    if (!settingsState.data.groups[settingsState.kind].some((p) => p.id === settingsState.id)) settingsState.id = "current";
    renderSettings();
  } catch (e) { $("settings-result").textContent = e.message; }
  finally { settingsState.busy = false; settingsButtons(); }
}
$("settings-open").addEventListener("click", async () => {
  settingsDialog.showModal();
  await loadSettings();
});
$("settings-close").addEventListener("click", () => {
  if (settingsDiscard()) settingsDialog.close();
});
settingsDialog.addEventListener("cancel", (e) => {
  if (!settingsDiscard()) e.preventDefault();
});
$("settings-reload").addEventListener("click", () => { if (settingsDiscard()) loadSettings(); });
document.querySelectorAll("[data-settings-kind]").forEach((tab) => tab.addEventListener("click", () => {
  if (!settingsDiscard() || !settingsState.data) return;
  settingsState.kind = tab.dataset.settingsKind;
  settingsState.id = "current";
  renderSettings();
}));
$("settings-profile").addEventListener("change", () => {
  if (!settingsDiscard()) { $("settings-profile").value = settingsState.id; return; }
  settingsState.id = $("settings-profile").value;
  renderSettings();
});
$("settings-add").addEventListener("click", () => {
  if (!settingsDiscard()) return;
  settingsState.id = "new";
  renderSettings();
  $("settings-name").focus();
});
$("settings-form").addEventListener("input", (event) => {
  if (event.target.id === "settings-model-search" || event.target.id === "settings-model-choice") return;
  settingsState.dirty = true;
  if (["base_url", "models_url", "api_key", "api_key_env", "format", "anthropic_version"].includes(event.target.name)
      || event.target.dataset.clearSecret === "api_key") {
    settingsState.models = [];
    if ($("settings-model-picker")) show($("settings-model-picker"), false);
  }
  settingsButtons();
});
function settingsValues(keys) {
  const values = {};
  for (const f of settingsState.data.fields[settingsState.kind]) {
    if (keys && !keys.includes(f.key)) continue;
    const input = $("settings-fields").querySelector(`[name="${f.key}"]`);
    const clear = $("settings-fields").querySelector(`[data-clear-secret="${f.key}"]`);
    values[f.key] = f.secret ? (clear?.checked ? "" : input.value || null)
      : f.kind === "bool" ? String(input.checked) : input.value;
  }
  return values;
}
function renderModelChoices() {
  const query = $("settings-model-search").value.trim().toLowerCase();
  const matches = settingsState.models.filter((m) => (m.id + " " + m.name).toLowerCase().includes(query));
  const empty = el("option", "", matches.length ? "选择模型" : "无匹配模型");
  empty.value = "";
  $("settings-model-choice").replaceChildren(empty, ...matches.map((model) => {
    const option = el("option", "", model.name && model.name !== model.id ? `${model.id} · ${model.name}` : model.id);
    option.value = model.id;
    return option;
  }));
  $("settings-model-choice").disabled = !matches.length;
}
async function fetchSettingsModels() {
  if (settingsState.busy || !settingsWritable()) return;
  const body = {kind: "ai", id: settingsState.id, revision: settingsState.data.revision,
    values: settingsValues(["format", "base_url", "models_url", "api_key", "api_key_env", "anthropic_version"])};
  settingsState.busy = true;
  settingsState.models = [];
  show($("settings-model-picker"));
  $("settings-model-search").value = "";
  renderModelChoices();
  $("settings-model-status").textContent = "正在获取模型列表…";
  settingsButtons();
  try {
    const result = await settingsProbe("/api/settings/models", body);
    settingsState.models = result.models;
    renderModelChoices();
    $("settings-model-status").textContent = result.models.length
      ? `${result.models.length} 个模型${result.truncated ? "（列表未完全加载）" : ""}` : "接口返回空列表";
  } catch (e) {
    $("settings-model-status").textContent = e.message;
  } finally {
    settingsState.busy = false;
    settingsButtons();
  }
}
async function settingsProbe(path, body) {
  const started = await api(path, {...body, background: true});
  for (;;) {
    await new Promise((resolve) => setTimeout(resolve, 700));
    const operation = await api(`/api/operations/${started.id}`);
    if (operation.status === "running") continue;
    if (operation.status !== "succeeded") throw new Error(operation.error || "诊断超时或中断");
    const result = JSON.parse(operation.result);
    if (result.error) throw new Error(result.error);
    return result;
  }
}
async function settingsAction(action) {
  if (settingsState.busy || !settingsWritable()) return;
  if (action === "activate" && !confirm("将此配置设为当前配置？后续任务将使用新的模型或通知渠道。")) return;
  if (action === "test" && settingsState.kind === "feishu" && !confirm("向此配置的飞书机器人发送一条 CleverSlurm 测试消息？")) return;
  const body = {kind: settingsState.kind, id: settingsState.id, revision: settingsState.data.revision, action};
  if (action === "save") {
    body.name = $("settings-name").value;
    body.values = settingsValues();
  }
  if (action === "test") body.confirm_send = settingsState.kind === "feishu";
  settingsState.busy = true;
  settingsButtons();
  $("settings-result").textContent = action === "test" ? "正在测试已保存的配置…" : "正在保存…";
  try {
    const result = action === "test" ? await settingsProbe("/api/settings/test", body) : await api("/api/settings", body);
    if (action !== "test") {
      settingsState.data = result;
      settingsState.id = action === "activate" ? "current" : result.saved_id;
      renderSettings();
    }
    $("settings-result").textContent = result.message || (action === "activate" ? "已切换当前配置。" : "配置已保存。");
  } catch (e) { $("settings-result").textContent = e.message; }
  finally { settingsState.busy = false; settingsButtons(); }
}
$("settings-form").addEventListener("submit", (e) => { e.preventDefault(); settingsAction("save"); });
$("settings-activate").addEventListener("click", () => settingsAction("activate"));
$("settings-test").addEventListener("click", () => settingsAction("test"));
$("settings-copy").addEventListener("click", () => settingsAction("duplicate"));
