const APP_VERSION = "1.3.1";
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
const automation = window.createAutomationModule({ state, $, fetcher: joeFetch });
const notifiedTaskConflicts = new Set();
let language = window.JoeI18n.initialLanguage(
  window.localStorage,
  window.navigator.language
);
const t = (key, params) => window.JoeI18n.translate(language, key, params);

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
  // L'option « Automatique » du menu Agent est construite en JavaScript : elle
  // ne porte pas de balise `data-i18n`, donc la traduction du document ne
  // l'atteint pas. Elle restait dans la langue precedente pendant que tout le
  // reste changeait. On rebatit le menu avant de repeindre.
  updateProviderMenu(state.providerCatalog || []);
  for (const select of document.querySelectorAll("select")) {
    refreshSelectMenu(select);
  }
  if (knownTasks.length) renderTasks();
  if (typeof renderUsage === "function" && state.usage.length) renderUsage();
  automation.render?.();
  if (typeof renderConversations === "function" && state.projects.length) {
    renderConversations();
  }
  window.dispatchEvent(new CustomEvent("joe:language-changed", {
    detail: { language }
  }));
}

// Journal servi au dernier passage. Joe peut être relancé depuis un autre
// dossier : la page afficherait alors un autre historique sans prévenir, et
// toute action sur une conversation de l'ancien échouerait en « introuvable ».
let servedStore = null;

async function loadStatus() {
  const status = await fetch("/api/status").then(response => response.json());
  updateProviderMenu(status.provider_catalog || status.providers || []);
  annotateNetworkControl(status.network_control_providers);
  $("version").textContent = status.version || t("old_version");
  if (status.conversation_store) {
    if (servedStore && status.conversation_store !== servedStore) {
      const banner = $("restart-warning");
      banner.textContent = t("store_changed", { path: status.project || "" });
      banner.classList.remove("hidden");
      if (loadConversations) await loadConversations(false).catch(() => {});
    }
    servedStore = status.conversation_store;
  }
  if (status.version !== APP_VERSION) {
    const warning = $("restart-warning");
    warning.textContent = t("old_backend", { version: status.version || t("current_version") });
    warning.classList.remove("hidden");
  }
}

function annotateNetworkControl(controlled) {
  // Le réglage ne contraint que les fournisseurs qui exposent réellement un
  // commutateur réseau : on le dit, plutôt que de laisser croire à une
  // garantie globale.
  if (!Array.isArray(controlled)) return;
  const scope = controlled.length
    ? t("network_control_only", { providers: controlled.map(capitalize).join(", ") })
    : t("network_control_none");
  for (const id of ["tool-web-access-help", "project-remote-access-help"]) {
    const target = $(id);
    if (!target) continue;
    const prefix = id === "tool-web-access-help"
      ? t("available_default")
      : t("enabled_default");
    target.textContent = `${prefix} ${scope}`;
  }
}

// Une CLI absente reste visible mais inchoisissable : la masquer ferait croire
// que Joe ne la connaît pas, et la proposer mènerait à un choix qui ne peut pas
// aboutir. Le panneau des CLI dit comment l'installer.
function providerMenuItems(providers, selected) {
  return providers.map(provider => {
    const item = typeof provider === "string"
      ? { id: provider, label: capitalize(provider) }
      : { ...provider };
    if (item.available === false) {
      item.label = `${item.label} — ${t("provider_absent")}`;
      item.disabled = item.id !== selected;
    }
    return item;
  });
}

function updateProviderMenu(providers) {
  // Le statut est relu toutes les cinq secondes. Reconstruire le menu a chaque
  // fois remettait la sélection à zéro puis la restaurait — mais le menu
  // visible, lui, restait sur « Automatique ». On ne reconstruit donc que
  // lorsque le catalogue ou la langue ont réellement changé.
  const signature = `${language}|${JSON.stringify(providers)}`;
  if (signature === state.providerCatalogSignature) return;
  state.providerCatalogSignature = signature;
  state.providerCatalog = providers;
  const selected = $("agent").value;
  setOptions(
    $("agent"),
    [{ id: "", label: t("automatic") }, ...providerMenuItems(providers, selected)]
  );
  if ([...$("agent").options].some(option => option.value === selected)) {
    $("agent").value = selected;
  }
  // `setOptions` a rafraîchi le menu visible avant que la valeur ne soit
  // remise : sans ce second passage, l'affichage contredit la sélection.
  refreshSelectMenu($("agent"));
  // Le modèle dépend de l'agent : si la reconstruction a perdu la sélection,
  // la liste des modèles doit suivre au lieu de rester sur l'ancienne.
  if ($("agent").value !== selected) updateCapabilityMenus();
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
    return ["running", "waiting_quota", "integrating", "resolving", "review", "conflict"].includes(task.status);
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
    waiting_quota: t("waiting_quota"),
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
    card.title = t("open_task");
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
      <div class="task-meta">${branch}<span>${Number(task.files || 0)} ${escapeHtml(t("files"))} · +${Number(task.insertions || 0)} −${Number(task.deletions || 0)}</span></div>
      ${task.status === "waiting_quota" && task.scheduled_for ? `<div class="task-wait">${escapeHtml(t("resume_capitalized"))} ${escapeHtml(new Date(task.scheduled_for * 1000).toLocaleString(language))}</div>` : ""}
      <div class="task-pipeline">${(task.pipeline || []).map(stage => (
        `<span class="${escapeHtml(stage.status)}"><i></i>${escapeHtml(({ Demande: t("request_stage"), Réalisation: t("implementation_stage"), Validation: t("validation_stage"), Diff: t("diff_stage"), Livraison: t("delivery_stage") })[stage.label] || stage.label)}</span>`
      )).join("")}</div>
      ${task.error ? `<p class="task-error">${escapeHtml(task.error)}</p>` : ""}
      <div class="task-actions"></div>`;
    const actions = card.querySelector(".task-actions");
    actions.appendChild(taskAction(t("conversation"), openConversation));
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
  if (approval.kind === "plan") return renderPlanApproval(approval);
  const card = document.createElement("article");
  card.className = "task-card approval";
  card.innerHTML = `
    <div class="task-card-head"><strong>${t("approval_requested")}</strong><b>${t("review_task")}</b></div>
    <p class="task-error">${escapeHtml(approval.message)}</p>
    <div class="task-meta"><span>${escapeHtml(approval.payload?.request || "")}</span></div>
    <div class="task-actions"></div>`;
  const actions = card.querySelector(".task-actions");
  actions.append(
    taskAction(t("refuse"), () => decideApproval(approval, "refused"), "danger"),
    taskAction(t("allow"), () => decideApproval(approval, "approved"), "primary")
  );
  return card;
}

function renderPlanApproval(approval) {
  // Un plan se lit avant d'être validé : on le rend en entier, et on laisse
  // l'ajuster sans le réécrire, comme le fait Codex.
  const card = document.createElement("article");
  card.className = "task-card approval plan-approval";
  card.innerHTML = `
    <div class="task-card-head"><strong>${t("proposed_plan")}</strong><b>${t("review_task")}</b></div>
    <div class="task-meta"><span>${escapeHtml(approval.payload?.request || "")}</span></div>
    <div class="plan-body"></div>`;
  renderMarkdown(card.querySelector(".plan-body"), approval.payload?.plan || "");
  card.appendChild(planControls(approval));
  return card;
}

// Zone d'ajustement + boutons de décision d'un plan, partagée entre la carte du
// panneau Tâches et le rappel affiché sous la réponse dans la fenêtre principale.
function planControls(approval) {
  const wrap = document.createElement("div");
  wrap.className = "plan-controls";
  const notesLabel = document.createElement("label");
  notesLabel.className = "plan-notes-label";
  notesLabel.textContent = t("changes_optional");
  const notes = document.createElement("textarea");
  notes.className = "plan-notes";
  notes.rows = 2;
  notes.placeholder = t("changes_placeholder");
  notesLabel.appendChild(notes);
  const actions = document.createElement("div");
  actions.className = "task-actions";
  // Le même plan peut être affiché à deux endroits : une décision doit retirer
  // les deux jeux de boutons pour éviter une double validation.
  const done = () => dropPlanControls(approval.id);
  actions.append(
    taskAction(t("refuse"), () => decideApproval(approval, "refused").then(done), "danger"),
    taskAction(t("schedule_ellipsis"), () => schedulePlan(approval)),
    taskAction(
      t("allow_with_changes"),
      () => decideApproval(approval, "approved", notes.value.trim()).then(done)
    ),
    taskAction(t("allow"), () => decideApproval(approval, "approved").then(done), "primary")
  );
  wrap.append(notesLabel, actions);
  wrap.dataset.approvalId = approval.id;
  return wrap;
}

function dropPlanControls(approvalId) {
  for (const node of document.querySelectorAll(
    `.plan-controls[data-approval-id="${CSS.escape(approvalId)}"]`
  )) {
    node.remove();
  }
}

// Après un run en mode plan, la réponse (le plan lui-même) est déjà dans la
// bulle. On y accroche les mêmes commandes de validation que dans le panneau
// Tâches, pour lire et décider sans quitter la fenêtre principale.
function attachPlanControls(conversationId, bubble) {
  if (!bubble) return;
  const approval = knownApprovals.find(
    item => item.kind === "plan" && item.conversation_id === conversationId
  );
  if (!approval) return;
  const wrapper = bubble.closest(".message") || bubble.parentElement;
  if (!wrapper || wrapper.querySelector(".plan-controls")) return;
  wrapper.appendChild(planControls(approval));
}

// Un plan validé peut partir tout de suite (« Autoriser ») ou être confié au
// planificateur autonome : mêmes étapes, exécutées sans personne devant l'écran.
// Le plan vient de Joe (mode plan) ou de l'utilisateur, qui écrit ses étapes
// directement dans le formulaire — les deux aboutissent au même endroit.
async function schedulePlan(approval) {
  const payload = approval.payload || {};
  const steps = window.JoeMarkdown.planSteps(payload.plan);
  if (!steps.length) {
    window.alert(
      t("plan_has_no_steps")
    );
  }
  await automation.open({
    title: (payload.request || "Plan autonome").slice(0, 80),
    steps,
    // Le plan part au planificateur : l'approbation est honorée, sans lancer
    // de run immédiat — c'est tout l'intérêt d'un travail différé.
    onScheduled: async () => {
      await joeFetch(`/api/approvals/${encodeURIComponent(approval.id)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision: "approved" })
      });
      dropPlanControls(approval.id);
      await loadTasks();
    }
  });
}

async function decideApproval(approval, decision, notes = "") {
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
    if (approval.kind === "plan") {
      // Pas de fournisseur imposé ni d'approval_id : le routeur choisit à neuf
      // l'agent le mieux placé selon les quotas restants et la tâche.
      const parts = [payload.request, `# ${t("validated_plan")}`, payload.plan];
      if (notes) parts.push(`# ${t("requested_changes")}`, notes);
      // Le modèle reçoit tout (demande + plan + ajustements), mais l'historique
      // n'affiche qu'un intitulé court : le plan est déjà lisible juste au-dessus.
      await startRun(parts.join("\n\n"), approval.conversation_id, {
        ...currentRunSettings(),
        plan: false,
        promptLabel: t("implement_validated_plan")
      });
    } else {
      await startRun(
        payload.request,
        payload.conversation_id,
        { ...payload, approval_id: approval.id }
      );
    }
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

window.addEventListener("joe:open-conversation", async event => {
  const conversationId = event.detail?.conversationId;
  if (!conversationId) return;
  await loadConversations(false);
  await selectConversation(conversationId);
});

async function showTaskDiff(task) {
  const response = await joeFetch(`/api/tasks/${encodeURIComponent(task.id)}/diff`);
  const report = await response.json();
  if (!response.ok) {
    window.alert(report.error || t("diff_unavailable"));
    return;
  }
  $("task-diff-title").textContent = task.title;
  $("task-diff-stats").textContent = `${report.files.length} ${t(report.files.length === 1 ? "file_singular" : "file_plural")} · +${report.insertions} −${report.deletions}`;
  $("task-diff-files").innerHTML = report.files.map(file => (
    `<span><b>${escapeHtml(file.path)}</b><small>+${file.insertions} −${file.deletions}</small></span>`
  )).join("") || `<small>${t("no_changes")}</small>`;
  $("task-diff-preview").textContent = report.patch_preview || t("no_text_diff");
  $("task-diff-dialog").showModal();
}

async function integrateTask(task) {
  if (!window.confirm(t("integrate_branch_confirm", { branch: task.branch }))) return;
  const response = await joeFetch(`/api/tasks/${encodeURIComponent(task.id)}/integrate`, {
    method: "POST"
  });
  const payload = await response.json();
  if (!response.ok) {
    window.alert(payload.error || t("integration_impossible"));
    return;
  }
  await loadTasks();
}

async function deleteTask(task) {
  if (!window.confirm(
    t("discard_task_confirm", { branch: task.branch })
  )) return;
  const response = await joeFetch(`/api/tasks/${encodeURIComponent(task.id)}`, {
    method: "DELETE"
  });
  const payload = await response.json();
  if (!response.ok) {
    window.alert(payload.error || t("deletion_impossible"));
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
    target.innerHTML = `<small>${t("no_project_files")}</small>`;
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
    remove.title = t("delete_file");
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
    download.title = t("open_or_download");
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
    chip.title = t("remove_from_request");
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
      window.alert(payload.error || t("add_file_failed", { name: file.name }));
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

// Une seule liste de fournisseurs, rendue à deux endroits : l'écran de
// première ouverture et le panneau permanent des préférences. Deux rendus
// séparés auraient divergé, et c'est le second qui compte — on installe une
// CLI des semaines après avoir découvert Joe.
function providerState(provider) {
  if (!provider.enabled) return { key: "provider_turned_off", tone: "muted" };
  if (provider.installed) return { key: "provider_detected", tone: "ok" };
  if (provider.unconfirmed) return { key: "provider_unconfirmed", tone: "warn" };
  return { key: "provider_absent", tone: "muted" };
}

function providerRow(provider, interactive) {
  const row = document.createElement("div");
  row.className = `provider-row ${providerState(provider).tone}`;
  const head = document.createElement("div");
  head.className = "provider-head";
  const name = document.createElement("b");
  name.textContent = provider.label || provider.provider;
  const state = document.createElement("span");
  state.textContent = [
    t(providerState(provider).key),
    provider.version || "",
    provider.runtime_issue || ""
  ].filter(Boolean).join(" · ");
  head.append(name, state);
  row.appendChild(head);
  // L'interrupteur n'a de sens que sur une CLI présente : rien à écarter
  // tant qu'elle n'est pas là.
  if (interactive && (provider.installed || !provider.enabled)) {
    const choice = document.createElement("label");
    choice.className = "provider-choice";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = provider.enabled !== false;
    box.addEventListener("change", () => setProviderEnabled(provider.provider, box.checked));
    const knob = document.createElement("span");
    knob.className = "provider-switch";
    knob.setAttribute("aria-hidden", "true");
    const text = document.createElement("span");
    text.textContent = t("provider_use");
    choice.append(box, knob, text);
    row.appendChild(choice);
  }
  // Une CLI depreciee reste detectee et se laisse choisir : sans cette
  // mention, l'utilisateur voit des echecs sans cause apparente.
  const retired = provider.deprecated || {};
  if (retired.since) {
    const note = document.createElement("div");
    note.className = "provider-retired";
    const text = document.createElement("small");
    text.textContent = t("provider_retired", {
      date: retired.since,
      successor: retired.successor || ""
    });
    note.appendChild(text);
    if (retired.successor_url) {
      const link = document.createElement("a");
      link.href = retired.successor_url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = retired.successor || retired.successor_url;
      note.appendChild(link);
    }
    row.appendChild(note);
  }
  const install = provider.install || {};
  // Personne n'installe cinq CLI : sans savoir a quoi chacune sert, choisir
  // revient a deviner. Le texte reprend la doctrine de routage de Joe.
  const purpose = t(`provider_purpose_${provider.provider.replace(/-/g, "_")}`);
  if (!provider.installed && purpose) {
    const role = document.createElement("small");
    role.className = "provider-purpose";
    role.textContent = purpose;
    row.appendChild(role);
  }
  // Trouver le binaire ne dit rien du compte. Joe ne peut pas le savoir sans
  // lancer la CLI, alors il le dit : une CLI détectée reste inutilisable tant
  // qu'on ne s'y est pas connecté, et l'erreur au premier run n'explique rien.
  if (provider.installed && install.sign_in) {
    const signIn = document.createElement("div");
    signIn.className = `provider-signin${provider.auth_failed ? " failed" : ""}`;
    const note = document.createElement("small");
    note.textContent = t(
      provider.auth_failed ? "provider_sign_in_failed" : "provider_sign_in_once"
    );
    const code = document.createElement("code");
    code.textContent = install.sign_in;
    signIn.append(note, code, copyButton(() => install.sign_in));
    row.appendChild(signIn);
  }
  if (!provider.installed && (install.command || install.homepage)) {
    const help = document.createElement("div");
    help.className = "provider-install";
    // Trois lignes libres laissaient deviner l'ordre des gestes. Une liste
    // numerotee dit ou commencer et ou s'arreter.
    const steps = document.createElement("ol");
    steps.className = "provider-steps";
    if (install.node_warning) {
      const missing = document.createElement("li");
      missing.className = "provider-step-blocked";
      const warn = document.createElement("small");
      warn.textContent = install.node_warning;
      missing.append(document.createTextNode(t("provider_prerequisite")), warn);
      const nodeLink = document.createElement("a");
      nodeLink.href = "https://nodejs.org/en/download";
      nodeLink.target = "_blank";
      nodeLink.rel = "noopener noreferrer";
      nodeLink.textContent = t("provider_install_node");
      missing.appendChild(nodeLink);
      steps.appendChild(missing);
    }
    if (install.command) {
      const step = document.createElement("li");
      step.appendChild(document.createTextNode(t("provider_step_install")));
      const code = document.createElement("code");
      code.textContent = install.command;
      step.append(code, copyButton(() => install.command));
      steps.appendChild(step);
    }
    if (install.sign_in) {
      const step = document.createElement("li");
      step.appendChild(document.createTextNode(t("provider_step_sign_in")));
      const code = document.createElement("code");
      code.textContent = install.sign_in;
      step.append(code, copyButton(() => install.sign_in));
      steps.appendChild(step);
    }
    const last = document.createElement("li");
    last.textContent = t("provider_step_recheck");
    steps.appendChild(last);
    help.appendChild(steps);
    if (install.homepage) {
      const link = document.createElement("a");
      link.href = install.homepage;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = t("provider_official_page");
      help.appendChild(link);
    }
    row.appendChild(help);
  }
  return row;
}

function renderProviders(target, report, { interactive = false } = {}) {
  const providers = report.providers || [];
  // La liste des refus vient du serveur à chaque rendu : la reconstruire de
  // mémoire ferait perdre un refus posé ailleurs, ou depuis un autre onglet.
  state.disabledProviders = new Set(
    providers.filter(provider => provider.enabled === false).map(provider => provider.provider)
  );
  target.replaceChildren();
  if (report.storage && !report.storage.writable) {
    const storage = document.createElement("div");
    storage.className = "provider-row error";
    storage.innerHTML = `<div class="provider-head"><b>${t("storage")}</b><span>${t("needs_fix")}</span></div>`;
    target.appendChild(storage);
  }
  for (const provider of providers) {
    target.appendChild(providerRow(provider, interactive));
  }
  if (!providers.some(provider => provider.installed)) {
    const empty = document.createElement("p");
    empty.className = "provider-empty";
    empty.textContent = t("providers_none");
    target.appendChild(empty);
  }
}

async function fetchDoctor(target, options) {
  try {
    const response = await joeFetch("/api/doctor");
    if (!response.ok) throw new Error("doctor unavailable");
    renderProviders(target, await response.json(), options);
  } catch {
    target.innerHTML = `<p>${t("diagnostic_unavailable")}</p>`;
  }
}

async function setProviderEnabled(provider, enabled) {
  const current = new Set(state.disabledProviders || []);
  if (enabled) current.delete(provider);
  else current.add(provider);
  state.disabledProviders = current;
  const response = await joeFetch("/api/providers", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ disabled: [...current] })
  });
  if (!response.ok) window.alert(t("providers_save_failed"));
  await fetchDoctor($("providers-list"), { interactive: true });
}

// Les onglets des paramètres. Le contenu d'un onglet ne se charge qu'à son
// ouverture : le diagnostic des CLI lance des processus, inutile de le faire
// pour qui vient changer son workflow par défaut.
function showSettingsPane(name) {
  for (const tab of document.querySelectorAll(".settings-tab")) {
    const active = tab.dataset.pane === name;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  }
  for (const pane of document.querySelectorAll(".settings-pane")) {
    pane.classList.toggle("hidden", pane.dataset.pane !== name);
  }
  // « Enregistrer » ne concerne que les réglages a saisir ; les CLI
  // s'enregistrent au clic, et le projet a sa propre fenêtre.
  $("save-preferences").classList.toggle("hidden", name !== "conversations");
  if (name === "providers") fetchDoctor($("providers-list"), { interactive: true });
}

function openSettings(pane = "conversations") {
  $("preferences-dialog").showModal();
  showSettingsPane(pane);
}

const ONBOARDING_STORAGE_KEY = "joe-onboarded-v2";

async function loadDoctor() {
  if (window.localStorage.getItem(ONBOARDING_STORAGE_KEY)) return;
  const dialog = $("onboarding-dialog");
  const start = $("close-onboarding");
  // Fermer avant la fin du diagnostic fait manquer la seule page qui dise
  // quelles CLI manquent et comment les installer — et elle ne revient pas
  // d'elle-même. Le bouton reste donc inerte tant que Joe n'a pas répondu,
  // touche Échap comprise.
  const holdEscape = event => event.preventDefault();
  start.disabled = true;
  start.textContent = t("diagnostic_pending");
  dialog.addEventListener("cancel", holdEscape);
  dialog.showModal();
  try {
    await fetchDoctor($("doctor-status"), { interactive: true });
  } finally {
    dialog.removeEventListener("cancel", holdEscape);
    start.disabled = false;
    start.textContent = t("start_using_joe");
  }
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
    item.disabled = option.disabled;
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
let renderUsage;

function shortCommit(value) {
  return value ? value.slice(0, 8) : t("unavailable");
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
    ? t("dev_status_unknown")
    : report.origin_dev_integrated
      ? t("dev_integrated")
      : t("dev_not_integrated");
  card.innerHTML = `
    <header>
      <div><span class="eyebrow">${t("repository_changes")}</span><div class="diff-summary"><strong>${report.files.length} ${t(report.files.length === 1 ? "file_singular" : "file_plural")}</strong><span class="insertions">+${report.insertions}</span><span class="deletions">−${report.deletions}</span></div></div>
      <span class="change-status">${t(report.delivery?.status === "pushed" ? "pushed" : report.delivery?.status === "committed" ? "committed" : "kept")}</span>
    </header>`;
  const details = document.createElement("details");
  details.className = "git-details";
  details.innerHTML = `
    <summary>Voir les détails</summary>
    <div class="git-facts">
      <span>${t("branch")} <b>${escapeHtml(report.branch || t("detached_head"))}</b></span>
      <span>HEAD <b>${shortCommit(report.head_before)} → ${shortCommit(report.head_after)}</b> · ${t(headChanged ? "changed" : "unchanged")}</span>
      <span>origin/dev <b>${shortCommit(report.origin_dev_before)} → ${shortCommit(report.origin_dev_after)}</b> · ${t(devChanged ? "reference_updated" : "unchanged")} · ${t(report.fetch_observed ? "fetch_observed" : "no_fetch_observed")}</span>
      <span>${integration}</span>
      ${report.delivery ? `<span>Livraison <b>${escapeHtml(report.delivery.message || report.delivery.status)}</b>${report.delivery.commit ? ` · ${shortCommit(report.delivery.commit)}` : ""}</span>` : ""}
    </div>`;
  if (report.files.length) {
    const hint = document.createElement("p");
    hint.className = "git-selection-hint";
    hint.textContent = report.rejectable
      ? t("reject_files_help")
      : t("changes_kept_automatically");
    details.appendChild(hint);
    const list = document.createElement("div");
    list.className = "diff-files";
    for (const file of report.files) {
      const row = document.createElement("label");
      row.innerHTML = `${report.rejectable ? `<input type="checkbox" value="${escapeHtml(file.path)}">` : ""}<code>${escapeHtml(file.path)}</code><span class="insertions">+${file.insertions}</span><span class="deletions">−${file.deletions}</span>${file.preexisting ? `<small title="${t("preexisting_help")}">${t("preexisting")}</small>` : ""}`;
      list.appendChild(row);
    }
    details.appendChild(list);
  }
  if (report.patch_preview) {
    const preview = document.createElement("details");
    preview.className = "diff-preview";
    const summaryNode = document.createElement("summary");
    summaryNode.textContent = t("view_full_diff");
    const patch = document.createElement("pre");
    patch.textContent = report.patch_preview;
    preview.append(summaryNode, patch);
    details.appendChild(preview);
  }
  const actions = document.createElement("div");
  actions.className = "git-actions";
  const keep = document.createElement("button");
  keep.textContent = t("keep_all");
  keep.onclick = () => {
    keep.disabled = true;
    keep.textContent = t("all_kept");
    details.open = false;
  };
  const rejectButton = document.createElement("button");
  rejectButton.className = "reject-changes";
  rejectButton.textContent = t("reject_selection");
  rejectButton.disabled = !report.rejectable || !runId;
  rejectButton.title = report.rejectable
    ? t("restore_selected")
    : report.reject_reason || t("automatic_restore_unavailable");
  rejectButton.onclick = async () => {
    const files = [...details.querySelectorAll('.diff-files input:checked')].map((input) => input.value);
    if (!files.length) {
      window.alert(t("choose_rejected_file"));
      return;
    }
    if (!window.confirm(t("reject_files_confirm", { count: files.length }))) return;
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
    card.querySelector(".change-status").textContent = t("selection_rejected");
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
  // Reconstruire la liste efface la sélection. Les capacités sont chargées en
  // parallèle de la conversation : elles arrivaient souvent après elle et
  // remettaient le modèle sur « défaut du fournisseur ». On garde donc le
  // choix courant tant qu'il figure dans la nouvelle liste.
  const previous = $("model").value;
  setOptions($("model"), [{ id: "", label: t("provider_default") }]);
  setOptions($("effort"), [{ id: "", label: t("model_default") }]);
  if (!capability) {
    for (const control of [$("model"), $("effort")]) {
      control.disabled = true;
      refreshSelectMenu(control);
    }
    return;
  }
  $("model").disabled = false;
  addOptions($("model"), capability.models || []);
  // Échappatoire : la CLI accepte parfois un modèle que Joe ne liste pas
  // encore. Le libellé était écrit en dur, donc en français même en anglais.
  addOptions($("model"), [{ id: "__custom__", label: t("model_by_id") }]);
  if (previous && [...$("model").options].some(option => option.value === previous)) {
    $("model").value = previous;
  }
  refreshSelectMenu($("model"));
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
  $("tool-web-access").checked = settings.web_access !== "off";
  for (const select of [
    $("agent"), $("mode"), $("model"), $("effort")
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
  // La liste des agents etait ecrite dans le document : Cursor y manquait, et
  // rien n'y disait ce qui est installe. Elle vient du catalogue, comme celle
  // du menu principal.
  setOptions($("preference-agent"), [
    { id: "", label: t("automatic_recommended") },
    ...providerMenuItems(state.providerCatalog || [], preferences.agent || "")
  ]);
  $("preference-agent").value = preferences.agent || "";
  $("preference-mode").value = preferences.mode || "";
  refreshSelectMenu($("preference-agent"));
  refreshSelectMenu($("preference-mode"));
  openSettings("conversations");
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
    option.disabled = Boolean(item.disabled);
    select.appendChild(option);
  }
}

let createConversation;
let createProject;
let deleteConversation;
let loadConversations;
let refreshActiveConversation;
let moveConversation;
let openProject;
let renameConversation;
let renderConversations;
let saveProject;
let selectConversation;
let togglePin;
let confirmDeleteConversation;

function clearConversation() { $("messages").replaceChildren(); }

function addMessage(label, text, kind, options = {}) {
  const viewport = document.querySelector(".conversation");
  const follow = shouldFollow(viewport);
  const wrapper = document.createElement("div");
  wrapper.className = `message ${kind}`;
  const title = document.createElement("div");
  title.className = "message-label";
  const titleText = document.createElement("span");
  // Une étiquette traduisible se donne par sa clé : elle est rendue dans la
  // langue courante, et `data-i18n` la fait suivre un changement de langue.
  // Un nom de fournisseur, lui, s'écrit tel quel.
  if (options.labelKey) {
    titleText.dataset.i18n = options.labelKey;
    titleText.textContent = t(options.labelKey);
  } else {
    titleText.textContent = label;
  }
  const copy = copyButton(() => bubble.dataset.source || bubble.textContent);
  title.append(titleText, copy);
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  bubble.dataset.source = text;
  wrapper.append(title, bubble);
  $("messages").appendChild(wrapper);
  if (options.forceScroll) {
    // Un envoi explicite crée un nouveau point de lecture : aller directement
    // au nouveau run, même si une ancienne tâche avait positionné l'historique.
    requestAnimationFrame(() => {
      viewport.scrollTop = viewport.scrollHeight;
    });
  } else if (!options.suppressScroll) {
    scrollIfFollowing(viewport, follow);
  }
  return bubble;
}

function copyButton(getText) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "copy-button";
  button.textContent = t("copy");
  button.title = t("copy_clipboard");
  button.setAttribute("aria-label", t("copy_clipboard"));
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
    button.textContent = t("copied");
    button.classList.add("copied");
    setTimeout(() => {
      button.textContent = t("copy");
      button.classList.remove("copied");
    }, 1200);
  };
  return button;
}

function showRoute(event) {
  const badge = $("route-badge");
  const target = `${event.primary}${event.reviewer ? ` → ${event.reviewer}` : ""}`;
  // Le modèle effectivement appelé fait partie de l'information attendue : une
  // action locale doit dire qu'aucun modèle ne tourne, pas rester muette.
  const model = event.local_action ? t("no_model_called") : event.model;
  badge.textContent = [
    event.mode?.toUpperCase(),
    target,
    model
  ].filter(Boolean).join(" · ");
  badge.title = describeRouterDecision(event);
  badge.classList.remove("hidden");
}

function describeRouterDecision(event) {
  const classifier = event.classifier;
  if (!classifier) {
    return event.decided_by === "lexical"
      ? t("lexical_route_no_model")
      : t("lexical_route", { reason: event.reason || t("internal_rules") });
  }
  return [
    classifier.classifier_provider && classifier.classifier_model
      ? `Routeur ${classifier.classifier_provider}/${classifier.classifier_model}`
      : "Routeur LLM",
    classifier.workflow ? `workflow ${classifier.workflow}` : "",
    classifier.model_tier ? `palier ${classifier.model_tier}` : "",
    classifier.effort ? `effort ${classifier.effort}` : "",
    Number.isFinite(classifier.confidence)
      ? `confiance ${Number(classifier.confidence).toFixed(2)}`
      : "",
    classifier.latency_ms ? `${classifier.latency_ms} ms` : ""
  ].filter(Boolean).join(" · ");
}

function primaryStageLabel(run, localAction) {
  // Un même libellé à l'ouverture et à la clôture de l'étape, sinon le
  // pipeline change de nom en cours de route.
  if ((localAction || run?.localAction) === "create_skill") return t("create_skill_stage");
  if ((localAction || run?.localAction) === "create_autonomous_campaign") return t("autonomous_creation");
  if (run?.intent === "modify") return t("implementation");
  if (run?.intent === "analyze") return "Analyse";
  return "Traitement";
}

function renderRouterDecision(event) {
  // Rend la décision du routeur visible sur la carte de l'agent retenu, et pas
  // seulement dans le journal brut.
  const agent = ensureAgent(event.primary);
  const detail = describeRouterDecision(event);
  const signature = `routage\n${detail}`;
  if (agent.activity.lastElementChild?.dataset.signature === signature) return;
  const row = document.createElement("div");
  row.className = "activity-row";
  row.dataset.signature = signature;
  row.innerHTML = `<i></i><div><strong>${t("routing_label")}</strong><span>${escapeHtml(detail)}</span></div>`;
  agent.activity.appendChild(row);
  while (agent.activity.children.length > 12) {
    agent.activity.firstElementChild.remove();
  }
}

function ensureAgent(name) {
  if (state.agents.has(name)) return state.agents.get(name);
  const card = document.createElement("div");
  card.className = "agent-card";
  card.innerHTML = `<div class="agent-head"><span class="agent-name">${escapeHtml(name)}</span><span class="agent-meta hidden"></span><span class="agent-status">${t("waiting")}</span></div><p class="agent-heartbeat hidden"></p><div class="agent-activity"></div><pre class="agent-output"></pre>`;
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
  const displayedEffort = agent.effort || t("provider_default_effort");
  const parts = [
    agent.model,
    agent.model ? `${t("effort").toLowerCase()} ${displayedEffort}` : ""
  ].filter(Boolean);
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
    event.fallback_from ? t("fallback_from_lower", { provider: capitalize(event.fallback_from) }) : ""
  ].filter(Boolean).join(" · ");
  const stageLabel = workflowStageLabel(event);
  stage.innerHTML = `<summary><span>${escapeHtml(stageLabel)}</span><b>${escapeHtml(providerDetails)} · ${t(complete ? "done_lower" : failed ? "failed_lower" : "running_lower")}</b></summary><div class="workflow-opinion"></div>`;
  if ((complete || failed) && event.content) {
    renderMarkdown(stage.querySelector(".workflow-opinion"), event.content);
    stage.querySelector("summary").appendChild(
      copyButton(() => event.content)
    );
  }
}

function workflowStageLabel(event) {
  if (event.stage === "implementation") return t("workflow_implementation");
  if (event.stage === "review") return t("workflow_review");
  if (event.stage === "synthesis") return t("workflow_synthesis");
  if (event.stage?.startsWith("proposal_")) return t("workflow_proposal");
  if (event.stage?.startsWith("review_")) return t("workflow_cross_review");
  return event.label;
}

function workflowLabels(mode) {
  if (mode === "consensus") {
    return {
      running: t("consensus_running"),
      complete: t("consensus_complete"),
      detail: t("consensus_detail")
    };
  }
  if (mode === "review") {
    return {
      running: t("review_running"),
      complete: t("review_complete"),
      detail: t("review_detail")
    };
  }
  return {
    running: t("execution_running"),
    complete: t("execution_complete"),
    detail: t("execution_detail")
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
    if (status) status.textContent = t("failed");
  }
  // Arrêter toutes les animations d'agents (heartbeats)
  for (const heartbeat of document.querySelectorAll(".agent-heartbeat")) {
    heartbeat.classList.add("hidden");
    heartbeat.textContent = "";
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
        ? ` · ${t("fallback_from_lower", { provider: capitalize(stage.dataset.fallbackFrom) })}`
        : "";
      status.textContent = `${capitalize(provider)} · ${metadata}${relay} · ${t("running_lower")}`;
    }
  }
}

function updateWorkflowFallback(provider, fallback) {
  for (const stage of document.querySelectorAll(".workflow-stage.running")) {
    const status = stage.querySelector("summary b");
    if (!status?.textContent.toLowerCase().startsWith(provider)) continue;
    stage.dataset.fallbackFrom = provider;
    status.textContent = `${capitalize(fallback)} · ${t("fallback_from_lower", { provider: capitalize(provider) })} · ${t("running_lower")}`;
  }
}

({
  loadUsage,
  showQuotaNotice,
  updateCountdowns,
  renderUsage
} = window.createJoeUsage({
  state,
  $,
  escapeHtml,
  capitalize,
  addMessage,
  translate: t,
  fetcher: joeFetch
}));

({
  createConversation,
  createProject,
  deleteConversation,
  loadConversations,
  refreshActiveConversation,
  moveConversation,
  openProject,
  renameConversation,
  renderConversations,
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
  // Une réponse d'agent se rend toujours par `renderAnswer` : elle peut se
  // terminer sur une question, et `renderMarkdown` seul en ferait un bloc de
  // code JSON à la place des boutons.
  renderAnswer,
  renderGitReport,
  renderHistoricalRunSummary,
  attachPlanControls,
  translate: t,
  applySettings,
  refreshSelectMenu,
  renderWorkflowUpdate,
  renderPromptQueue,
  fetcher: joeFetch
}));

const TERMINAL_EVENTS = new Set(["complete", "error", "cancelled"]);

function patchRunningStages(activeRun, provider, patch) {
  // Le prédicat « étape courante de ce fournisseur » n'existe qu'ici.
  for (const [stage, workflowEvent] of activeRun.workflow) {
    if (workflowEvent.status === "running" && workflowEvent.provider === provider) {
      activeRun.workflow.set(stage, { ...workflowEvent, ...patch });
    }
  }
}

function handleEvent(conversationId, event, finalBubble) {
  const activeRun = state.runs.get(conversationId);
  if (event.type === "route" && activeRun) {
    activeRun.mode = event.mode;
    activeRun.intent = event.intent;
    activeRun.localAction = event.local_action || null;
  }
  if (event.type === "workflow_update" && activeRun) {
    event = {
      ...(activeRun.workflow.get(event.stage) || {}),
      ...event
    };
    activeRun.workflow.set(event.stage, event);
  }
  if (event.type === "provider_fallback" && activeRun) {
    patchRunningStages(activeRun, event.provider, {
      provider: event.fallback,
      fallback_from: event.provider
    });
  }
  if (event.type === "provider_start" && activeRun) {
    patchRunningStages(activeRun, event.provider, {
      model: event.model,
      effort: event.effort
    });
  }
  if (conversationId !== state.activeConversationId) {
    const panel = state.panels.get(conversationId);
    if (panel) {
      panel.rawLog += `${JSON.stringify(event)}\n`;
      if (TERMINAL_EVENTS.has(event.type)) {
        for (const node of panel.agentNodes) {
          node.classList.remove("active");
          const status = node.querySelector(".agent-status");
          if (status) status.textContent = t(event.type === "complete" ? "done" : "interrupted");
        }
        panel.runState = t(event.type === "complete" ? "done" : "failed");
        panel.runStateClass = `run-state ${event.type === "complete" ? "done" : "idle"}`;
      }
    }
    if (TERMINAL_EVENTS.has(event.type)) {
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
    showRoute(event);
    loadTasks().catch(() => {});
    const primary = ensureAgent(event.primary);
    // Renseigner la carte dès le routage : une action locale n'émet jamais de
    // provider_start, et la carte restait donc sans modèle ni statut.
    setAgentMeta(primary, {
      model: event.local_action
        ? t("local_action_no_model")
        : (event.model || undefined),
      effort: event.local_action ? "" : (event.effort || undefined)
    });
    if (event.local_action) {
      primary.status.textContent = t("joe_local_action");
    }
    renderRouterDecision(event);
    if (event.reviewer) ensureAgent(event.reviewer);
    setSummaryPending(finalBubble, true);
    finalBubble.textContent = t("final_synthesis_pending");
    if (event.mode === "fast") {
      const label = primaryStageLabel(
        { intent: event.intent },
        event.local_action
      );
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
    agent.status.textContent = t("running");
    agent.heartbeat.textContent = "";
    agent.heartbeat.classList.add("hidden");
    const metadata = setAgentMeta(agent, {
      model: event.model || t("default_model"),
      effort: event.effort || ""
    });
    updateWorkflowProviderMetadata(event.provider, metadata);
  } else if (event.type === "activity") {
    const agent = ensureAgent(event.provider);
    if (event.kind === "heartbeat") {
      agent.heartbeat.textContent = event.label_key === "still_running"
        ? t("active_for", { duration: event.detail })
        : (event.detail || (event.label_key ? t(event.label_key) : event.label));
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
        const activityLabel = event.label_key ? t(event.label_key) : event.label;
        row.innerHTML = `<i></i><div><strong>${escapeHtml(activityLabel)}</strong>${event.detail ? `<span>${escapeHtml(event.detail)}</span>` : ""}</div>`;
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
    agent.status.textContent = event.ok ? t("done") : `${t("failed")} · ${event.error || t("unknown")}`;
    if (!structuredWorkflow && event.ok) {
      renderWorkflowUpdate({
        mode: activeRun?.mode || "fast",
        stage: "primary",
        provider: event.provider,
        label: primaryStageLabel(activeRun, event.local_action),
        status: "complete"
      }, finalBubble, activeRun?.runId || "");
    }
  } else if (event.type === "provider_fallback") {
    const agent = ensureAgent(event.provider);
    agent.card.classList.remove("active");
    agent.heartbeat.classList.add("hidden");
    agent.status.textContent = event.error === "quota"
      ? t("quota_exhausted_fallback", { provider: capitalize(event.fallback) })
      : t("unavailable_fallback", { provider: capitalize(event.fallback) });
    const fallback = ensureAgent(event.fallback);
    fallback.status.textContent = t("relay_from", { provider: capitalize(event.provider) });
    updateWorkflowFallback(event.provider, event.fallback);
  } else if (event.type === "quota_admission") {
    finalBubble.textContent += `\n${event.message}`;
  } else if (event.type === "quota_scheduled") {
    const when = new Date(event.retry_at * 1000).toLocaleString();
    setSummaryPending(finalBubble, false);
    finalBubble.textContent = t("quota_wait", { when });
    $("run-state").textContent = t("waiting");
    $("run-state").className = "run-state running";
    loadTasks().catch(() => {});
  } else if (event.type === "evidence") {
    const row = document.createElement("div");
    row.className = `evidence-row ${event.status}`;
    const labels = { verified: t("verified"), inferred: t("inferred"), refused: t("refused") };
    // Le serveur envoie une clé quand la phrase est traduisible, et du texte
    // brut quand elle ne l'est pas (un nom de fournisseur, un message d'outil).
    const label = event.label_key
      ? t(event.label_key, event.label_params)
      : event.label;
    const detail = event.detail_key
      ? t(event.detail_key, event.detail_params)
      : event.detail || "";
    row.innerHTML = `<b>${labels[event.status] || escapeHtml(event.status)}</b><span>${escapeHtml(label)} · ${escapeHtml(detail)}</span>`;
    $("evidence-log").appendChild(row);
  } else if (event.type === "quota_notice") {
    showQuotaNotice(event);
  } else if (event.type === "git_report") {
    renderGitReport(event, event.run_id);
  } else if (event.type === "complete") {
    setSummaryPending(finalBubble, false);
    finishWorkflowProgress(activeRun?.runId, activeRun?.mode);
    renderAnswer(finalBubble, event.response);
    finishRun(conversationId, true);
    loadConversations(false);
    loadTasks()
      .then(() => attachPlanControls(conversationId, finalBubble))
      .catch(() => {});
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
    $("run-state").textContent = t("interrupted");
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
    model = window.prompt(t("exact_model_prompt")) || "";
  }
  return {
    agent: $("agent").value,
    mode: $("mode").value,
    model,
    effort: $("effort").value,
    attachments: [...selectedFileIds],
    plan: $("tool-plan-first").checked
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
  heading.innerHTML = `<strong>${t("queue_title")}</strong><span>${t("prompt_count", { count: queue.length })}</span>`;
  target.appendChild(heading);
  queue.forEach((item, index) => {
    const row = document.createElement("div");
    row.className = "queue-item";
    const text = document.createElement("span");
    text.textContent = item.request;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.title = t("remove_from_queue");
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
  // `promptLabel` sert uniquement à l'affichage : le modèle reçoit tout le
  // `request`, mais l'historique montre un intitulé court (ex. un plan validé
  // n'a pas à réapparaître en entier comme s'il s'agissait de mon message).
  const { promptLabel, ...runSettings } = settings;
  const shownRequest = promptLabel || request;
  const payload = {
    request,
    conversation_id: conversationId,
    language,
    ...runSettings
  };
  if (promptLabel) payload.prompt_label = promptLabel;
  // Rendu optimiste immédiat : dès le clic sur « Envoyer », on affiche le
  // message de l'utilisateur et une bulle placeholder, sans attendre la
  // réponse du serveur (le routage peut prendre un instant à démarrer).
  const visible = conversationId === state.activeConversationId;
  let optimisticUserBubble = null;
  if (visible) {
    state.agents.clear();
    $("agents").replaceChildren();
    $("raw-log").textContent = "";
    $("send").disabled = false;
    $("send").querySelector("span").textContent = t("queue");
    $("stop").classList.remove("hidden");
    $("run-state").textContent = t("running");
    $("run-state").className = "run-state running";
    optimisticUserBubble = addMessage(null, shownRequest, "user", {
      labelKey: "you"
    });
  }
  const finalBubble = visible
    ? addMessage(
        null,
        t("local_routing"),
        "assistant",
        { forceScroll: true, labelKey: "joe_summary" }
      )
    : null;
  // Comme l'affichage précède désormais l'appel réseau, tout échec doit
  // remettre l'UI dans un état cohérent au lieu de laisser la bulle en attente.
  const failVisibly = message => {
    if (finalBubble) {
      finalBubble.textContent = message;
      finalBubble.dataset.source = message;
      finishRun(conversationId, false);
    } else {
      window.alert(message);
    }
  };
  let response = await joeFetch("/api/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (response.status === 428) {
    const pending = await response.json();
    const choice = await confirmFullAccess();
    if (!choice) {
      failVisibly(t("full_access_denied"));
      await loadTasks();
      return;
    }
    if (choice === "always") await grantAlwaysFullAccess(pending.project_id);
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
    failVisibly(error.error || response.statusText);
    return;
  }
  const { run_id } = await response.json();
  if (optimisticUserBubble) {
    optimisticUserBubble.closest(".message").dataset.runId = run_id;
  }
  if (finalBubble) {
    finalBubble.closest(".message").dataset.runId = run_id;
  }
  selectedFileIds.clear();
  renderAttachmentChips();
  renderFileLibrary();
  attachRun(conversationId, run_id, request, finalBubble);
  loadConversations(false);
}

// Rend le choix tel quel : « une fois », « toujours », ou rien. Le second
// bascule le projet en accès automatique — le réglage existait déjà, mais
// enfoui dans les réglages du projet, loin du moment où la friction se produit.
function confirmFullAccess() {
  const dialog = $("permission-dialog");
  return new Promise(resolve => {
    const onClose = () => {
      dialog.removeEventListener("close", onClose);
      const choice = dialog.returnValue;
      resolve(choice === "default" || choice === "always" ? choice : null);
    };
    dialog.addEventListener("close", onClose);
    dialog.showModal();
  });
}

async function grantAlwaysFullAccess(projectId) {
  if (!projectId) return;
  await joeFetch(`/api/projects/${encodeURIComponent(projectId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ai_access: "auto" })
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
    if (TERMINAL_EVENTS.has(event.type)) stream.close();
  };
  stream.onerror = () => {
    stream.close();
    if (state.runs.has(conversationId)) {
      if (activeRun.bubble) {
        activeRun.bubble.textContent = t("stream_interrupted");
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
          renderAnswer(local.bubble, completed.content);
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
      ? t("interrupted_by_restart")
      : t("no_active_task");
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
  window.localStorage.setItem(ONBOARDING_STORAGE_KEY, "1");
};
window.addEventListener("joe:conversation-selected", () => {
  selectedFileIds.clear();
  loadFiles().catch(() => {});
});
document.addEventListener("click", closeSelectMenus);
// Les popovers « Outils » et « Quotas » sont des <details> natifs : sans ce
// garde, seul un second clic sur le résumé les referme. On les referme dès
// qu'un clic tombe en dehors de leur périmètre.
document.addEventListener("click", event => {
  for (const popover of document.querySelectorAll(
    "details.tool-popover[open], details.usage-popover[open]"
  )) {
    if (!popover.contains(event.target)) popover.removeAttribute("open");
  }
});
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
$("automation-start").onchange = () => automation.syncStartFields();
$("autonomous-schedule-enabled").onchange = () => automation.syncAutonomousSchedule();
$("automation-tab-plan").onclick = () => automation.setView("plan");
$("automation-tab-autonomous").onclick = () => automation.setView("autonomous");
$("open-automation").onclick = () => automation.open().catch(
  error => window.alert(error.message)
);
$("save-automation").onclick = event => automation.save(event).catch(
  error => window.alert(error.message)
);
$("start-autonomous-campaign").onclick = () => automation.startCampaign().catch(
  error => window.alert(error.message)
);
$("recheck-onboarding").onclick = event => {
  // On installe une CLI dans un terminal, cet écran ouvert : sans ce bouton
  // il faudrait fermer l'accueil pour constater qu'elle est arrivée.
  event.preventDefault();
  fetchDoctor($("doctor-status"), { interactive: true });
};

for (const tab of document.querySelectorAll(".settings-tab")) {
  tab.onclick = event => {
    event.preventDefault();
    showSettingsPane(tab.dataset.pane);
  };
}

// On installe une CLI dans un terminal, cette fenêtre ouverte : au retour, la
// liste doit déjà être à jour. Un bouton « actualiser » demandait à
// l'utilisateur de se souvenir d'une étape que la fenêtre peut faire seule.
window.addEventListener("focus", () => {
  const pane = document.querySelector('.settings-pane[data-pane="providers"]');
  if ($("preferences-dialog").open && pane && !pane.classList.contains("hidden")) {
    fetchDoctor($("providers-list"), { interactive: true });
  }
});

$("open-project-skills").onclick = () => {
  const conversation = state.conversations.find(
    item => item.id === state.activeConversationId
  );
  const project = state.projects.find(
    item => item.id === conversation?.project_id
  );
  if (!project) {
    window.alert(t("select_project_conversation"));
    return;
  }
  // Ouvrir une seconde fenêtre sans retour laissait l'utilisateur dans une
  // impasse : il fallait tout refermer pour revenir aux paramètres.
  state.cameFromSettings = true;
  $("preferences-dialog").close();
  openProject(project);
};

$("back-to-settings").onclick = event => {
  event.preventDefault();
  state.cameFromSettings = false;
  $("project-dialog").close();
  openSettings("project");
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
setInterval(() => {
  Promise.all([
    automation.load(),
    refreshActiveConversation(),
    loadStatus()
  ]).catch(() => {});
}, 5000);
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

function renderAnswer(bubble, text) {
  // Rend la réponse, et si elle se termine par un bloc `joe:question`, propose
  // les options en boutons plutôt que de laisser l'utilisateur les recopier.
  const question = window.JoeMarkdown.extractQuestion(text);
  renderMarkdown(bubble, question ? question.body : text);
  if (!question) return;
  const card = document.createElement("div");
  card.className = "question-card";
  card.style.marginTop = "1rem";
  card.style.paddingTop = "1rem";
  card.style.borderTop = "1px solid var(--ink-soft)";
  const label = document.createElement("strong");
  label.textContent = question.question;
  label.style.display = "block";
  label.style.marginBottom = "0.5rem";
  const choices = document.createElement("div");
  choices.className = "question-options";
  for (const option of question.options) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "question-option";
    button.textContent = option;
    button.onclick = () => {
      for (const other of choices.querySelectorAll("button")) other.disabled = true;
      button.classList.add("chosen");
      startRun(option);
    };
    choices.appendChild(button);
  }
  card.append(label, choices);
  bubble.appendChild(card);
}

function reportStartupFailure(error) {
  // Un échec de démarrage doit être visible dans l'interface : une trace
  // console laisse l'utilisateur devant une page vide sans explication.
  console.error("Joe initialization failed", error);
  const banner = $("restart-warning");
  if (!banner) return;
  banner.textContent = t("ui_load_failed", { error: error.message });
  banner.classList.remove("hidden");
}

window.JoeAuth.pairBrowser()
  .then(() => {
    // L'accueil des CLI ne dépend pas du chargement des conversations,
    // tâches ou automatisations. Une erreur dans l'un de ces panneaux ne doit
    // jamais priver une nouvelle installation de son parcours de démarrage.
    loadDoctor().catch(reportStartupFailure);
    Promise.all([loadStatus(), loadActiveRuns(), loadTasks(), automation.load()])
      .then(() => loadConversations())
      .then(connectActiveRuns)
      .catch(reportStartupFailure);

    loadCapabilities().catch(() => {
      state.capabilities = {};
    });
    loadUsage().catch(() => {
      $("usage").innerHTML = `<span class="usage-loading">${t("quotas_temporarily_unavailable")}</span>`;
    });
  })
  .catch(error => {
    console.error("Joe pairing failed", error);
  });
