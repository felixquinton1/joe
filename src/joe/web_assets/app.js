const state = { running: false, agents: new Map(), capabilities: {}, usage: [] };
const $ = id => document.getElementById(id);

async function loadStatus() {
  const status = await fetch("/api/status").then(response => response.json());
  $("project").textContent = status.project;
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

async function loadHistory() {
  const history = await fetch("/api/history").then(response => response.json());
  const target = $("history");
  target.replaceChildren();
  if (!history.length) {
    target.innerHTML = '<p class="agent-status">Aucune exécution enregistrée</p>';
    return;
  }
  for (const item of history) {
    const button = document.createElement("button");
    button.className = "history-item";
    button.innerHTML = `<strong>${escapeHtml(item.request || "Sans titre")}</strong><span>${escapeHtml(item.route?.mode || "")} · ${escapeHtml(item.route?.primary || "")}</span>`;
    button.onclick = () => showHistory(item);
    target.appendChild(button);
  }
}

function showHistory(item) {
  clearConversation();
  addMessage("Toi", item.request, "user");
  const bubble = addMessage("Joe · historique", "", "assistant");
  renderMarkdown(bubble, item.final || "Aucune réponse enregistrée.");
  const route = item.route || {};
  showRoute(route.mode, route.primary, route.reviewer);
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

function handleEvent(event, finalBubble) {
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
    finishRun(true);
    loadHistory();
  } else if (event.type === "error") {
    finalBubble.textContent = `Erreur : ${event.message}`;
    finishRun(false);
  }
}

function finishRun(ok) {
  state.running = false;
  $("send").disabled = false;
  $("run-state").textContent = ok ? "Terminé" : "Échec";
  $("run-state").className = `run-state ${ok ? "done" : "idle"}`;
}

async function startRun(request) {
  state.running = true;
  state.agents.clear();
  $("agents").replaceChildren();
  $("raw-log").textContent = "";
  $("send").disabled = true;
  $("run-state").textContent = "En cours";
  $("run-state").className = "run-state running";
  clearConversation();
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
    finishRun(false);
    return;
  }
  const { run_id } = await response.json();
  const stream = new EventSource(`/api/events/${run_id}`);
  stream.onmessage = ({ data }) => {
    const event = JSON.parse(data);
    handleEvent(event, finalBubble);
    if (event.type === "complete" || event.type === "error") stream.close();
  };
  stream.onerror = () => {
    stream.close();
    if (state.running) {
      finalBubble.textContent = "Connexion au flux interrompue. Consulte le journal technique.";
      finishRun(false);
    }
  };
}

$("composer").addEventListener("submit", async event => {
  event.preventDefault();
  if (state.running) return;
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
$("agent").addEventListener("change", updateCapabilityMenus);
$("model").addEventListener("change", updateEfforts);
$("refresh-history").onclick = loadHistory;
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

Promise.all([loadStatus(), loadCapabilities(), loadHistory(), loadUsage()]).catch(error => {
  $("project").textContent = `Erreur : ${error.message}`;
});
