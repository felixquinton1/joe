window.createJoeUsage = function createJoeUsage({ state, $, escapeHtml, capitalize, addMessage }) {
  async function loadUsage(force = false) {
    const button = $("refresh-usage");
    if (force) {
      button.disabled = true;
      button.classList.add("refreshing");
    }
    try {
      const response = await fetch(`/api/usage${force ? "?force=1" : ""}`);
      if (!response.ok) throw new Error("Quotas indisponibles");
      state.usage = await response.json();
      renderUsage();
    } finally {
      if (force) {
        button.disabled = false;
        button.classList.remove("refreshing");
      }
    }
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
        for (const metric of provider.metrics || []) {
          const row = document.createElement("div");
          row.className = "usage-metric";
          row.innerHTML = `<span>${escapeHtml(metric.name)}</span><strong>${escapeHtml(metric.value)}</strong>`;
          card.appendChild(row);
        }
        if (provider.message) {
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

  return {
    formatPercent,
    loadUsage,
    renderUsage,
    resetDescription,
    showQuotaNotice,
    updateCountdowns,
    usageWindow
  };
};
