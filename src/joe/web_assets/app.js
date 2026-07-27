const APP_VERSION = "0.12.1";
const state = {
  agents: new Map(),
  capabilities: {},
  usage: [],
  projects: [],
  activeProjectId: "main",
  conversations: [],
  activeConversationId: null,
  panels: new Map(),
  runs: new Map()
};
const $ = id => document.getElementById(id);

async function loadStatus() {
  const status = await fetch("/api/status").then(response => response.json());
  $("project").textContent = status.project;
  $("version").textContent = status.version || "ancienne version";
  if (status.version !== APP_VERSION) {
    const warning = $("restart-warning");
    warning.textContent = `Le serveur Joe ${status.version || "actuel"} utilise encore un ancien backend. Arrête-le avec Ctrl+C, relance joe, puis recharge cette page.`;
    warning.classList.remove("hidden");
  }
}

async function loadCapabilities() {
  state.capabilities = await fetch("/api/capabilities").then(response => response.json());
  updateCapabilityMenus();
}

async function loadUsage() {
  const response = await fetch("/api/usage");
  if (!response.ok) throw new Error("Quotas indisponibles");
  state.usage = await response.json();
  renderUsage();
}

function renderUsage() {
  const target = $("usage");
  target.replaceChildren();
  for (const provider of state.usage) {
    const card = document.createElement("article");
    card.className = `usage-card ${provider.available ? "" : "unavailable"} ${provider.stale ? "stale" : ""}`;
    const plan = provider.plan ? `<span>${escapeHtml(provider.plan)}</span>` : "";
    card.innerHTML = `<header><strong>${escapeHtml(provider.provider)}</strong>${plan}</header>`;
    if (!provider.available) {
      const message = document.createElement("p");
      message.textContent = provider.message;
      card.appendChild(message);
    } else {
      for (const window of provider.windows) card.appendChild(usageWindow(window));
      if (provider.stale && provider.message) {
        const message = document.createElement("p");
        message.textContent = provider.message;
        card.appendChild(message);
      }
    }
    target.appendChild(card);
  }
  updateCountdowns();
}

function usageWindow(window) {
  const row = document.createElement("div");
  row.className = "usage-window";
  const remaining = Number(window.remaining_percent);
  row.innerHTML = `
    <div class="usage-line"><span>${escapeHtml(window.name)}</span><strong>${formatPercent(remaining)} restant</strong></div>
    <div class="usage-bar"><i style="width:${Math.max(0, Math.min(100, remaining))}%"></i></div>
    <small class="countdown" data-reset="${window.resets_at || ""}"></small>`;
  return row;
}

function formatPercent(value) {
  return `${Number.isInteger(value) ? value : value.toFixed(1)} %`;
}

function updateCountdowns() {
  for (const node of document.querySelectorAll(".countdown")) {
    const reset = Number(node.dataset.reset);
    if (!reset) {
      node.textContent = "Réinitialisation non communiquée";
      continue;
    }
    const seconds = Math.max(0, reset - Date.now() / 1000);
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    node.textContent = seconds <= 0
      ? "Réinitialisation imminente"
      : `Reset dans ${days ? `${days} j ` : ""}${hours ? `${hours} h ` : ""}${minutes} min`;
  }
}

function resetDescription(timestamp) {
  const reset = Number(timestamp);
  if (!reset) return "heure de retour non exposée";
  const seconds = Math.max(0, reset - Date.now() / 1000);
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remaining = seconds <= 0
    ? "réinitialisation imminente"
    : `dans ${days ? `${days} j ` : ""}${hours ? `${hours} h ` : ""}${minutes} min`;
  const date = new Date(reset * 1000).toLocaleString("fr-FR", {
    dateStyle: "short",
    timeStyle: "short"
  });
  return `${remaining} (${date})`;
}

function showQuotaNotice(event) {
  const bubble = addMessage("Joe · limite atteinte", "", "notice");
  const lines = [`${capitalize(event.provider)} a atteint une limite d’utilisation.`];
  if (event.windows?.length) {
    for (const window of event.windows) {
      lines.push(`• ${window.name} : ${resetDescription(window.resets_at)}`);
    }
  } else {
    lines.push(`• ${event.usage_message || "Heure de retour non exposée par la CLI."}`);
  }
  if (event.alternatives?.length) {
    const choices = event.alternatives.map(item => {
      const models = item.models?.length ? ` (${item.models.join(", ")})` : "";
      return `${capitalize(item.provider)}${models}`;
    });
    lines.push(`Joe essaie automatiquement : ${choices.join(" → ")}.`);
  } else {
    lines.push("Aucun autre fournisseur configuré n’est actuellement disponible.");
  }
  lines.push("Changer de modèle chez le même fournisseur ne contourne généralement pas une limite partagée.");
  bubble.textContent = lines.join("\n");
}

function shortCommit(value) {
  return value ? value.slice(0, 8) : "indisponible";
}

function renderGitReport(report, runId) {
  if (!report?.available) return;
  const viewport = document.querySelector(".conversation");
  const follow = shouldFollow(viewport);
  const card = document.createElement("section");
  card.className = "git-report";
  const headChanged = report.head_before !== report.head_after;
  const devChanged = report.origin_dev_before !== report.origin_dev_after;
  const integration = report.origin_dev_integrated === null
    ? "état de dev inconnu"
    : report.origin_dev_integrated
      ? "origin/dev est intégré"
      : "origin/dev n’est pas intégré";
  card.innerHTML = `
    <header>
      <div><span class="eyebrow">Git · vérification indépendante</span><strong>${escapeHtml(report.branch || "HEAD détachée")}</strong></div>
      <span class="change-status">Conservées par défaut</span>
    </header>
    <div class="git-facts">
      <span>HEAD <b>${shortCommit(report.head_before)} → ${shortCommit(report.head_after)}</b>${headChanged ? " · modifié" : " · inchangé"}</span>
      <span>origin/dev <b>${shortCommit(report.origin_dev_before)} → ${shortCommit(report.origin_dev_after)}</b>${devChanged ? " · référence actualisée" : " · inchangé"} · ${report.fetch_observed ? "fetch observé" : "aucun fetch observé"}</span>
      <span>${integration}</span>
    </div>`;
  const summary = document.createElement("div");
  summary.className = "diff-summary";
  summary.innerHTML = `<strong>${report.files.length} fichier${report.files.length > 1 ? "s" : ""}</strong><span class="insertions">+${report.insertions}</span><span class="deletions">−${report.deletions}</span>`;
  card.appendChild(summary);
  if (report.files.length) {
    const list = document.createElement("div");
    list.className = "diff-files";
    for (const file of report.files) {
      const row = document.createElement("div");
      row.innerHTML = `<code>${escapeHtml(file.path)}</code><span class="insertions">+${file.insertions}</span><span class="deletions">−${file.deletions}</span>${file.preexisting ? '<small title="Déjà modifié avant la tâche">préexistant</small>' : ""}`;
      list.appendChild(row);
    }
    card.appendChild(list);
  }
  if (report.patch_preview) {
    const details = document.createElement("details");
    details.className = "diff-preview";
    const summaryNode = document.createElement("summary");
    summaryNode.textContent = "Voir le diff";
    const patch = document.createElement("pre");
    patch.textContent = report.patch_preview;
    details.append(summaryNode, patch);
    card.appendChild(details);
  }
  const actions = document.createElement("div");
  actions.className = "git-actions";
  const keep = document.createElement("button");
  keep.textContent = "Conserver";
  keep.onclick = () => {
    keep.disabled = true;
    keep.textContent = "Conservé";
    card.querySelector(".change-status").textContent = "Acceptées";
  };
  const rejectButton = document.createElement("button");
  rejectButton.className = "reject-changes";
  rejectButton.textContent = "Rejeter";
  rejectButton.disabled = !report.rejectable || !runId;
  rejectButton.title = report.rejectable
    ? "Restaurer exactement l’état précédant cette tâche"
    : report.reject_reason || "Restauration automatique indisponible";
  rejectButton.onclick = async () => {
    if (!window.confirm("Rejeter toutes les modifications de fichiers attribuées à cette tâche ?")) return;
    const response = await fetch(`/api/runs/${runId}/reject`, { method: "POST" });
    const payload = await response.json();
    if (!response.ok) {
      window.alert(payload.message);
      return;
    }
    rejectButton.disabled = true;
    keep.disabled = true;
    card.querySelector(".change-status").textContent = "Rejetées et restaurées";
  };
  actions.append(keep, rejectButton);
  if (!report.rejectable && report.files.length) {
    const reason = document.createElement("small");
    reason.textContent = report.reject_reason;
    actions.appendChild(reason);
  }
  card.appendChild(actions);
  $("messages").appendChild(card);
  scrollIfFollowing(viewport, follow);
}

function updateCapabilityMenus() {
  const provider = $("agent").value;
  const capability = state.capabilities[provider];
  setOptions($("model"), [{ id: "", label: "Défaut du fournisseur" }]);
  setOptions($("effort"), [{ id: "", label: "Défaut du modèle" }]);
  setOptions($("execution-mode"), [{ id: "", label: "Automatique" }]);
  if (!capability) {
    for (const control of [$("model"), $("effort"), $("execution-mode")]) {
      control.disabled = true;
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
  setOptions($("effort"), [{ id: "", label: selectedModel?.default_effort ? `Défaut · ${selectedModel.default_effort}` : "Défaut du modèle" }]);
  addOptions($("effort"), efforts.map(value => ({ id: value, label: value })));
  $("effort").disabled = !efforts.length;
}

function currentSettings() {
  return {
    agent: $("agent").value,
    mode: $("mode").value,
    model: $("model").value,
    effort: $("effort").value,
    execution_mode: $("execution-mode").value
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
}

async function saveSettings() {
  if (!state.activeConversationId) return;
  await fetch(`/api/conversations/${state.activeConversationId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ settings: currentSettings() })
  });
}

function setOptions(select, items) {
  select.replaceChildren();
  addOptions(select, items);
}

function addOptions(select, items) {
  for (const item of items) {
    const option = document.createElement("option");
    option.value = item.id;
    option.textContent = item.label || item.id;
    select.appendChild(option);
  }
}

async function loadConversations(selectFirst = true) {
  [state.conversations, state.projects] = await Promise.all([
    fetch("/api/conversations").then(response => response.json()),
    fetch("/api/projects").then(response => response.json())
  ]);
  if (!state.conversations.length) {
    const created = await createConversation(false);
    state.conversations = [created];
  }
  renderConversations();
  if (selectFirst && !state.activeConversationId) {
    await selectConversation(state.conversations[0].id);
  }
}

function renderConversations() {
  const target = $("conversations");
  target.replaceChildren();
  for (const project of state.projects) {
    const group = document.createElement("section");
    group.className = "project-group";
    const header = document.createElement("div");
    header.className = "project-group-head";
    header.innerHTML = `<strong>${escapeHtml(project.name)}</strong>`;
    const projectActions = document.createElement("div");
    const editProject = smallButton("⚙", "Modifier le contexte du sous-projet", () => openProject(project));
    const addConversation = smallButton("＋", "Nouvelle conversation dans ce sous-projet", () => createConversation(true, project.id));
    projectActions.append(editProject, addConversation);
    header.appendChild(projectActions);
    group.appendChild(header);
    const conversations = state.conversations.filter(item => item.project_id === project.id);
    for (const conversation of conversations) {
    const row = document.createElement("div");
    row.className = `conversation-item ${conversation.id === state.activeConversationId ? "active" : ""}`;
    const button = document.createElement("button");
    button.className = "history-item";
    button.innerHTML = `<strong>${escapeHtml(conversation.title)}</strong><span>${state.runs.has(conversation.id) ? "● En cours" : `${conversation.messages.length} messages`}</span>`;
    button.onclick = () => selectConversation(conversation.id);
    const pin = document.createElement("button");
    pin.className = `pin-button ${conversation.pinned ? "pinned" : ""}`;
    pin.title = conversation.pinned ? "Désépingler" : "Épingler";
    pin.textContent = conversation.pinned ? "★" : "☆";
    pin.onclick = () => togglePin(conversation);
    const rename = document.createElement("button");
    rename.className = "pin-button";
    rename.title = "Renommer";
    rename.textContent = "✎";
    rename.onclick = () => renameConversation(conversation);
    const remove = document.createElement("button");
    remove.className = "pin-button delete-button";
    remove.title = "Supprimer";
    remove.textContent = "×";
    remove.onclick = () => confirmDeleteConversation(conversation);
    row.append(button, rename, pin, remove);
    group.appendChild(row);
    }
    target.appendChild(group);
  }
}

function smallButton(label, title, action) {
  const button = document.createElement("button");
  button.className = "project-action";
  button.textContent = label;
  button.title = title;
  button.onclick = action;
  return button;
}

async function selectConversation(conversationId) {
  preserveActivePanel();
  const conversation = await fetch(`/api/conversations/${conversationId}`).then(response => response.json());
  state.activeConversationId = conversationId;
  state.activeProjectId = conversation.project_id || "main";
  renderConversations();
  clearConversation();
  $("conversation-title").textContent = conversation.title;
  for (const message of conversation.messages) {
    if (message.role === "user") {
      addMessage("Toi", message.content, "user");
    } else {
      const bubble = addMessage("Joe · synthèse", "", "assistant");
      renderMarkdown(bubble, message.content);
      if (message.git_report) renderGitReport(message.git_report, message.run_id);
    }
  }
  if (!conversation.messages.length) {
    $("messages").innerHTML = '<div class="empty-state"><span class="empty-mark">J</span><h3>Nouvelle conversation</h3><p>Les réglages et l’historique de cette conversation resteront indépendants.</p></div>';
  }
  const conversationViewport = document.querySelector(".conversation");
  conversationViewport.scrollTop = conversationViewport.scrollHeight;
  applySettings(conversation.settings || {});
  restoreConversationPanel(conversationId);
  const running = state.runs.has(conversationId);
  $("send").disabled = running;
  $("stop").classList.toggle("hidden", !running);
  $("run-state").textContent = running ? "En cours" : "Prêt";
  $("run-state").className = `run-state ${running ? "running" : "idle"}`;
  if (running) {
    const bubble = addMessage("Joe", "Cette tâche continue en arrière-plan…", "assistant");
    state.runs.get(conversationId).bubble = bubble;
  }
}

function preserveActivePanel() {
  if (!state.activeConversationId) return;
  state.panels.set(state.activeConversationId, {
    agentNodes: [...$("agents").children],
    agents: state.agents,
    rawLog: $("raw-log").textContent,
    runState: $("run-state").textContent,
    runStateClass: $("run-state").className
  });
}

function restoreConversationPanel(conversationId) {
  const panel = state.panels.get(conversationId);
  if (!panel) {
    state.agents = new Map();
    $("agents").replaceChildren();
    $("raw-log").textContent = "";
    return;
  }
  state.agents = panel.agents;
  $("agents").replaceChildren(...panel.agentNodes);
  $("raw-log").textContent = panel.rawLog;
  $("run-state").textContent = panel.runState;
  $("run-state").className = panel.runStateClass;
}

async function createConversation(select = true, projectId = state.activeProjectId) {
  const conversation = await fetch("/api/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ project_id: projectId })
  }).then(response => response.json());
  if (select) {
    await loadConversations(false);
    await selectConversation(conversation.id);
  }
  return conversation;
}

async function renameConversation(conversation) {
  const title = window.prompt("Nouveau nom de la conversation :", conversation.title);
  if (!title?.trim()) return;
  await fetch(`/api/conversations/${conversation.id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: title.trim() })
  });
  await loadConversations(false);
  if (conversation.id === state.activeConversationId) $("conversation-title").textContent = title.trim();
}

function confirmDeleteConversation(conversation) {
  state.deletingConversation = conversation;
  $("delete-conversation-name").textContent = conversation.title;
  $("delete-conversation-dialog").showModal();
}

async function deleteConversation(event) {
  event.preventDefault();
  const conversation = state.deletingConversation;
  if (!conversation) return;
  const response = await fetch(`/api/conversations/${conversation.id}`, {
    method: "DELETE"
  });
  if (!response.ok) {
    const payload = await response.json();
    $("delete-conversation-dialog").close();
    window.alert(payload.error || "La conversation n’a pas pu être supprimée.");
    return;
  }
  $("delete-conversation-dialog").close();
  state.deletingConversation = null;
  if (conversation.id === state.activeConversationId) {
    state.activeConversationId = null;
  }
  await loadConversations(false);
  const next = state.conversations.find(
    item => item.project_id === conversation.project_id
  ) || state.conversations[0];
  if (next) await selectConversation(next.id);
}

function createProject() {
  state.editingProjectId = null;
  $("project-dialog-title").textContent = "Nouveau sous-projet";
  $("save-project").textContent = "Créer";
  $("project-name").value = "";
  $("project-context").value = "";
  $("project-dialog").showModal();
  requestAnimationFrame(() => $("project-name").focus());
}

function openProject(project) {
  state.editingProjectId = project.id;
  $("project-dialog-title").textContent = "Modifier le sous-projet";
  $("save-project").textContent = "Enregistrer";
  $("project-name").value = project.name;
  $("project-context").value = project.context || "";
  $("project-dialog").showModal();
}

async function saveProject(event) {
  event.preventDefault();
  if (!$("project-name").reportValidity()) return;
  const creating = !state.editingProjectId;
  const response = await fetch(
    creating ? "/api/projects" : `/api/projects/${state.editingProjectId}`,
    {
    method: creating ? "POST" : "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: $("project-name").value,
      context: $("project-context").value
    })
  });
  const project = await response.json();
  if (creating && $("project-context").value) {
    await fetch(`/api/projects/${project.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ context: $("project-context").value })
    });
  }
  $("project-dialog").close();
  state.activeProjectId = project.id;
  if (creating) {
    await createConversation(true, project.id);
  } else {
    await loadConversations(false);
  }
}

async function togglePin(conversation) {
  await fetch(`/api/conversations/${conversation.id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pinned: !conversation.pinned })
  });
  await loadConversations(false);
}

function clearConversation() { $("messages").replaceChildren(); }

function addMessage(label, text, kind) {
  const viewport = document.querySelector(".conversation");
  const follow = shouldFollow(viewport);
  const wrapper = document.createElement("div");
  wrapper.className = `message ${kind}`;
  const title = document.createElement("div");
  title.className = "message-label";
  title.textContent = label;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  wrapper.append(title, bubble);
  $("messages").appendChild(wrapper);
  scrollIfFollowing(viewport, follow);
  return bubble;
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
  card.innerHTML = `<div class="agent-head"><span class="agent-name">${escapeHtml(name)}</span><span class="agent-status">En attente</span></div><div class="agent-activity"></div><pre class="agent-output"></pre>`;
  $("agents").appendChild(card);
  const agent = {
    card,
    status: card.querySelector(".agent-status"),
    activity: card.querySelector(".agent-activity"),
    output: card.querySelector(".agent-output")
  };
  state.agents.set(name, agent);
  return agent;
}

function handleEvent(conversationId, event, finalBubble) {
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
    }
    return;
  }
  finalBubble = state.runs.get(conversationId)?.bubble || finalBubble;
  const conversationViewport = document.querySelector(".conversation");
  const followConversation = shouldFollow(conversationViewport);
  const diagnostics = $("raw-log");
  const followDiagnostics = shouldFollow(diagnostics);
  $("raw-log").textContent += `${JSON.stringify(event)}\n`;
  scrollIfFollowing(diagnostics, followDiagnostics);
  if (event.type === "route") {
    showRoute(event.mode, event.primary, event.reviewer);
    ensureAgent(event.primary);
    if (event.reviewer) ensureAgent(event.reviewer);
    const quotaSwitch = event.reason?.includes("quota-switch=");
    const quotaDetail = quotaSwitch ? event.reason.split("; ").at(-1) : "";
    const execution = [event.model, event.effort ? `effort ${event.effort}` : ""].filter(Boolean).join(" · ");
    finalBubble.textContent = `${event.mode.toUpperCase()} · ${capitalize(event.primary)} sélectionné${event.reviewer ? ` · revue par ${capitalize(event.reviewer)}` : ""}${execution ? ` · ${execution}` : ""}${quotaSwitch ? `\nBascule automatique : ${quotaDetail}.` : ""}\nDémarrage de l’agent…`;
  } else if (event.type === "provider_start") {
    const agent = ensureAgent(event.provider);
    agent.card.classList.add("active");
    agent.status.textContent = "Démarrage";
    finalBubble.textContent = `${capitalize(event.provider)} démarre…`;
  } else if (event.type === "activity") {
    const agent = ensureAgent(event.provider);
    agent.status.textContent = event.label;
    const signature = `${event.label}\n${event.detail || ""}`;
    const previous = agent.activity.lastElementChild;
    if (previous?.dataset.signature === signature) return;
    const row = document.createElement("div");
    row.className = "activity-row";
    row.dataset.signature = signature;
    row.innerHTML = `<i></i><div><strong>${escapeHtml(event.label)}</strong>${event.detail ? `<span>${escapeHtml(event.detail)}</span>` : ""}</div>`;
    const followActivity = shouldFollow(agent.activity);
    agent.activity.appendChild(row);
    while (agent.activity.children.length > 12) agent.activity.firstElementChild.remove();
    scrollIfFollowing(agent.activity, followActivity);
    finalBubble.textContent = `${capitalize(event.provider)} · ${event.label}`;
  } else if (event.type === "stream") {
    const agent = ensureAgent(event.provider);
    const followOutput = shouldFollow(agent.output);
    agent.output.textContent += event.text;
    scrollIfFollowing(agent.output, followOutput);
  } else if (event.type === "provider_end") {
    const agent = ensureAgent(event.provider);
    agent.card.classList.remove("active");
    agent.status.textContent = event.ok ? "Terminé" : `Échec · ${event.error || "inconnu"}`;
  } else if (event.type === "quota_notice") {
    showQuotaNotice(event);
  } else if (event.type === "git_report") {
    renderGitReport(event, event.run_id);
  } else if (event.type === "complete") {
    renderMarkdown(finalBubble, event.response);
    finishRun(conversationId, true);
    loadConversations(false).then(() => selectConversation(conversationId));
  } else if (event.type === "error") {
    finalBubble.textContent = `Erreur : ${event.message}`;
    finishRun(conversationId, false);
    loadConversations(false).then(() => selectConversation(conversationId));
  } else if (event.type === "cancelled") {
    const prompt = state.runs.get(conversationId)?.request || "";
    finishRun(conversationId, false);
    $("request").value = prompt;
    $("run-state").textContent = "Interrompu";
    loadConversations(false).then(() => selectConversation(conversationId));
  }
  scrollIfFollowing(conversationViewport, followConversation);
}

function finishRun(conversationId, ok) {
  state.runs.delete(conversationId);
  $("send").disabled = false;
  $("stop").classList.add("hidden");
  $("stop").disabled = false;
  $("stop").querySelector("span").textContent = "Interrompre";
  $("run-state").textContent = ok ? "Terminé" : "Échec";
  $("run-state").className = `run-state ${ok ? "done" : "idle"}`;
}

async function startRun(request) {
  const conversationId = state.activeConversationId;
  if (!conversationId || state.runs.has(conversationId)) return;
  state.agents.clear();
  $("agents").replaceChildren();
  $("raw-log").textContent = "";
  $("send").disabled = true;
  $("stop").classList.remove("hidden");
  $("run-state").textContent = "En cours";
  $("run-state").className = "run-state running";
  addMessage("Toi", request, "user");
  const finalBubble = addMessage("Joe · synthèse", "Routage local en cours…", "assistant");
  let model = $("model").value;
  if (model === "__custom__") {
    model = window.prompt("Identifiant exact du modèle :") || "";
  }
  const response = await fetch("/api/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      request,
      conversation_id: conversationId,
      agent: $("agent").value,
      mode: $("mode").value,
      model,
      effort: $("effort").value,
      execution_mode: $("execution-mode").value
    })
  });
  if (!response.ok) {
    const error = await response.json();
    finalBubble.textContent = `Erreur : ${error.error || response.statusText}`;
    finishRun(conversationId, false);
    return;
  }
  const { run_id } = await response.json();
  const stream = new EventSource(`/api/events/${run_id}`);
  state.runs.set(conversationId, { runId: run_id, stream, bubble: finalBubble, request });
  loadConversations(false);
  stream.onmessage = ({ data }) => {
    const event = JSON.parse(data);
    handleEvent(conversationId, event, finalBubble);
    if (event.type === "complete" || event.type === "error" || event.type === "cancelled") stream.close();
  };
  stream.onerror = () => {
    stream.close();
    if (state.runs.has(conversationId)) {
      finalBubble.textContent = "Connexion au flux interrompue. Consulte le journal technique.";
      finishRun(conversationId, false);
    }
  };
}

async function cancelActiveRun() {
  const conversationId = state.activeConversationId;
  const run = state.runs.get(conversationId);
  if (!run) return;
  $("stop").disabled = true;
  $("stop").querySelector("span").textContent = "Arrêt…";
  const response = await fetch(`/api/runs/${run.runId}/cancel`, { method: "POST" });
  if (!response.ok) {
    $("stop").disabled = false;
    $("stop").querySelector("span").textContent = "Interrompre";
  }
}

$("composer").addEventListener("submit", async event => {
  event.preventDefault();
  if (!state.activeConversationId || state.runs.has(state.activeConversationId)) return;
  const request = $("request").value.trim();
  if (!request) return;
  $("request").value = "";
  await startRun(request);
});
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
$("new-project").onclick = createProject;
$("cancel-project").onclick = () => {
  state.editingProjectId = null;
  $("project-dialog").close();
};
$("save-project").onclick = saveProject;
$("confirm-delete-conversation").onclick = deleteConversation;
$("stop").onclick = cancelActiveRun;
$("refresh-usage").onclick = loadUsage;

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

function renderMarkdown(target, source) {
  const lines = String(source || "").replace(/\r\n/g, "\n").split("\n");
  const html = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }
    if (line.trim().startsWith("```")) {
      const language = line.trim().slice(3).trim();
      const code = [];
      index += 1;
      while (index < lines.length && !lines[index].trim().startsWith("```")) {
        code.push(lines[index]);
        index += 1;
      }
      index += index < lines.length ? 1 : 0;
      html.push(`<pre><code${language ? ` data-language="${escapeHtml(language)}"` : ""}>${escapeHtml(code.join("\n"))}</code></pre>`);
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      const level = heading[1].length;
      html.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`);
      index += 1;
      continue;
    }
    if (index + 1 < lines.length && isTableSeparator(lines[index + 1])) {
      const headers = tableCells(line);
      index += 2;
      const rows = [];
      while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
        rows.push(tableCells(lines[index]));
        index += 1;
      }
      html.push(`<div class="table-scroll"><table><thead><tr>${headers.map(cell => `<th>${inlineMarkdown(cell)}</th>`).join("")}</tr></thead><tbody>${rows.map(row => `<tr>${headers.map((_, column) => `<td>${inlineMarkdown(row[column] || "")}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
      continue;
    }
    if (/^\s*[-*+]\s+/.test(line)) {
      const items = [];
      while (index < lines.length && /^\s*[-*+]\s+/.test(lines[index])) {
        items.push(lines[index].replace(/^\s*[-*+]\s+/, ""));
        index += 1;
      }
      html.push(`<ul>${items.map(item => `<li>${inlineMarkdown(item)}</li>`).join("")}</ul>`);
      continue;
    }
    if (/^\s*\d+\.\s+/.test(line)) {
      const items = [];
      while (index < lines.length && /^\s*\d+\.\s+/.test(lines[index])) {
        items.push(lines[index].replace(/^\s*\d+\.\s+/, ""));
        index += 1;
      }
      html.push(`<ol>${items.map(item => `<li>${inlineMarkdown(item)}</li>`).join("")}</ol>`);
      continue;
    }
    if (/^>\s?/.test(line)) {
      const quotes = [];
      while (index < lines.length && /^>\s?/.test(lines[index])) {
        quotes.push(lines[index].replace(/^>\s?/, ""));
        index += 1;
      }
      html.push(`<blockquote>${inlineMarkdown(quotes.join(" "))}</blockquote>`);
      continue;
    }
    if (/^---+$/.test(line.trim())) {
      html.push("<hr>");
      index += 1;
      continue;
    }
    const paragraph = [line];
    index += 1;
    while (index < lines.length && lines[index].trim() && !startsMarkdownBlock(lines, index)) {
      paragraph.push(lines[index]);
      index += 1;
    }
    html.push(`<p>${paragraph.map(inlineMarkdown).join("<br>")}</p>`);
  }
  target.innerHTML = html.join("");
  target.classList.add("markdown");
}

function startsMarkdownBlock(lines, index) {
  const line = lines[index];
  return /^(#{1,4})\s+/.test(line)
    || line.trim().startsWith("```")
    || /^\s*[-*+]\s+/.test(line)
    || /^\s*\d+\.\s+/.test(line)
    || /^>\s?/.test(line)
    || /^---+$/.test(line.trim())
    || (index + 1 < lines.length && isTableSeparator(lines[index + 1]));
}

function isTableSeparator(line) {
  return /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
}

function tableCells(line) {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(cell => cell.trim());
}

function inlineMarkdown(value) {
  const code = [];
  let text = escapeHtml(value).replace(/`([^`]+)`/g, (_, content) => {
    code.push(content);
    return `\u0000CODE${code.length - 1}\u0000`;
  });
  text = text
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/__([^_]+)__/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  return text.replace(/\u0000CODE(\d+)\u0000/g, (_, position) => `<code>${code[Number(position)]}</code>`);
}

setInterval(updateCountdowns, 1000);
setInterval(() => loadUsage().catch(() => {}), 60000);

Promise.all([loadStatus(), loadCapabilities(), loadUsage()]).then(() => loadConversations()).catch(error => {
  $("project").textContent = `Erreur : ${error.message}`;
});
