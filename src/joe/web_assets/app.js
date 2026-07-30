const APP_VERSION = "0.28.8";
const state = {
  agents: new Map(),
  capabilities: {},
  usage: [],
  projects: [],
  activeProjectId: "main",
  conversations: [],
  activeConversationId: null,
  panels: new Map(),
  runs: new Map(),
  queues: new Map()
};
const $ = id => document.getElementById(id);
const renderMarkdown = window.JoeMarkdown.renderMarkdown;
const joeFetch = window.JoeAuth.authenticatedFetch;
const selectMenus = new Map();
let knownTasks = [];
let knownApprovals = [];
let knownFiles = [];
const selectedFileIds = new Set();
const notifiedTaskConflicts = new Set();
let language = window.JoeI18n.initialLanguage(
  window.localStorage,
  window.navigator.language
);
const t = key => window.JoeI18n.translate(language, key);

function applyLanguage(value) {
  language = window.JoeI18n.apply(document, value);
  window.localStorage.setItem("joe-language", language);
  for (const button of document.querySelectorAll("[data-language]")) {
    const active = button.dataset.language === language;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  }
  const running = state.activeConversationId
    && state.runs.has(state.activeConversationId);
  $("send").querySelector("span").textContent = t(running ? "queue" : "send");
  $("stop").querySelector("span").textContent = t("stop");
  if (!state.activeConversationId) {
    $("conversation-title").textContent = t("accomplish");
  }
  updateCapabilityMenus();
  for (const select of document.querySelectorAll("select")) {
    refreshSelectMenu(select);
  }
  if (knownTasks.length) renderTasks();
}

async function loadStatus() {
  const status = await fetch("/api/status").then(response => response.json());
  updateProviderMenu(status.provider_catalog || status.providers || []);
  annotateNetworkControl(status.network_control_providers);
  $("version").textContent = status.version || "ancienne version";
  if (status.version !== APP_VERSION) {
    const warning = $("restart-warning");
    warning.textContent = `Le serveur Joe ${status.version || "actuel"} utilise encore un ancien backend. Arrête-le avec Ctrl+C, relance joe, puis recharge cette page.`;
    warning.classList.remove("hidden");
  }
}

function annotateNetworkControl(controlled) {
  // Le réglage ne contraint que les fournisseurs qui exposent réellement un
  // commutateur réseau : on le dit, plutôt que de laisser croire à une
  // garantie globale.
  if (!Array.isArray(controlled)) return;
  const scope = controlled.length
    ? `Pour l’instant, ce contrôle est effectif uniquement sur ${controlled.map(capitalize).join(", ")} : les autres CLI n’exposent pas de réglage réseau.`
    : "Aucune CLI installée n’expose de réglage réseau : ce choix reste indicatif.";
  for (const id of ["tool-web-access-help", "project-remote-access-help"]) {
    const target = $(id);
    if (!target) continue;
    const prefix = id === "tool-web-access-help"
      ? "Disponible par défaut."
      : "Activé par défaut.";
    target.textContent = `${prefix} ${scope}`;
  }
}

function updateProviderMenu(providers) {
  const selected = $("agent").value;
  const items = providers.map(provider => (
    typeof provider === "string"
      ? { id: provider, label: capitalize(provider) }
      : provider
  ));
  setOptions(
    $("agent"),
    [{ id: "", label: t("automatic") }, ...items]
  );
  if ([...$("agent").options].some(option => option.value === selected)) {
    $("agent").value = selected;
  }
}

async function loadTasks() {
  const [tasksResponse, approvalsResponse] = await Promise.all([
    joeFetch("/api/tasks"),
    joeFetch("/api/approvals")
  ]);
  if (!tasksResponse.ok) return;
  knownTasks = await tasksResponse.json();
  knownApprovals = approvalsResponse.ok
    ? await approvalsResponse.json()
    : [];
  for (const task of knownTasks) {
    if (task.status === "conflict" && !notifiedTaskConflicts.has(task.id)) {
      notifiedTaskConflicts.add(task.id);
      notifyTaskConflict(task);
    }
  }
  renderTasks();
}

function notifyTaskConflict(task) {
  const toast = document.createElement("aside");
  toast.className = "task-toast";
  toast.innerHTML = `
    <div><strong>${escapeHtml(t("integration_failed"))}</strong>
    <span>${escapeHtml(task.title)}</span></div>
    <div class="task-toast-actions"></div>`;
  const actions = toast.querySelector(".task-toast-actions");
  actions.appendChild(taskAction(t("view_diff"), () => showTaskDiff(task)));
  const close = taskAction("×", () => toast.remove());
  close.setAttribute("aria-label", t("close"));
  actions.appendChild(close);
  $("toast-region").appendChild(toast);
}

function renderTasks() {
  const target = $("tasks");
  target.replaceChildren();
  const filter = $("task-filter").value;
  const tasks = knownTasks.filter(task => {
    if (filter === "all") return true;
    if (filter === "attention") return ["review", "conflict", "failed"].includes(task.status);
    return ["running", "integrating", "resolving", "review", "conflict"].includes(task.status);
  }).slice(0, 12);
  $("task-count").textContent = String(tasks.length + knownApprovals.length);
  for (const approval of knownApprovals) {
    target.appendChild(renderApproval(approval));
  }
  if (!tasks.length && !knownApprovals.length) {
    const empty = document.createElement("span");
    empty.className = "task-empty";
    empty.textContent = t("no_tasks");
    target.appendChild(empty);
    return;
  }
  const statusLabels = {
    running: t("running"),
    review: t("review_task"),
    completed: t("done"),
    integrated: t("integrated"),
    integrating: t("integrating"),
    resolving: t("resolving"),
    conflict: t("conflict"),
    failed: t("failed"),
    cancelled: t("cancelled")
  };
  for (const task of tasks) {
    const card = document.createElement("article");
    card.className = `task-card ${task.status}`;
    card.tabIndex = 0;
    card.setAttribute("role", "button");
    card.title = "Ouvrir le prompt de cette tâche";
    const openConversation = () => selectConversation(task.conversation_id, task.id);
    card.onclick = event => {
      if (!event.target.closest("button")) openConversation();
    };
    card.onkeydown = event => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      openConversation();
    };
    const branch = task.branch
      ? `<span title="${escapeHtml(task.branch)}">${escapeHtml(task.branch)}</span>`
      : `<span>${escapeHtml(t("current_workspace"))}</span>`;
    card.innerHTML = `
      <div class="task-card-head">
        <strong title="${escapeHtml(task.request)}">${escapeHtml(task.title)}</strong>
        <b>${escapeHtml(statusLabels[task.status] || task.status)}</b>
      </div>
      <div class="task-meta">${branch}<span>${Number(task.files || 0)} fichier${Number(task.files || 0) === 1 ? "" : "s"} · +${Number(task.insertions || 0)} −${Number(task.deletions || 0)}</span></div>
      <div class="task-pipeline">${(task.pipeline || []).map(stage => (
        `<span class="${escapeHtml(stage.status)}"><i></i>${escapeHtml(stage.label)}</span>`
      )).join("")}</div>
      ${task.error ? `<p class="task-error">${escapeHtml(task.error)}</p>` : ""}
      <div class="task-actions"></div>`;
    const actions = card.querySelector(".task-actions");
    actions.appendChild(taskAction("Conversation", openConversation));
    if (task.isolated && task.status !== "integrated") {
      actions.appendChild(taskAction(t("view_diff"), () => showTaskDiff(task)));
      if (["review", "conflict"].includes(task.status)) {
        actions.appendChild(taskAction(t("integrate"), () => integrateTask(task), "primary"));
      }
      if (!["running", "integrating", "resolving", "integrated"].includes(task.status)) {
        actions.appendChild(taskAction(t("delete"), () => deleteTask(task), "danger"));
      }
    }
    if (!actions.children.length) actions.remove();
    target.appendChild(card);
  }
}

function renderApproval(approval) {
  const card = document.createElement("article");
  card.className = "task-card approval";
  card.innerHTML = `
    <div class="task-card-head"><strong>Autorisation demandée</strong><b>À valider</b></div>
    <p class="task-error">${escapeHtml(approval.message)}</p>
    <div class="task-meta"><span>${escapeHtml(approval.payload?.request || "")}</span></div>
    <div class="task-actions"></div>`;
  const actions = card.querySelector(".task-actions");
  actions.append(
    taskAction("Refuser", () => decideApproval(approval, "refused"), "danger"),
    taskAction("Autoriser", () => decideApproval(approval, "approved"), "primary")
  );
  return card;
}

async function decideApproval(approval, decision) {
  const response = await joeFetch(
    `/api/approvals/${encodeURIComponent(approval.id)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision })
    }
  );
  if (!response.ok) return;
  if (decision === "approved") {
    const payload = approval.payload || {};
    await startRun(
      payload.request,
      payload.conversation_id,
      { ...payload, approval_id: approval.id }
    );
  }
  await loadTasks();
}

function taskAction(label, action, kind = "") {
  const button = document.createElement("button");
  button.type = "button";
  button.className = kind;
  button.textContent = label;
  button.onclick = event => {
    event.stopPropagation();
    action();
  };
  return button;
}

window.addEventListener("joe:open-task", async event => {
  const taskId = event.detail?.taskId;
  const task = knownTasks.find(item => item.id === taskId);
  if (task) return showTaskDiff(task);
  await loadTasks();
  const refreshed = knownTasks.find(item => item.id === taskId);
  if (refreshed) await showTaskDiff(refreshed);
});

async function showTaskDiff(task) {
  const response = await joeFetch(`/api/tasks/${encodeURIComponent(task.id)}/diff`);
  const report = await response.json();
  if (!response.ok) {
    window.alert(report.error || "Diff indisponible.");
    return;
  }
  $("task-diff-title").textContent = task.title;
  $("task-diff-stats").textContent = `${report.files.length} fichier${report.files.length === 1 ? "" : "s"} · +${report.insertions} −${report.deletions}`;
  $("task-diff-files").innerHTML = report.files.map(file => (
    `<span><b>${escapeHtml(file.path)}</b><small>+${file.insertions} −${file.deletions}</small></span>`
  )).join("") || "<small>Aucune modification.</small>";
  $("task-diff-preview").textContent = report.patch_preview || "Aucun diff textuel disponible.";
  $("task-diff-dialog").showModal();
}

async function integrateTask(task) {
  if (!window.confirm(`Intégrer la branche ${task.branch} dans le dépôt principal ?`)) return;
  const response = await joeFetch(`/api/tasks/${encodeURIComponent(task.id)}/integrate`, {
    method: "POST"
  });
  const payload = await response.json();
  if (!response.ok) {
    window.alert(payload.error || "Intégration impossible.");
    return;
  }
  await loadTasks();
}

async function deleteTask(task) {
  if (!window.confirm(
    `Supprimer la tâche et abandonner les modifications de ${task.branch} ?`
  )) return;
  const response = await joeFetch(`/api/tasks/${encodeURIComponent(task.id)}`, {
    method: "DELETE"
  });
  const payload = await response.json();
  if (!response.ok) {
    window.alert(payload.error || "Suppression impossible.");
    return;
  }
  await loadTasks();
}

function activeProjectId() {
  const conversation = state.conversations.find(
    item => item.id === state.activeConversationId
  );
  return conversation?.project_id || state.activeProjectId || "free";
}

async function loadFiles() {
  const response = await joeFetch(
    `/api/files?project=${encodeURIComponent(activeProjectId())}`
  );
  if (!response.ok) return;
  knownFiles = await response.json();
  for (const id of [...selectedFileIds]) {
    if (!knownFiles.some(item => item.id === id)) selectedFileIds.delete(id);
  }
  renderFileLibrary();
  renderAttachmentChips();
}

function renderFileLibrary() {
  const target = $("file-library");
  target.replaceChildren();
  if (!knownFiles.length) {
    target.innerHTML = "<small>Aucun fichier dans ce projet.</small>";
    return;
  }
  for (const file of knownFiles) {
    const row = document.createElement("div");
    row.className = "file-row";
    const select = document.createElement("button");
    select.type = "button";
    select.className = selectedFileIds.has(file.id) ? "selected" : "";
    select.textContent = file.name;
    select.title = `${file.name} · ${formatBytes(file.size)}`;
    select.onclick = () => {
      if (selectedFileIds.has(file.id)) selectedFileIds.delete(file.id);
      else selectedFileIds.add(file.id);
      renderFileLibrary();
      renderAttachmentChips();
    };
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "file-delete";
    remove.textContent = "×";
    remove.title = "Supprimer ce fichier";
    remove.onclick = async () => {
      const response = await joeFetch(
        `/api/files/${encodeURIComponent(file.id)}?project=${encodeURIComponent(file.project_id)}`,
        { method: "DELETE" }
      );
      if (response.ok) await loadFiles();
    };
    const download = document.createElement("button");
    download.type = "button";
    download.className = "file-download";
    download.textContent = "↗";
    download.title = "Ouvrir ou télécharger";
    download.onclick = () => window.open(
      `/api/files/${encodeURIComponent(file.id)}/download?project=${encodeURIComponent(file.project_id)}`,
      "_blank",
      "noopener"
    );
    row.append(select, download, remove);
    target.appendChild(row);
  }
}

function renderAttachmentChips() {
  const target = $("attachment-chips");
  target.replaceChildren();
  for (const id of selectedFileIds) {
    const file = knownFiles.find(item => item.id === id);
    if (!file) continue;
    const chip = document.createElement("button");
    chip.type = "button";
    chip.title = "Retirer de cette demande";
    chip.innerHTML = `<span>${escapeHtml(file.name)}</span><b>×</b>`;
    chip.onclick = () => {
      selectedFileIds.delete(id);
      renderAttachmentChips();
      renderFileLibrary();
    };
    target.appendChild(chip);
  }
  target.classList.toggle("hidden", !target.children.length);
}

async function uploadFiles(fileList) {
  const projectId = activeProjectId();
  for (const file of fileList) {
    const dataUrl = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(file);
    });
    const response = await joeFetch("/api/files", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        project_id: projectId,
        name: file.name,
        content_type: file.type,
        data: dataUrl.split(",", 2)[1] || ""
      })
    });
    const payload = await response.json();
    if (!response.ok) {
      window.alert(payload.error || `Impossible d’ajouter ${file.name}.`);
      continue;
    }
    selectedFileIds.add(payload.id);
  }
  await loadFiles();
}

function formatBytes(value) {
  const size = Number(value || 0);
  if (size < 1024) return `${size} o`;
  return `${(size / 1024).toFixed(size < 10240 ? 1 : 0)} Kio`;
}

async function loadDoctor() {
  if (window.localStorage.getItem("joe-onboarded-v1")) return;
  const dialog = $("onboarding-dialog");
  dialog.showModal();
  const response = await joeFetch("/api/doctor");
  if (!response.ok) {
    $("doctor-status").innerHTML = "<p>Diagnostic indisponible.</p>";
    return;
  }
  const report = await response.json();
  $("doctor-status").innerHTML = `
    <p class="${report.storage?.writable ? "ok" : "error"}">
      <b>Stockage</b><span>${report.storage?.writable ? "Prêt" : "À corriger"}</span>
    </p>
    ${(report.providers || []).map(provider => `
      <p class="${provider.installed ? "ok" : "muted"}">
        <b>${escapeHtml(capitalize(provider.provider))}</b>
        <span>${provider.installed ? escapeHtml(provider.version || "Installé") : "Non détecté"}</span>
      </p>`).join("")}`;
}

function setupSelectMenu(select) {
  if (selectMenus.has(select)) return;
  const wrapper = document.createElement("div");
  wrapper.className = "select-menu";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "select-menu-trigger";
  button.setAttribute("aria-haspopup", "listbox");
  button.setAttribute("aria-expanded", "false");
  const menu = document.createElement("div");
  menu.className = "select-menu-options";
  menu.setAttribute("role", "listbox");
  select.before(wrapper);
  wrapper.append(select, button, menu);
  select.classList.add("native-select");

  const close = () => {
    wrapper.classList.remove("open");
    wrapper.classList.remove("open-up");
    button.setAttribute("aria-expanded", "false");
  };
  button.onclick = event => {
    event.preventDefault();
    event.stopPropagation();
    const opening = !wrapper.classList.contains("open");
    closeSelectMenus();
    if (opening) {
      wrapper.classList.add("open");
      const boundary = wrapper.closest("dialog")?.getBoundingClientRect()
        || { top: 8, bottom: window.innerHeight - 8 };
      const triggerBounds = button.getBoundingClientRect();
      const menuHeight = menu.getBoundingClientRect().height;
      const spaceBelow = boundary.bottom - triggerBounds.bottom;
      const spaceAbove = triggerBounds.top - boundary.top;
      wrapper.classList.toggle(
        "open-up",
        spaceBelow < menuHeight + 10 && spaceAbove > spaceBelow,
      );
      button.setAttribute("aria-expanded", "true");
    }
  };
  button.onkeydown = event => {
    if (!["ArrowDown", "ArrowUp", "Escape"].includes(event.key)) return;
    event.preventDefault();
    if (event.key === "Escape") return close();
    const options = [...select.options];
    const direction = event.key === "ArrowDown" ? 1 : -1;
    const index = Math.max(0, options.findIndex(option => option.value === select.value));
    const next = options[(index + direction + options.length) % options.length];
    select.value = next.value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
    refreshSelectMenu(select);
  };
  selectMenus.set(select, { wrapper, button, menu, close });
  refreshSelectMenu(select);
}

function refreshSelectMenu(select) {
  const control = selectMenus.get(select);
  if (!control) return;
  control.button.disabled = select.disabled;
  const selected = select.selectedOptions[0] || select.options[0];
  control.button.textContent = selected?.textContent || "";
  control.menu.replaceChildren();
  for (const option of select.options) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "select-menu-option";
    item.setAttribute("role", "option");
    item.setAttribute("aria-selected", String(option.value === select.value));
    item.textContent = option.textContent;
    item.onclick = event => {
      event.preventDefault();
      event.stopPropagation();
      select.value = option.value;
      select.dispatchEvent(new Event("change", { bubbles: true }));
      refreshSelectMenu(select);
      control.close();
      control.button.focus();
    };
    control.menu.appendChild(item);
  }
}

function closeSelectMenus() {
  for (const control of selectMenus.values()) control.close();
}

async function loadCapabilities() {
  state.capabilities = await joeFetch("/api/capabilities").then(response => response.json());
  updateCapabilityMenus();
}

let loadUsage;
let showQuotaNotice;
let updateCountdowns;

function shortCommit(value) {
  return value ? value.slice(0, 8) : "indisponible";
}

function renderGitReport(report, runId) {
  if (!report?.available) return;
  const headChanged = report.head_before !== report.head_after;
  const devChanged = report.origin_dev_before !== report.origin_dev_after;
  const hasActivity = report.files.length || headChanged || devChanged || report.fetch_observed;
  if (!hasActivity) return;
  const viewport = document.querySelector(".conversation");
  const follow = shouldFollow(viewport);
  const card = document.createElement("section");
  card.className = "git-report";
  const integration = report.origin_dev_integrated === null
    ? "état de dev inconnu"
    : report.origin_dev_integrated
      ? "origin/dev est intégré"
      : "origin/dev n’est pas intégré";
  card.innerHTML = `
    <header>
      <div><span class="eyebrow">Modifications du dépôt</span><div class="diff-summary"><strong>${report.files.length} fichier${report.files.length > 1 ? "s" : ""}</strong><span class="insertions">+${report.insertions}</span><span class="deletions">−${report.deletions}</span></div></div>
      <span class="change-status">${report.delivery?.status === "pushed" ? "Commit et push effectués" : report.delivery?.status === "committed" ? "Commit effectué" : "Conservées"}</span>
    </header>`;
  const details = document.createElement("details");
  details.className = "git-details";
  details.innerHTML = `
    <summary>Voir les détails</summary>
    <div class="git-facts">
      <span>Branche <b>${escapeHtml(report.branch || "HEAD détachée")}</b></span>
      <span>HEAD <b>${shortCommit(report.head_before)} → ${shortCommit(report.head_after)}</b>${headChanged ? " · modifié" : " · inchangé"}</span>
      <span>origin/dev <b>${shortCommit(report.origin_dev_before)} → ${shortCommit(report.origin_dev_after)}</b>${devChanged ? " · référence actualisée" : " · inchangé"} · ${report.fetch_observed ? "fetch observé" : "aucun fetch observé"}</span>
      <span>${integration}</span>
      ${report.delivery ? `<span>Livraison <b>${escapeHtml(report.delivery.message || report.delivery.status)}</b>${report.delivery.commit ? ` · ${shortCommit(report.delivery.commit)}` : ""}</span>` : ""}
    </div>`;
  if (report.files.length) {
    const hint = document.createElement("p");
    hint.className = "git-selection-hint";
    hint.textContent = report.rejectable
      ? "Coche les fichiers que tu souhaites rejeter."
      : "Modifications conservées automatiquement.";
    details.appendChild(hint);
    const list = document.createElement("div");
    list.className = "diff-files";
    for (const file of report.files) {
      const row = document.createElement("label");
      row.innerHTML = `${report.rejectable ? `<input type="checkbox" value="${escapeHtml(file.path)}">` : ""}<code>${escapeHtml(file.path)}</code><span class="insertions">+${file.insertions}</span><span class="deletions">−${file.deletions}</span>${file.preexisting ? '<small title="Déjà modifié avant la tâche">préexistant</small>' : ""}`;
      list.appendChild(row);
    }
    details.appendChild(list);
  }
  if (report.patch_preview) {
    const preview = document.createElement("details");
    preview.className = "diff-preview";
    const summaryNode = document.createElement("summary");
    summaryNode.textContent = "Voir le diff complet";
    const patch = document.createElement("pre");
    patch.textContent = report.patch_preview;
    preview.append(summaryNode, patch);
    details.appendChild(preview);
  }
  const actions = document.createElement("div");
  actions.className = "git-actions";
  const keep = document.createElement("button");
  keep.textContent = "Conserver tout";
  keep.onclick = () => {
    keep.disabled = true;
    keep.textContent = "Tout est conservé";
    details.open = false;
  };
  const rejectButton = document.createElement("button");
  rejectButton.className = "reject-changes";
  rejectButton.textContent = "Rejeter la sélection";
  rejectButton.disabled = !report.rejectable || !runId;
  rejectButton.title = report.rejectable
    ? "Restaurer les fichiers sélectionnés à leur état précédent"
    : report.reject_reason || "Restauration automatique indisponible";
  rejectButton.onclick = async () => {
    const files = [...details.querySelectorAll('.diff-files input:checked')].map((input) => input.value);
    if (!files.length) {
      window.alert("Sélectionne au moins un fichier à rejeter.");
      return;
    }
    if (!window.confirm(`Rejeter ${files.length} fichier${files.length > 1 ? "s" : ""} sélectionné${files.length > 1 ? "s" : ""} ?`)) return;
    const response = await joeFetch(`/api/runs/${runId}/reject`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ files }),
    });
    const payload = await response.json();
    if (!response.ok) {
      window.alert(payload.message);
      return;
    }
    rejectButton.disabled = true;
    keep.disabled = true;
    card.querySelector(".change-status").textContent = "Sélection rejetée";
  };
  actions.append(keep, rejectButton);
  if (!report.rejectable && report.files.length) {
    const reason = document.createElement("small");
    reason.textContent = report.reject_reason;
    actions.appendChild(reason);
  }
  details.appendChild(actions);
  card.appendChild(details);
  $("messages").appendChild(card);
  scrollIfFollowing(viewport, follow);
}

function updateCapabilityMenus() {
  const provider = $("agent").value;
  const capability = state.capabilities[provider];
  setOptions($("model"), [{ id: "", label: t("provider_default") }]);
  setOptions($("effort"), [{ id: "", label: t("model_default") }]);
  setOptions($("execution-mode"), [{ id: "", label: t("automatic") }]);
  if (!capability) {
    for (const control of [$("model"), $("effort"), $("execution-mode")]) {
      control.disabled = true;
      refreshSelectMenu(control);
    }
    return;
  }
  $("model").disabled = false;
  $("execution-mode").disabled = false;
  addOptions($("model"), capability.models || []);
  addOptions($("model"), [{ id: "__custom__", label: "Autre identifiant…" }]);
  addOptions($("execution-mode"), (capability.execution_modes || []).filter(item => item.id !== "auto"));
  updateEfforts();
}

function updateEfforts() {
  const provider = $("agent").value;
  const capability = state.capabilities[provider] || {};
  const selectedModel = (capability.models || []).find(model => model.id === $("model").value);
  const efforts = selectedModel?.efforts || capability.efforts || [];
  setOptions($("effort"), [{ id: "", label: selectedModel?.default_effort ? `${t("model_default")} · ${selectedModel.default_effort}` : t("model_default") }]);
  addOptions($("effort"), efforts.map(value => ({ id: value, label: value })));
  $("effort").disabled = !efforts.length;
  refreshSelectMenu($("effort"));
}

function currentSettings() {
  return {
    agent: $("agent").value,
    mode: $("mode").value,
    model: $("model").value,
    effort: $("effort").value,
    execution_mode: $("execution-mode").value,
    web_access: $("tool-web-access").checked ? "on" : "off"
  };
}

function applySettings(settings) {
  $("agent").value = settings.agent || "";
  updateCapabilityMenus();
  if (settings.model && [...$("model").options].some(option => option.value === settings.model)) {
    $("model").value = settings.model;
  }
  updateEfforts();
  $("mode").value = settings.mode || "";
  $("effort").value = settings.effort || "";
  $("execution-mode").value = settings.execution_mode || "";
  $("tool-web-access").checked = settings.web_access !== "off";
  for (const select of [
    $("agent"), $("mode"), $("model"), $("effort"), $("execution-mode")
  ]) {
    refreshSelectMenu(select);
  }
}

async function saveSettings() {
  if (!state.activeConversationId) return;
  await joeFetch(`/api/conversations/${state.activeConversationId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ settings: currentSettings() })
  });
}

async function openPreferences() {
  const preferences = await joeFetch("/api/preferences").then(
    response => response.json()
  );
  $("preference-agent").value = preferences.agent || "";
  $("preference-mode").value = preferences.mode || "";
  refreshSelectMenu($("preference-agent"));
  refreshSelectMenu($("preference-mode"));
  $("preferences-dialog").showModal();
}

async function savePreferences(event) {
  event.preventDefault();
  const response = await joeFetch("/api/preferences", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      agent: $("preference-agent").value,
      mode: $("preference-mode").value
    })
  });
  if (!response.ok) {
    const payload = await response.json();
    window.alert(payload.error || response.statusText);
    return;
  }
  $("preferences-dialog").close();
}

function setOptions(select, items) {
  select.replaceChildren();
  addOptions(select, items);
  refreshSelectMenu(select);
}

function addOptions(select, items) {
  for (const item of items) {
    const option = document.createElement("option");
    option.value = item.id;
    option.textContent = item.label || item.id;
    select.appendChild(option);
  }
}

let createConversation;
let createProject;
let deleteConversation;
let loadConversations;
let moveConversation;
let openProject;
let renameConversation;
let saveProject;
let selectConversation;
let togglePin;
let confirmDeleteConversation;

function clearConversation() { $("messages").replaceChildren(); }

function addMessage(label, text, kind) {
  const viewport = document.querySelector(".conversation");
  const follow = shouldFollow(viewport);
  const wrapper = document.createElement("div");
  wrapper.className = `message ${kind}`;
  const title = document.createElement("div");
  title.className = "message-label";
  const titleText = document.createElement("span");
  titleText.textContent = label;
  const copy = copyButton(() => bubble.dataset.source || bubble.textContent);
  title.append(titleText, copy);
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  bubble.dataset.source = text;
  wrapper.append(title, bubble);
  $("messages").appendChild(wrapper);
  scrollIfFollowing(viewport, follow);
  return bubble;
}

function copyButton(getText) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "copy-button";
  button.textContent = "⧉ Copier";
  button.title = "Copier dans le presse-papiers";
  button.setAttribute("aria-label", "Copier dans le presse-papiers");
  button.onclick = async event => {
    event.preventDefault();
    event.stopPropagation();
    const text = String(getText() || "");
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const area = document.createElement("textarea");
      area.value = text;
      document.body.appendChild(area);
      area.select();
      document.execCommand("copy");
      area.remove();
    }
    button.textContent = "✓ Copié";
    button.classList.add("copied");
    setTimeout(() => {
      button.textContent = "⧉ Copier";
      button.classList.remove("copied");
    }, 1200);
  };
  return button;
}

function showRoute(mode, primary, reviewer) {
  const badge = $("route-badge");
  badge.textContent = `${mode?.toUpperCase()} · ${primary}${reviewer ? ` → ${reviewer}` : ""}`;
  badge.classList.remove("hidden");
}

function ensureAgent(name) {
  if (state.agents.has(name)) return state.agents.get(name);
  const card = document.createElement("div");
  card.className = "agent-card";
  card.innerHTML = `<div class="agent-head"><span class="agent-name">${escapeHtml(name)}</span><span class="agent-meta hidden"></span><span class="agent-status">En attente</span></div><p class="agent-heartbeat hidden"></p><div class="agent-activity"></div><pre class="agent-output"></pre>`;
  $("agents").appendChild(card);
  const agent = {
    card,
    status: card.querySelector(".agent-status"),
    meta: card.querySelector(".agent-meta"),
    heartbeat: card.querySelector(".agent-heartbeat"),
    activity: card.querySelector(".agent-activity"),
    output: card.querySelector(".agent-output"),
    model: "",
    effort: ""
  };
  state.agents.set(name, agent);
  return agent;
}

function setAgentMeta(agent, { model, effort } = {}) {
  if (model !== undefined) agent.model = model;
  if (effort !== undefined) agent.effort = effort;
  const parts = [agent.model, agent.effort ? `effort ${agent.effort}` : ""].filter(Boolean);
  agent.meta.textContent = parts.join(" · ");
  agent.meta.classList.toggle("hidden", parts.length === 0);
  return parts.join(" · ");
}

function renderWorkflowUpdate(event, finalBubble, runId) {
  if (!finalBubble) return;
  const message = finalBubble.closest(".message");
  let progress = document.querySelector(`.workflow-progress[data-run="${runId}"]`);
  if (!progress) {
    progress = document.createElement("section");
    progress.className = "workflow-progress";
    progress.dataset.run = runId;
    const labels = workflowLabels(event.mode);
    progress.innerHTML = `<header><span class="eyebrow">${labels.running}</span><strong>${labels.detail}</strong></header><div class="workflow-stages"></div>`;
    message.before(progress);
  }
  const stages = progress.querySelector(".workflow-stages");
  let stage = stages.querySelector(`[data-stage="${event.stage}"]`);
  if (!stage) {
    stage = document.createElement("details");
    stage.className = "workflow-stage";
    stage.dataset.stage = event.stage;
    stages.appendChild(stage);
  }
  const complete = event.status === "complete";
  const failed = event.status === "failed";
  stage.classList.toggle("complete", complete);
  stage.classList.toggle("failed", failed);
  stage.classList.toggle("running", !complete && !failed);
  const providerDetails = [
    capitalize(event.provider),
    event.model || "",
    event.effort ? `effort ${event.effort}` : "",
    event.fallback_from ? `relais de ${capitalize(event.fallback_from)}` : ""
  ].filter(Boolean).join(" · ");
  stage.innerHTML = `<summary><span>${escapeHtml(event.label)}</span><b>${escapeHtml(providerDetails)} · ${complete ? "terminé" : failed ? "échec" : "en cours"}</b></summary><div class="workflow-opinion"></div>`;
  if ((complete || failed) && event.content) {
    renderMarkdown(stage.querySelector(".workflow-opinion"), event.content);
    stage.querySelector("summary").appendChild(
      copyButton(() => event.content)
    );
  }
}

function workflowLabels(mode) {
  if (mode === "consensus") {
    return {
      running: "Consensus en cours",
      complete: "Consensus terminé",
      detail: "Avis et examens croisés"
    };
  }
  if (mode === "review") {
    return {
      running: "Implémentation contrôlée",
      complete: "Implémentation contrôlée terminée",
      detail: "Réalisation, revue et correction"
    };
  }
  return {
    running: "Exécution en cours",
    complete: "Exécution terminée",
    detail: "Traitement par un agent"
  };
}

function finishWorkflowProgress(runId, mode) {
  const progress = document.querySelector(
    `.workflow-progress[data-run="${runId || ""}"]`
  );
  const eyebrow = progress?.querySelector("header .eyebrow");
  if (eyebrow) eyebrow.textContent = workflowLabels(mode).complete;
}

function failRunningWorkflow() {
  for (const stage of document.querySelectorAll(".workflow-stage.running")) {
    stage.classList.remove("running");
    stage.classList.add("failed");
    const status = stage.querySelector("summary b");
    if (status) status.textContent = "Échec";
  }
}

function setSummaryPending(finalBubble, pending) {
  finalBubble?.closest(".message")?.classList.toggle(
    "workflow-summary-pending",
    pending
  );
}

function renderHistoricalRunSummary(message, finalBubble) {
  const summary = message.run_summary;
  if (!summary?.workflow?.length) return;
  for (const event of summary.workflow) {
    renderWorkflowUpdate(event, finalBubble, message.run_id || "");
  }
  const progress = document.querySelector(
    `.workflow-progress[data-run="${message.run_id || ""}"]`
  );
  const eyebrow = progress?.querySelector("header .eyebrow");
  if (eyebrow) {
    eyebrow.textContent = workflowLabels(summary.route?.mode).complete;
  }
}

function updateWorkflowProviderMetadata(provider, metadata) {
  for (const stage of document.querySelectorAll(".workflow-stage.running")) {
    const status = stage.querySelector("summary b");
    if (status?.textContent.toLowerCase().startsWith(provider)) {
      const relay = stage.dataset.fallbackFrom
        ? ` · relais de ${capitalize(stage.dataset.fallbackFrom)}`
        : "";
      status.textContent = `${capitalize(provider)} · ${metadata}${relay} · en cours`;
    }
  }
}

function updateWorkflowFallback(provider, fallback) {
  for (const stage of document.querySelectorAll(".workflow-stage.running")) {
    const status = stage.querySelector("summary b");
    if (!status?.textContent.toLowerCase().startsWith(provider)) continue;
    stage.dataset.fallbackFrom = provider;
    status.textContent = `${capitalize(fallback)} · relais de ${capitalize(provider)} · en cours`;
  }
}

({
  loadUsage,
  showQuotaNotice,
  updateCountdowns
} = window.createJoeUsage({
  state,
  $,
  escapeHtml,
  capitalize,
  addMessage,
  fetcher: joeFetch
}));

({
  createConversation,
  createProject,
  deleteConversation,
  loadConversations,
  moveConversation,
  openProject,
  renameConversation,
  saveProject,
  selectConversation,
  togglePin,
  confirmDeleteConversation
} = window.createJoeConversations({
  state,
  $,
  escapeHtml,
  closeMobilePanels,
  clearConversation,
  addMessage,
  renderMarkdown,
  renderGitReport,
  renderHistoricalRunSummary,
  applySettings,
  refreshSelectMenu,
  renderWorkflowUpdate,
  renderPromptQueue,
  fetcher: joeFetch
}));

function handleEvent(conversationId, event, finalBubble) {
  const activeRun = state.runs.get(conversationId);
  if (event.type === "route" && activeRun) {
    activeRun.mode = event.mode;
    activeRun.intent = event.intent;
  }
  if (event.type === "workflow_update" && activeRun) {
    event = {
      ...(activeRun.workflow.get(event.stage) || {}),
      ...event
    };
    activeRun.workflow.set(event.stage, event);
  }
  if (event.type === "provider_fallback" && activeRun) {
    for (const [stage, workflowEvent] of activeRun.workflow) {
      if (
        workflowEvent.status === "running"
        && workflowEvent.provider === event.provider
      ) {
        activeRun.workflow.set(stage, {
          ...workflowEvent,
          provider: event.fallback,
          fallback_from: event.provider
        });
      }
    }
  }
  if (event.type === "provider_start" && activeRun) {
    for (const [stage, workflowEvent] of activeRun.workflow) {
      if (
        workflowEvent.status === "running"
        && workflowEvent.provider === event.provider
      ) {
        activeRun.workflow.set(stage, {
          ...workflowEvent,
          model: event.model,
          effort: event.effort
        });
      }
    }
  }
  if (conversationId !== state.activeConversationId) {
    const panel = state.panels.get(conversationId);
    if (panel) {
      panel.rawLog += `${JSON.stringify(event)}\n`;
      if (event.type === "complete" || event.type === "error" || event.type === "cancelled") {
        for (const node of panel.agentNodes) {
          node.classList.remove("active");
          const status = node.querySelector(".agent-status");
          if (status) status.textContent = event.type === "complete" ? "Terminé" : "Interrompu";
        }
        panel.runState = event.type === "complete" ? "Terminé" : "Échec";
        panel.runStateClass = `run-state ${event.type === "complete" ? "done" : "idle"}`;
      }
    }
    if (event.type === "complete" || event.type === "error" || event.type === "cancelled") {
      state.runs.delete(conversationId);
      loadConversations(false);
      loadTasks().catch(() => {});
      launchNextQueued(conversationId);
    }
    return;
  }
  finalBubble = state.runs.get(conversationId)?.bubble || finalBubble;
  const structuredWorkflow = ["consensus", "review"].includes(activeRun?.mode);
  const conversationViewport = document.querySelector(".conversation");
  const followConversation = shouldFollow(conversationViewport);
  const diagnostics = $("raw-log");
  const followDiagnostics = shouldFollow(diagnostics);
  $("raw-log").textContent += `${JSON.stringify(event)}\n`;
  scrollIfFollowing(diagnostics, followDiagnostics);
  if (event.type === "route") {
    showRoute(event.mode, event.primary, event.reviewer);
    loadTasks().catch(() => {});
    ensureAgent(event.primary);
    if (event.reviewer) ensureAgent(event.reviewer);
    setSummaryPending(finalBubble, true);
    finalBubble.textContent = "Synthèse finale en attente…";
    if (event.mode === "fast") {
      const label = event.intent === "modify"
        ? "Implémentation"
        : event.intent === "analyze" ? "Analyse" : "Réponse";
      renderWorkflowUpdate({
        mode: event.mode,
        stage: "primary",
        provider: event.primary,
        label,
        status: "running"
      }, finalBubble, activeRun?.runId || "");
    }
  } else if (event.type === "provider_start") {
    const agent = ensureAgent(event.provider);
    agent.card.classList.add("active");
    agent.status.textContent = "En cours";
    agent.heartbeat.textContent = "";
    agent.heartbeat.classList.add("hidden");
    const metadata = setAgentMeta(agent, {
      model: event.model || "modèle par défaut",
      effort: event.effort || "défaut"
    });
    updateWorkflowProviderMetadata(event.provider, metadata);
  } else if (event.type === "activity") {
    const agent = ensureAgent(event.provider);
    if (event.kind === "heartbeat") {
      agent.heartbeat.textContent = event.detail || event.label;
      agent.heartbeat.classList.remove("hidden");
    } else if (event.kind === "model") {
      const metadata = setAgentMeta(agent, { model: event.label });
      updateWorkflowProviderMetadata(event.provider, metadata);
    } else {
      const signature = `${event.label}\n${event.detail || ""}`;
      const previous = agent.activity.lastElementChild;
      if (previous?.dataset.signature !== signature) {
        const row = document.createElement("div");
        row.className = "activity-row";
        row.dataset.signature = signature;
        row.innerHTML = `<i></i><div><strong>${escapeHtml(event.label)}</strong>${event.detail ? `<span>${escapeHtml(event.detail)}</span>` : ""}</div>`;
        const followActivity = shouldFollow(agent.activity);
        agent.activity.appendChild(row);
        while (agent.activity.children.length > 12) agent.activity.firstElementChild.remove();
        scrollIfFollowing(agent.activity, followActivity);
      }
    }
  } else if (event.type === "stream") {
    const agent = ensureAgent(event.provider);
    const followOutput = shouldFollow(agent.output);
    agent.output.textContent += event.text;
    scrollIfFollowing(agent.output, followOutput);
  } else if (event.type === "workflow_update") {
    renderWorkflowUpdate(event, finalBubble, activeRun?.runId || "");
  } else if (event.type === "provider_end") {
    const agent = ensureAgent(event.provider);
    agent.card.classList.remove("active");
    agent.heartbeat.classList.add("hidden");
    agent.status.textContent = event.ok ? "Terminé" : `Échec · ${event.error || "inconnu"}`;
    if (!structuredWorkflow && event.ok) {
      renderWorkflowUpdate({
        mode: activeRun?.mode || "fast",
        stage: "primary",
        provider: event.provider,
        label: activeRun?.intent === "modify" ? "Implémentation" : "Traitement",
        status: "complete"
      }, finalBubble, activeRun?.runId || "");
    }
  } else if (event.type === "provider_fallback") {
    const agent = ensureAgent(event.provider);
    agent.card.classList.remove("active");
    agent.heartbeat.classList.add("hidden");
    agent.status.textContent = event.error === "quota"
      ? `Quota épuisé · relais ${capitalize(event.fallback)}`
      : `Indisponible · relais ${capitalize(event.fallback)}`;
    const fallback = ensureAgent(event.fallback);
    fallback.status.textContent = `Relais de ${capitalize(event.provider)}`;
    updateWorkflowFallback(event.provider, event.fallback);
  } else if (event.type === "quota_admission") {
    finalBubble.textContent += `\n${event.message}`;
  } else if (event.type === "evidence") {
    const row = document.createElement("div");
    row.className = `evidence-row ${event.status}`;
    const labels = { verified: "Vérifié", inferred: "Inféré", refused: "Refusé" };
    row.innerHTML = `<b>${labels[event.status] || escapeHtml(event.status)}</b><span>${escapeHtml(event.label)} · ${escapeHtml(event.detail || "")}</span>`;
    $("evidence-log").appendChild(row);
  } else if (event.type === "quota_notice") {
    showQuotaNotice(event);
  } else if (event.type === "git_report") {
    renderGitReport(event, event.run_id);
  } else if (event.type === "complete") {
    setSummaryPending(finalBubble, false);
    finishWorkflowProgress(activeRun?.runId, activeRun?.mode);
    renderMarkdown(finalBubble, event.response);
    finishRun(conversationId, true);
    loadConversations(false);
    loadTasks().catch(() => {});
    launchNextQueued(conversationId);
  } else if (event.type === "error") {
    setSummaryPending(finalBubble, false);
    failRunningWorkflow();
    finalBubble.textContent = `Erreur : ${event.message}`;
    finishRun(conversationId, false);
    loadTasks().catch(() => {});
    loadConversations(false).then(() => selectConversation(conversationId));
  } else if (event.type === "cancelled") {
    setSummaryPending(finalBubble, false);
    failRunningWorkflow();
    const prompt = state.runs.get(conversationId)?.request || "";
    finishRun(conversationId, false);
    loadTasks().catch(() => {});
    $("request").value = prompt;
    resizeComposer();
    $("run-state").textContent = "Interrompu";
    loadConversations(false).then(() => selectConversation(conversationId));
  }
  scrollIfFollowing(conversationViewport, followConversation);
}

function finishRun(conversationId, ok) {
  state.runs.delete(conversationId);
  $("send").disabled = false;
  $("send").querySelector("span").textContent = t("send");
  $("stop").classList.add("hidden");
  $("stop").disabled = false;
  $("stop").querySelector("span").textContent = t("stop");
  $("run-state").textContent = t(ok ? "done" : "failed");
  $("run-state").className = `run-state ${ok ? "done" : "idle"}`;
  if (conversationId === state.activeConversationId) {
    const conversation = state.conversations.find(
      item => item.id === conversationId
    );
    if (conversation) conversation.unread_completion = false;
    joeFetch(`/api/conversations/${encodeURIComponent(conversationId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ unread_completion: false })
    }).catch(() => {});
  }
}

function currentRunSettings() {
  let model = $("model").value;
  if (model === "__custom__") {
    model = window.prompt("Identifiant exact du modèle :") || "";
  }
  return {
    agent: $("agent").value,
    mode: $("mode").value,
    model,
    effort: $("effort").value,
    execution_mode: $("execution-mode").value,
    attachments: [...selectedFileIds]
  };
}

function enqueueRequest(conversationId, request, settings) {
  const queue = state.queues.get(conversationId) || [];
  queue.push({ request, settings });
  state.queues.set(conversationId, queue);
  renderPromptQueue();
}

function renderPromptQueue() {
  const target = $("prompt-queue");
  const queue = state.queues.get(state.activeConversationId) || [];
  target.replaceChildren();
  target.classList.toggle("hidden", !queue.length);
  if (!queue.length) return;
  const heading = document.createElement("div");
  heading.className = "queue-heading";
  heading.innerHTML = `<strong>File d’attente</strong><span>${queue.length} prompt${queue.length > 1 ? "s" : ""}</span>`;
  target.appendChild(heading);
  queue.forEach((item, index) => {
    const row = document.createElement("div");
    row.className = "queue-item";
    const text = document.createElement("span");
    text.textContent = item.request;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.title = "Retirer de la file";
    remove.textContent = "×";
    remove.onclick = () => {
      queue.splice(index, 1);
      if (!queue.length) state.queues.delete(state.activeConversationId);
      renderPromptQueue();
    };
    row.append(text, copyButton(() => item.request), remove);
    target.appendChild(row);
  });
}

function launchNextQueued(conversationId) {
  const queue = state.queues.get(conversationId);
  if (!queue?.length || state.runs.has(conversationId)) return;
  const next = queue.shift();
  if (!queue.length) state.queues.delete(conversationId);
  if (conversationId === state.activeConversationId) renderPromptQueue();
  setTimeout(
    () => startRun(next.request, conversationId, next.settings),
    100
  );
}

async function startRun(
  request,
  conversationId = state.activeConversationId,
  settings = null
) {
  if (!conversationId) return;
  settings = settings || currentRunSettings();
  const mentioned = knownFiles.filter(
    file => request.includes(`@${file.name}`)
  ).map(file => file.id);
  settings.attachments = [
    ...new Set([...(settings.attachments || []), ...mentioned])
  ];
  if (state.runs.has(conversationId)) {
    enqueueRequest(conversationId, request, settings);
    return;
  }
  const payload = {
    request,
    conversation_id: conversationId,
    ...settings
  };
  let response = await joeFetch("/api/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (response.status === 428) {
    const pending = await response.json();
    const approved = await confirmFullAccess();
    if (!approved) {
      await loadTasks();
      return;
    }
    await joeFetch(`/api/approvals/${encodeURIComponent(pending.approval_id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision: "approved" })
    });
    response = await joeFetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...payload, approval_id: pending.approval_id })
    });
  }
  if (!response.ok) {
    const error = await response.json();
    window.alert(error.error || response.statusText);
    return;
  }
  const visible = conversationId === state.activeConversationId;
  if (visible) {
    state.agents.clear();
    $("agents").replaceChildren();
    $("raw-log").textContent = "";
    $("send").disabled = false;
    $("send").querySelector("span").textContent = t("queue");
    $("stop").classList.remove("hidden");
    $("run-state").textContent = t("running");
    $("run-state").className = "run-state running";
    addMessage("Toi", request, "user");
  }
  const finalBubble = visible
    ? addMessage("Joe · synthèse", "Routage local en cours…", "assistant")
    : null;
  const { run_id } = await response.json();
  selectedFileIds.clear();
  renderAttachmentChips();
  renderFileLibrary();
  attachRun(conversationId, run_id, request, finalBubble);
  loadConversations(false);
}

function confirmFullAccess() {
  const dialog = $("permission-dialog");
  return new Promise(resolve => {
    const onClose = () => {
      dialog.removeEventListener("close", onClose);
      resolve(dialog.returnValue === "default");
    };
    dialog.addEventListener("close", onClose);
    dialog.showModal();
  });
}

function attachRun(conversationId, runId, request, finalBubble = null) {
  const current = state.runs.get(conversationId) || {};
  if (current.stream) return;
  const cursor = Number(current.lastEventId) || 0;
  const stream = new EventSource(`/api/events/${runId}?after=${cursor}`);
  const activeRun = {
    ...current,
    runId,
    stream,
    bubble: finalBubble || current.bubble || null,
    request,
    workflow: current.workflow || new Map(),
    lastEventId: cursor
  };
  state.runs.set(conversationId, activeRun);
  stream.onmessage = ({ data, lastEventId }) => {
    if (lastEventId) activeRun.lastEventId = Number(lastEventId);
    const event = JSON.parse(data);
    handleEvent(conversationId, event, activeRun.bubble);
    if (event.type === "complete" || event.type === "error" || event.type === "cancelled") stream.close();
  };
  stream.onerror = () => {
    stream.close();
    if (state.runs.has(conversationId)) {
      if (activeRun.bubble) {
        activeRun.bubble.textContent = "Connexion au flux interrompue · vérification du run côté serveur…";
      }
      activeRun.stream = null;
      setTimeout(() => reconcileRun(conversationId, runId), 1200);
    }
  };
}

async function reconcileRun(conversationId, previousRunId, attempt = 0) {
  if (!state.runs.has(conversationId)) return;
  let runs;
  try {
    const response = await joeFetch("/api/runs/active");
    if (!response.ok) throw new Error("server unavailable");
    runs = await response.json();
  } catch {
    if (attempt < 10) {
      setTimeout(
        () => reconcileRun(conversationId, previousRunId, attempt + 1),
        1500
      );
    }
    return;
  }
  const serverRun = runs.find(
    run => run.conversation_id === conversationId
  );
  if (serverRun) {
    const local = state.runs.get(conversationId);
    if (local) local.stream = null;
    attachRun(
      conversationId,
      serverRun.run_id,
      serverRun.request,
      local?.bubble || null
    );
    return;
  }
  const local = state.runs.get(conversationId);
  try {
    const response = await joeFetch(`/api/conversations/${conversationId}`);
    if (response.ok) {
      const conversation = await response.json();
      const completed = [...(conversation.messages || [])].reverse().find(
        message => (
          message.role === "assistant"
          && message.run_id === previousRunId
        )
      );
      if (completed) {
        if (local?.bubble) {
          renderHistoricalRunSummary(completed, local.bubble);
          renderMarkdown(local.bubble, completed.content);
          if (completed.git_report) {
            renderGitReport(completed.git_report, completed.run_id);
          }
        }
        if (conversationId === state.activeConversationId) {
          finishRun(conversationId, true);
        } else {
          state.runs.delete(conversationId);
        }
        loadConversations(false);
        launchNextQueued(conversationId);
        return;
      }
    }
  } catch {
    // Fall through to the genuine interruption state.
  }
  if (local?.bubble) {
    local.bubble.textContent = previousRunId
      ? "La tâche a été interrompue par le redémarrage de Joe."
      : "Aucune tâche active côté serveur.";
  }
  if (conversationId === state.activeConversationId) {
    finishRun(conversationId, false);
  } else {
    state.runs.delete(conversationId);
  }
  loadConversations(false);
}

async function loadActiveRuns() {
  const runs = await joeFetch("/api/runs/active").then(response => response.json());
  for (const run of runs) {
    state.runs.set(run.conversation_id, {
      runId: run.run_id,
      stream: null,
      bubble: null,
      request: run.request,
      workflow: new Map(),
      lastEventId: 0
    });
  }
}

function connectActiveRuns() {
  for (const [conversationId, run] of state.runs) {
    attachRun(conversationId, run.runId, run.request, run.bubble);
  }
}

async function cancelActiveRun() {
  const conversationId = state.activeConversationId;
  const run = state.runs.get(conversationId);
  if (!run) return;
  $("stop").disabled = true;
  $("stop").querySelector("span").textContent = t("stopping");
  const response = await joeFetch(`/api/runs/${run.runId}/cancel`, { method: "POST" });
  if (!response.ok) {
    $("stop").disabled = false;
    $("stop").querySelector("span").textContent = t("stop");
  }
}

$("composer").addEventListener("submit", async event => {
  event.preventDefault();
  if (!state.activeConversationId) return;
  const request = $("request").value.trim();
  if (!request) return;
  $("request").value = "";
  resizeComposer();
  await startRun(request);
});
$("request").addEventListener("input", resizeComposer);
$("request").addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    $("composer").requestSubmit();
  }
});
$("agent").addEventListener("change", () => {
  updateCapabilityMenus();
  saveSettings();
});
$("model").addEventListener("change", () => {
  updateEfforts();
  saveSettings();
});
$("mode").addEventListener("change", saveSettings);
$("effort").addEventListener("change", saveSettings);
$("execution-mode").addEventListener("change", saveSettings);
$("tool-web-access").addEventListener("change", saveSettings);
$("task-filter").addEventListener("change", renderTasks);
$("attach-files").onclick = () => $("file-input").click();
$("file-input").addEventListener("change", async event => {
  await uploadFiles([...event.target.files]);
  event.target.value = "";
});
for (const eventName of ["dragenter", "dragover"]) {
  $("composer").addEventListener(eventName, event => {
    event.preventDefault();
    $("composer").classList.add("dragging-files");
  });
}
for (const eventName of ["dragleave", "drop"]) {
  $("composer").addEventListener(eventName, event => {
    event.preventDefault();
    $("composer").classList.remove("dragging-files");
  });
}
$("composer").addEventListener("drop", async event => {
  const files = [...(event.dataTransfer?.files || [])];
  if (files.length) await uploadFiles(files);
});
$("refresh-files").onclick = () => loadFiles().catch(() => {});
$("close-onboarding").onclick = () => {
  window.localStorage.setItem("joe-onboarded-v1", "1");
};
window.addEventListener("joe:conversation-selected", () => {
  selectedFileIds.clear();
  loadFiles().catch(() => {});
});
document.addEventListener("click", closeSelectMenus);
$("new-project").onclick = createProject;
$("cancel-project").onclick = () => {
  state.editingProjectId = null;
  $("project-dialog").close();
};
$("save-project").onclick = saveProject;
$("confirm-delete-conversation").onclick = deleteConversation;
$("stop").onclick = cancelActiveRun;
$("refresh-usage").onclick = () => loadUsage(true).catch(error => {
  // L'actualisation active exige le profil maintainer : sans ce garde-fou,
  // un refus produit une promesse rejetée non traitée.
  $("usage").innerHTML =
    `<span class="usage-loading">${escapeHtml(error.message)}</span>`;
});
$("open-preferences").onclick = () => openPreferences().catch(
  error => window.alert(error.message)
);
$("open-project-skills").onclick = () => {
  const conversation = state.conversations.find(
    item => item.id === state.activeConversationId
  );
  const project = state.projects.find(
    item => item.id === conversation?.project_id
  );
  if (!project) {
    window.alert("Sélectionne d’abord une conversation liée à un projet.");
    return;
  }
  $("preferences-dialog").close();
  openProject(project);
};
$("save-preferences").onclick = savePreferences;
for (const button of document.querySelectorAll("[data-language]")) {
  button.addEventListener("click", () => applyLanguage(button.dataset.language));
}

function escapeHtml(value) {
  const node = document.createElement("span");
  node.textContent = value;
  return node.innerHTML;
}

function capitalize(value) {
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : "";
}

function shouldFollow(element) {
  if (!element) return false;
  return element.scrollHeight - element.scrollTop - element.clientHeight < 48;
}

function scrollIfFollowing(element, follow) {
  if (!element || !follow) return;
  requestAnimationFrame(() => element.scrollTo({ top: element.scrollHeight, behavior: "smooth" }));
}

function setupPanelResizers() {
  const layout = document.querySelector(".layout");
  for (const handle of document.querySelectorAll(".panel-resizer")) {
    handle.onpointerdown = event => {
      if (window.innerWidth <= 720) return;
      event.preventDefault();
      handle.setPointerCapture(event.pointerId);
      handle.classList.add("dragging");
      document.body.style.userSelect = "none";
      handle.onpointermove = moveEvent => {
        if (handle.dataset.resizer === "left") {
          const width = Math.max(180, Math.min(430, moveEvent.clientX));
          layout.style.setProperty("--left-panel", `${width}px`);
        } else {
          const width = Math.max(
            240,
            Math.min(540, window.innerWidth - moveEvent.clientX)
          );
          layout.style.setProperty("--right-panel", `${width}px`);
        }
      };
      handle.onpointerup = () => {
        handle.classList.remove("dragging");
        document.body.style.userSelect = "";
        handle.onpointermove = null;
      };
    };
  }
}

function resizeComposer() {
  const area = $("request");
  const maximum = Math.min(window.innerHeight * 0.42, 360);
  area.style.height = "auto";
  const target = Math.max(48, Math.min(area.scrollHeight, maximum));
  area.style.height = `${target}px`;
  area.style.overflowY = area.scrollHeight > maximum ? "auto" : "hidden";
}

function closeMobilePanels() {
  for (const [panelSelector, buttonId] of [
    [".history-panel", "toggle-history"],
    [".activity-panel", "toggle-activity"]
  ]) {
    document.querySelector(panelSelector)?.classList.remove("mobile-open");
    $(buttonId)?.setAttribute("aria-expanded", "false");
  }
}

function toggleMobilePanel(panelSelector, buttonId) {
  const panel = document.querySelector(panelSelector);
  const opening = !panel.classList.contains("mobile-open");
  closeMobilePanels();
  if (opening) {
    panel.classList.add("mobile-open");
    $(buttonId).setAttribute("aria-expanded", "true");
  }
}

setInterval(updateCountdowns, 1000);
setInterval(() => loadUsage().catch(() => {}), 60000);
setInterval(() => loadTasks().catch(() => {}), 10000);
setupPanelResizers();
resizeComposer();
for (const select of document.querySelectorAll("select")) {
  setupSelectMenu(select);
}
applyLanguage(language);
$("toggle-history").onclick = () => toggleMobilePanel(
  ".history-panel",
  "toggle-history"
);
$("toggle-activity").onclick = () => toggleMobilePanel(
  ".activity-panel",
  "toggle-activity"
);

function reportStartupFailure(error) {
  // Un échec de démarrage doit être visible dans l'interface : une trace
  // console laisse l'utilisateur devant une page vide sans explication.
  console.error("Joe initialization failed", error);
  const banner = $("restart-warning");
  if (!banner) return;
  banner.textContent = `Joe n’a pas pu charger cette interface : ${error.message}`;
  banner.classList.remove("hidden");
}

window.JoeAuth.pairBrowser()
  .then(() => {
    Promise.all([loadStatus(), loadActiveRuns(), loadTasks()])
      .then(() => loadConversations())
      .then(connectActiveRuns)
      .then(loadDoctor)
      .catch(reportStartupFailure);

    loadCapabilities().catch(() => {
      state.capabilities = {};
    });
    loadUsage().catch(() => {
      $("usage").innerHTML = '<span class="usage-loading">Quotas momentanément indisponibles</span>';
    });
  })
  .catch(error => {
    console.error("Joe pairing failed", error);
  });
