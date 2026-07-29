const APP_VERSION = "0.22.3";
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

async function loadStatus() {
  const status = await fetch("/api/status").then(response => response.json());
  updateProviderMenu(status.provider_catalog || status.providers || []);
  $("project").textContent = status.project;
  $("version").textContent = status.version || "ancienne version";
  if (status.version !== APP_VERSION) {
    const warning = $("restart-warning");
    warning.textContent = `Le serveur Joe ${status.version || "actuel"} utilise encore un ancien backend. Arrête-le avec Ctrl+C, relance joe, puis recharge cette page.`;
    warning.classList.remove("hidden");
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
    [{ id: "", label: "Automatique" }, ...items]
  );
  if ([...$("agent").options].some(option => option.value === selected)) {
    $("agent").value = selected;
  }
}

async function loadCapabilities() {
  state.capabilities = await fetch("/api/capabilities").then(response => response.json());
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
      <span class="change-status">Conservées</span>
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
    const response = await fetch(`/api/runs/${runId}/reject`, {
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

function renderWorkflowUpdate(event, finalBubble, runId) {
  if (!finalBubble) return;
  const message = finalBubble.closest(".message");
  let progress = document.querySelector(`.workflow-progress[data-run="${runId}"]`);
  if (!progress) {
    progress = document.createElement("section");
    progress.className = "workflow-progress";
    progress.dataset.run = runId;
    progress.innerHTML = `<header><span class="eyebrow">${event.mode === "consensus" ? "Consensus en cours" : "Implémentation contrôlée"}</span><strong>${event.mode === "consensus" ? "Avis et examens croisés" : "Réalisation, revue et correction"}</strong></header><div class="workflow-stages"></div>`;
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
    eyebrow.textContent = summary.route?.mode === "consensus"
      ? "Consensus terminé"
      : "Implémentation contrôlée terminée";
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
  addMessage
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
  renderWorkflowUpdate,
  renderPromptQueue
}));

function handleEvent(conversationId, event, finalBubble) {
  const activeRun = state.runs.get(conversationId);
  if (event.type === "route" && activeRun) activeRun.mode = event.mode;
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
    ensureAgent(event.primary);
    if (event.reviewer) ensureAgent(event.reviewer);
    setSummaryPending(
      finalBubble,
      ["consensus", "review"].includes(event.mode)
    );
    const quotaSwitch = event.reason?.includes("quota-switch=");
    const quotaDetail = quotaSwitch ? event.reason.split("; ").at(-1) : "";
    const execution = [event.model, event.effort ? `effort ${event.effort}` : "", event.execution_mode ? `permission ${event.execution_mode}` : ""].filter(Boolean).join(" · ");
    finalBubble.textContent = ["consensus", "review"].includes(event.mode)
      ? "Synthèse finale en attente…"
      : `Routage local terminé${Number.isFinite(event.routing_ms) ? ` en ${event.routing_ms} ms` : ""}.\n${event.mode.toUpperCase()} · ${capitalize(event.primary)} ${event.health_check ? "effectue un test minimal" : "répond"}${event.reviewer ? ` · revue par ${capitalize(event.reviewer)}` : ""}${execution ? ` · ${execution}` : ""}${quotaSwitch ? `\nBascule automatique : ${quotaDetail}.` : ""}`;
  } else if (event.type === "provider_start") {
    const agent = ensureAgent(event.provider);
    agent.card.classList.add("active");
    agent.status.textContent = "Démarrage";
    const metadata = [
      event.model || "modèle par défaut",
      `effort ${event.effort || "défaut"}`
    ].filter(Boolean).join(" · ");
    const row = document.createElement("div");
    row.className = "activity-row provider-metadata";
    row.innerHTML = `<i></i><div><strong>${escapeHtml(capitalize(event.provider))}</strong><span>${escapeHtml(metadata)}</span></div>`;
    agent.activity.appendChild(row);
    updateWorkflowProviderMetadata(event.provider, metadata);
    if (!structuredWorkflow) {
      finalBubble.textContent = `${capitalize(event.provider)} · ${metadata}\nDémarrage…`;
    }
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
    if (!structuredWorkflow) finalBubble.textContent = `${capitalize(event.provider)} · ${event.label}`;
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
    agent.status.textContent = event.ok ? "Terminé" : `Échec · ${event.error || "inconnu"}`;
  } else if (event.type === "provider_fallback") {
    const agent = ensureAgent(event.provider);
    agent.card.classList.remove("active");
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
    renderMarkdown(finalBubble, event.response);
    finishRun(conversationId, true);
    loadConversations(false);
    launchNextQueued(conversationId);
  } else if (event.type === "error") {
    setSummaryPending(finalBubble, false);
    finalBubble.textContent = `Erreur : ${event.message}`;
    finishRun(conversationId, false);
    loadConversations(false).then(() => selectConversation(conversationId));
  } else if (event.type === "cancelled") {
    setSummaryPending(finalBubble, false);
    const prompt = state.runs.get(conversationId)?.request || "";
    finishRun(conversationId, false);
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
  $("send").querySelector("span").textContent = "Lancer";
  $("stop").classList.add("hidden");
  $("stop").disabled = false;
  $("stop").querySelector("span").textContent = "Interrompre";
  $("run-state").textContent = ok ? "Terminé" : "Échec";
  $("run-state").className = `run-state ${ok ? "done" : "idle"}`;
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
    execution_mode: $("execution-mode").value
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
  if (state.runs.has(conversationId)) {
    enqueueRequest(conversationId, request, settings);
    return;
  }
  const payload = {
    request,
    conversation_id: conversationId,
    ...settings
  };
  let response = await fetch("/api/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  if (response.status === 428) {
    const approved = await confirmFullAccess();
    if (!approved) return;
    response = await fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...payload, full_access_approved: true })
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
    $("send").querySelector("span").textContent = "Mettre en file";
    $("stop").classList.remove("hidden");
    $("run-state").textContent = "En cours";
    $("run-state").className = "run-state running";
    addMessage("Toi", request, "user");
  }
  const finalBubble = visible
    ? addMessage("Joe · synthèse", "Routage local en cours…", "assistant")
    : null;
  const { run_id } = await response.json();
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
    const response = await fetch("/api/runs/active");
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
    const response = await fetch(`/api/conversations/${conversationId}`);
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
  const runs = await fetch("/api/runs/active").then(response => response.json());
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
  $("stop").querySelector("span").textContent = "Arrêt…";
  const response = await fetch(`/api/runs/${run.runId}/cancel`, { method: "POST" });
  if (!response.ok) {
    $("stop").disabled = false;
    $("stop").querySelector("span").textContent = "Interrompre";
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
$("new-project").onclick = createProject;
$("cancel-project").onclick = () => {
  state.editingProjectId = null;
  $("project-dialog").close();
};
$("save-project").onclick = saveProject;
$("confirm-delete-conversation").onclick = deleteConversation;
$("stop").onclick = cancelActiveRun;
$("refresh-usage").onclick = () => loadUsage(true);

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
setupPanelResizers();
resizeComposer();
$("toggle-history").onclick = () => toggleMobilePanel(
  ".history-panel",
  "toggle-history"
);
$("toggle-activity").onclick = () => toggleMobilePanel(
  ".activity-panel",
  "toggle-activity"
);

Promise.all([loadStatus(), loadActiveRuns()])
  .then(() => loadConversations())
  .then(connectActiveRuns)
  .catch(error => {
    $("project").textContent = `Erreur : ${error.message}`;
  });

loadCapabilities().catch(() => {
  state.capabilities = {};
});
loadUsage().catch(() => {
  $("usage").innerHTML = '<span class="usage-loading">Quotas momentanément indisponibles</span>';
});
