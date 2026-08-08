window.createAutomationModule = ({ state, $, fetcher }) => {
  let plans = [];
  let campaigns = [];
  // Rappel de celui qui a ouvert le formulaire : un plan confié au
  // planificateur ne doit plus rester affiché comme « à valider ».
  let onScheduled = null;

  const formatDate = value => value
    ? new Date(Number(value) * 1000).toLocaleString()
    : "Maintenant";

  function activeProject() {
    const conversation = state.conversations.find(
      item => item.id === state.activeConversationId
    );
    return state.projects.find(item => item.id === conversation?.project_id);
  }

  function render() {
    const target = $("automation-list");
    target.replaceChildren();
    for (const plan of plans.slice(0, 12)) {
      const card = document.createElement("article");
      card.className = `automation-card ${plan.status}`;
      const completed = (plan.steps || []).filter(
        step => step.status === "completed"
      ).length;
      card.innerHTML = `
        <div><strong></strong><span></span></div>
        <small></small>
        <button type="button">Annuler</button>`;
      card.querySelector("strong").textContent = plan.title;
      card.querySelector("span").textContent = plan.status;
      card.querySelector("small").textContent =
        `${completed}/${plan.steps.length} étapes · ${formatDate(plan.scheduled_for)}`;
      const button = card.querySelector("button");
      button.hidden = ["completed", "cancelled", "blocked"].includes(plan.status);
      button.onclick = async () => {
        await fetcher(`/api/automations/${plan.id}/cancel`, { method: "POST" });
        await load();
      };
      target.appendChild(card);
    }
    if (!plans.length) {
      target.innerHTML = "<small>Aucun plan programmé.</small>";
    }
    const autonomousTarget = $("autonomous-list");
    autonomousTarget.replaceChildren();
    for (const campaign of campaigns.slice(0, 8)) {
      const card = document.createElement("article");
      card.className = `automation-card ${campaign.status}`;
      card.innerHTML = `<div><strong></strong><span></span></div><small></small><button type="button">Annuler</button>`;
      card.querySelector("strong").textContent = campaign.title;
      card.querySelector("span").textContent = campaign.status;
      card.querySelector("small").textContent = `itération ${campaign.iteration}/${campaign.max_iterations} · phase ${campaign.phase}`;
      if (campaign.status === "paused" && campaign.next_start_at) {
        card.querySelector("small").textContent += ` · reprise ${formatDate(campaign.next_start_at)}`;
      }
      const button = card.querySelector("button");
      button.hidden = ["completed", "cancelled", "blocked"].includes(campaign.status);
      button.onclick = async () => {
        await fetcher(`/api/autonomous/${campaign.id}/cancel`, { method: "POST" });
        await load();
      };
      autonomousTarget.appendChild(card);
    }
    if (!campaigns.length) autonomousTarget.innerHTML = "<small>Aucune campagne Autonomous.</small>";
  }

  async function load() {
    const response = await fetcher("/api/automations");
    if (!response.ok) return;
    plans = await response.json();
    const autonomousResponse = await fetcher("/api/autonomous");
    campaigns = autonomousResponse.ok ? await autonomousResponse.json() : [];
    render();
  }

  async function startParkinsons() {
    if (!state.activeConversationId) return;
    const scheduled = $("autonomous-schedule-enabled").checked;
    const days = $("autonomous-window-days").value === "weekdays"
      ? [0, 1, 2, 3, 4]
      : [0, 1, 2, 3, 4, 5, 6];
    const response = await fetcher("/api/autonomous", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: "DaT Parkinson — démo synthétique",
        conversation_id: state.activeConversationId,
        objective: "Améliorer une baseline reproductible de classification binaire synthétique inspirée d'images DaT en minimisant la log loss, sans données restreintes.",
        research_protocol: "Consulter les pages publiques officielles du challenge, documenter métrique, format et contraintes, puis rechercher des méthodes publiques comparables avec leurs sources.",
        data_policy: "Ne jamais ouvrir, joindre, recopier ou envoyer les scans et fichiers restreints de DrivenData à une IA. Seulement code, données synthétiques, métriques agrégées et logs nettoyés.",
        command: ["python", "experiment.py"],
        working_directory: "templates/autonomous/dat-parkinsons",
        metrics_path: "metrics.json",
        metric_name: "log_loss",
        metric_direction: "min",
        timeout_seconds: 480,
        max_iterations: 20,
        max_duration_seconds: Number($("autonomous-budget-minutes").value) * 60,
        restricted_data: true,
        schedule: {
          timezone: "Europe/Paris",
          windows: scheduled ? [{
            days,
            start: $("autonomous-window-start").value,
            end: $("autonomous-window-end").value
          }] : []
        },
        checkpoint_path: "checkpoints/latest.pt",
        resume_command: ["python", "experiment.py", "--resume", "checkpoints/latest.pt"],
        stop_signal_path: "artifacts/STOP_REQUESTED",
        stop_grace_seconds: 30,
        mode: "review",
        execution_mode: "workspace-write"
      })
    });
    const payload = await response.json();
    if (!response.ok) return window.alert(payload.error || "Impossible de démarrer Autonomous.");
    await load();
  }

  async function open(prefill = {}) {
    const project = activeProject();
    if (!project || !state.activeConversationId) {
      window.alert("Choisis d’abord une conversation.");
      return;
    }
    $("automation-project").textContent = project.name;
    $("automation-provider").value = project.quota_provider || "";
    $("automation-title").value = prefill.title || "";
    $("automation-steps").value = (prefill.steps || []).join("\n");
    $("automation-when").value = "";
    $("automation-start").value = "now";
    onScheduled = prefill.onScheduled || null;
    syncStartFields();
    await load();
    $("automation-dialog").showModal();
  }

  // La date n'a de sens que pour un départ daté : « au rechargement des quotas »
  // se résout côté serveur, l'échéance n'étant pas toujours publiée d'avance.
  function syncStartFields() {
    $("automation-when-label").hidden = $("automation-start").value !== "at";
  }

  async function save(event) {
    event.preventDefault();
    const project = activeProject();
    if (!project) return;
    const provider = $("automation-provider").value;
    const preference = await fetcher(`/api/projects/${project.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ quota_provider: provider, quota_automation: true })
    });
    if (!preference.ok) {
      window.alert("Impossible d’enregistrer la stratégie de quotas.");
      return;
    }
    project.quota_provider = provider;
    const steps = $("automation-steps").value
      .split(/\n+/)
      .map(step => step.replace(/^\s*(?:[-*]|\d+[.)])\s*/, "").trim())
      .filter(Boolean);
    const start = $("automation-start").value;
    const when = $("automation-when").value;
    const response = await fetcher("/api/automations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: $("automation-title").value,
        conversation_id: state.activeConversationId,
        steps,
        start_mode: start === "quota_reset" ? "quota_reset" : "at",
        scheduled_for: start === "at" && when
          ? new Date(when).getTime() / 1000
          : Date.now() / 1000,
        mode: $("automation-mode").value,
        execution_mode: $("automation-execution").value,
        max_retries: Number($("automation-retries").value),
        auto_integrate: $("automation-integrate").checked
      })
    });
    const payload = await response.json();
    if (!response.ok) {
      window.alert(payload.error || "Impossible de programmer ce plan.");
      return;
    }
    $("automation-title").value = "";
    $("automation-steps").value = "";
    if (onScheduled) {
      const notify = onScheduled;
      onScheduled = null;
      await notify();
    }
    await load();
  }

  return { load, open, save, syncStartFields, startParkinsons };
};
