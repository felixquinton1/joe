window.createJoeUsage = function createJoeUsage({
  state, $, escapeHtml, capitalize, addMessage, translate, fetcher
}) {
  const tr = (key, params) => translate ? translate(key, params) : key;
  function usageDescription(usage) {
    const tokens = usage.total_tokens ?? ((usage.input_tokens || 0) + (usage.output_tokens || 0));
    // Older DeepSeek summaries were saved with a provider-specific status.
    const deepseekEstimate = usage.cost_status === "estimated_deepseek_api"
      || (usage.cost_status === "estimated_api" && (usage.cost_provider || usage.provider) === "deepseek");
    if (deepseekEstimate && typeof usage.cost_usd === "number") {
      return tr("deepseek_usage_estimate", { cost: usage.cost_usd.toFixed(6), tokens });
    }
    return tr("usage_tokens_count", { tokens });
  }

  function balanceView(provider) {
    const balance = provider.balance;
    const amount = balance?.amount ?? balance?.balance_usd;
    const currency = balance?.currency || "USD";
    const known = balance?.data_available === true && typeof amount === "number"
      && Number.isFinite(amount) && ["USD", "CNY"].includes(currency);
    const view = { text: tr("usage_balance_unavailable"), note: "" };
    if (known) {
      view.text = new Intl.NumberFormat(document.documentElement?.lang || "en", {
        style: "currency", currency, minimumFractionDigits: 2,
        maximumFractionDigits: amount > 0 && amount < 0.01 ? 6 : 2
      }).format(amount);
      if (balance.funds_sufficient === false) view.note = tr("usage_balance_insufficient");
    } else if (balance?.error_kind === "authentication") {
      view.note = tr("usage_balance_auth", { status: balance.http_status || 401 });
    } else if (balance?.error_kind === "credentials") {
      view.note = tr("usage_balance_credentials");
    } else {
      view.note = tr("usage_balance_retry");
    }
    return view;
  }

  async function loadUsage(force = false) {
    const button = $("refresh-usage");
    if (force) {
      button.disabled = true;
      button.classList.add("refreshing");
    }
    try {
      const response = await fetcher(`/api/usage${force ? "?force=1" : ""}`);
      if (!response.ok) throw new Error(tr("quotas_unavailable"));
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
      const metered = provider.quota_display === "token_billing";
      const card = document.createElement("article");
      card.className = ["usage-card", provider.available ? "" : "unavailable",
        metered ? "usage-card--metered" : "", provider.stale ? "stale" : ""]
        .filter(Boolean).join(" ");
      const plan = provider.plan ? `<span>${escapeHtml(provider.plan)}</span>` : "";
      const label = provider.label || provider.provider;
      card.innerHTML = `<header><strong>${escapeHtml(label)}</strong>${plan}</header>`;
      let balance;
      if (metered) {
        balance = balanceView(provider);
        const row = document.createElement("div");
        row.className = "usage-balance";
        row.innerHTML = `<span>${escapeHtml(tr("usage_balance_label"))}</span><strong>${escapeHtml(balance.text)}</strong>`;
        card.appendChild(row);
      }
      if (!provider.available) {
        const message = document.createElement("p");
        message.textContent = provider.message;
        card.appendChild(message);
      } else {
        for (const window of provider.windows || []) card.appendChild(usageWindow(window));
        for (const metric of provider.metrics || []) {
          const row = document.createElement("div");
          row.className = "usage-metric";
          row.innerHTML = `<span>${escapeHtml(metric.name)}</span><strong>${escapeHtml(metric.value)}</strong>`;
          card.appendChild(row);
        }
        const note = balance?.note || provider.message;
        if (note) {
          const message = document.createElement("p");
          message.textContent = note;
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
      <div class="usage-line"><span>${escapeHtml(window.name)}</span><strong>${formatPercent(remaining)} ${tr("remaining")}</strong></div>
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
        node.textContent = tr("reset_unknown");
        continue;
      }
      const seconds = Math.max(0, reset - Date.now() / 1000);
      const days = Math.floor(seconds / 86400);
      const hours = Math.floor((seconds % 86400) / 3600);
      const minutes = Math.floor((seconds % 3600) / 60);
      node.textContent = seconds <= 0
        ? tr("reset_imminent")
        : tr("reset_in", { duration: `${days ? `${days} ${tr("day_short")} ` : ""}${hours ? `${hours} h ` : ""}${minutes} min` });
    }
  }

  function resetDescription(timestamp) {
    const reset = Number(timestamp);
    if (!reset) return tr("return_time_unknown");
    const seconds = Math.max(0, reset - Date.now() / 1000);
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const remaining = seconds <= 0
      ? tr("reset_imminent").toLocaleLowerCase()
      : tr("in_duration", { duration: `${days ? `${days} ${tr("day_short")} ` : ""}${hours ? `${hours} h ` : ""}${minutes} min` });
    const date = new Date(reset * 1000).toLocaleString(document.documentElement.lang, {
      dateStyle: "short",
      timeStyle: "short"
    });
    return `${remaining} (${date})`;
  }

  function showQuotaNotice(event) {
    const bubble = addMessage(null, "", "notice", {
      labelKey: "joe_limit_reached"
    });
    const lines = [tr("usage_limit_reached", { provider: capitalize(event.provider) })];
    if (event.windows?.length) {
      for (const window of event.windows) {
        lines.push(`• ${window.name} : ${resetDescription(window.resets_at)}`);
      }
    } else {
      lines.push(`• ${event.usage_message || tr("cli_reset_unknown")}`);
    }
    if (event.alternatives?.length) {
      const choices = event.alternatives.map(item => {
        const models = item.models?.length ? ` (${item.models.join(", ")})` : "";
        return `${capitalize(item.provider)}${models}`;
      });
      lines.push(tr("automatic_fallbacks", { choices: choices.join(" → ") }));
    } else {
      lines.push(tr("no_fallback_available"));
    }
    lines.push(tr("shared_limit_warning"));
    bubble.textContent = lines.join("\n");
  }

  return {
    usageDescription,
    balanceView,
    formatPercent,
    loadUsage,
    renderUsage,
    resetDescription,
    showQuotaNotice,
    updateCountdowns,
    usageWindow
  };
};
