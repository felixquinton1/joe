const APP_VERSION = "0.6.0";
const state = {
  agents: new Map(),
  capabilities: {},
  usage: [],
  conversations: [],
  activeConversationId: null,
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
    card.className = `usage-card ${provider.available ? "" : "unavailable"}`;
    const plan = provider.plan ? `<span>${escapeHtml(provider.plan)}</span>` : "";
    card.innerHTML = `<header><strong>${escapeHtml(provider.provider)}</strong>${plan}</header>`;
    if (!provider.available) {
      const message = document.createElement("p");
      message.textContent = provider.message;
      card.appendChild(message);
    } else {
      for (const window of provider.windows) card.appendChild(usageWindow(window));
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
  state.conversations = await fetch("/api/conversations").then(response => response.json());
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
  for (const conversation of state.conversations) {
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
    row.append(button, pin);
    target.appendChild(row);
  }
}

async function selectConversation(conversationId) {
  const conversation = await fetch(`/api/conversations/${conversationId}`).then(response => response.json());
  state.activeConversationId = conversationId;
  renderConversations();
  clearConversation();
  $("conversation-title").textContent = conversation.title;
  for (const message of conversation.messages) {
    if (message.role === "user") {
      addMessage("Toi", message.content, "user");
    } else {
      const bubble = addMessage("Joe · synthèse", "", "assistant");
      renderMarkdown(bubble, message.content);
    }
  }
  if (!conversation.messages.length) {
    $("messages").innerHTML = '<div class="empty-state"><span class="empty-mark">J</span><h3>Nouvelle conversation</h3><p>Les réglages et l’historique de cette conversation resteront indépendants.</p></div>';
  }
  applySettings(conversation.settings || {});
  state.agents.clear();
  $("agents").replaceChildren();
  $("raw-log").textContent = "";
  const running = state.runs.has(conversationId);
  $("send").disabled = running;
  $("run-state").textContent = running ? "En cours" : "Prêt";
  $("run-state").className = `run-state ${running ? "running" : "idle"}`;
  if (running) {
    const bubble = addMessage("Joe", "Cette tâche continue en arrière-plan…", "assistant");
    state.runs.get(conversationId).bubble = bubble;
  }
}

async function createConversation(select = true) {
  const conversation = await fetch("/api/conversations", { method: "POST" }).then(response => response.json());
  if (select) {
    await loadConversations(false);
    await selectConversation(conversation.id);
  }
  return conversation;
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
  wrapper.scrollIntoView({ behavior: "smooth", block: "end" });
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
    if (event.type === "complete" || event.type === "error") {
      state.runs.delete(conversationId);
      loadConversations(false);
    }
    return;
  }
  finalBubble = state.runs.get(conversationId)?.bubble || finalBubble;
  $("raw-log").textContent += `${JSON.stringify(event)}\n`;
  if (event.type === "route") {
    showRoute(event.mode, event.primary, event.reviewer);
    ensureAgent(event.primary);
    if (event.reviewer) ensureAgent(event.reviewer);
    finalBubble.textContent = `${event.mode.toUpperCase()} · ${capitalize(event.primary)} sélectionné${event.reviewer ? ` · revue par ${capitalize(event.reviewer)}` : ""}\nDémarrage de l’agent…`;
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
    agent.activity.appendChild(row);
    while (agent.activity.children.length > 12) agent.activity.firstElementChild.remove();
    agent.activity.scrollTop = agent.activity.scrollHeight;
    finalBubble.textContent = `${capitalize(event.provider)} · ${event.label}${event.detail ? `\n${event.detail}` : ""}`;
  } else if (event.type === "stream") {
    const agent = ensureAgent(event.provider);
    agent.output.textContent += event.text;
    agent.output.scrollTop = agent.output.scrollHeight;
  } else if (event.type === "provider_end") {
    const agent = ensureAgent(event.provider);
    agent.card.classList.remove("active");
    agent.status.textContent = event.ok ? "Terminé" : `Échec · ${event.error || "inconnu"}`;
  } else if (event.type === "complete") {
    renderMarkdown(finalBubble, event.response);
    finishRun(conversationId, true);
    loadConversations(false).then(() => selectConversation(conversationId));
  } else if (event.type === "error") {
    finalBubble.textContent = `Erreur : ${event.message}`;
    finishRun(conversationId, false);
    loadConversations(false).then(() => selectConversation(conversationId));
  }
}

function finishRun(conversationId, ok) {
  state.runs.delete(conversationId);
  $("send").disabled = false;
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
  state.runs.set(conversationId, { runId: run_id, stream, bubble: finalBubble });
  loadConversations(false);
  stream.onmessage = ({ data }) => {
    const event = JSON.parse(data);
    handleEvent(conversationId, event, finalBubble);
    if (event.type === "complete" || event.type === "error") stream.close();
  };
  stream.onerror = () => {
    stream.close();
    if (state.runs.has(conversationId)) {
      finalBubble.textContent = "Connexion au flux interrompue. Consulte le journal technique.";
      finishRun(conversationId, false);
    }
  };
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
$("new-conversation").onclick = () => createConversation(true);
$("refresh-usage").onclick = loadUsage;

function escapeHtml(value) {
  const node = document.createElement("span");
  node.textContent = value;
  return node.innerHTML;
}

function capitalize(value) {
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : "";
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
