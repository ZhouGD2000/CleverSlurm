"use strict";

const settingsState = {data: null, kind: "ai", id: "current", dirty: false, busy: false};
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
  for (const tab of document.querySelectorAll("[data-settings-kind]")) tab.disabled = settingsState.busy;
}
function settingsDiscard() {
  return !settingsState.busy && (!settingsState.dirty || confirm("放弃未保存的配置修改？"));
}
function renderSettings() {
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
$("settings-form").addEventListener("input", () => {
  settingsState.dirty = true;
  settingsButtons();
});
async function settingsAction(action) {
  if (settingsState.busy || !settingsWritable()) return;
  if (action === "activate" && !confirm("将此配置设为当前配置？后续任务将使用新的模型或通知渠道。")) return;
  if (action === "test" && settingsState.kind === "feishu" && !confirm("向此配置的飞书机器人发送一条 CleverSlurm 测试消息？")) return;
  const body = {kind: settingsState.kind, id: settingsState.id, revision: settingsState.data.revision, action};
  if (action === "save") {
    body.name = $("settings-name").value;
    body.values = {};
    for (const f of settingsState.data.fields[settingsState.kind]) {
      const input = $("settings-fields").querySelector(`[name="${f.key}"]`);
      const clear = $("settings-fields").querySelector(`[data-clear-secret="${f.key}"]`);
      body.values[f.key] = f.secret ? (clear?.checked ? "" : input.value || null)
        : f.kind === "bool" ? String(input.checked) : input.value;
    }
  }
  if (action === "test") body.confirm_send = settingsState.kind === "feishu";
  settingsState.busy = true;
  settingsButtons();
  $("settings-result").textContent = action === "test" ? "正在测试已保存的配置…" : "正在保存…";
  try {
    const result = await api(action === "test" ? "/api/settings/test" : "/api/settings", body);
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
