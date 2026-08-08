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

  function journalLabel(event) {
    if (event.kind === "agent_step") {
      const actor = [event.provider, event.model].filter(Boolean).join(" / ") || "routage en cours";
      const mode = event.mode === "consensus" ? "consensus multi-IA" : event.mode || "une IA";
      const changes = event.files ? ` · ${event.files} fichier(s), +${event.insertions || 0}/-${event.deletions || 0}` : "";
      const attempts = (event.attempts || []).map(item => [item.provider, item.model].filter(Boolean).join("/")).filter(Boolean);
      const participants = attempts.length ? ` · appels ${attempts.join(", ")}` : "";
      return `${event.phase || "étape IA"} · ${actor} · ${mode} · ${event.status}${participants}${changes}`;
    }
    if (event.kind === "experiment") {
      const command = Array.isArray(event.command) ? event.command.join(" ") : "commande locale";
      const metrics = Object.entries(event.metrics || {}).map(([key, value]) => `${key}=${value}`).join(", ");
      return `expérience · ${command} · ${event.status} · ${event.duration_seconds || "?"} s${metrics ? ` · ${metrics}` : ""}`;
    }
    if (event.kind === "paused") return `pause planifiée · reprise ${formatDate(event.next_start_at)}`;
    return `${event.kind || "événement"} · ${event.status || "enregistré"}`;
  }

  function setView(view) {
    const autonomous = view === "autonomous";
    $("automation-tab-plan").classList.toggle("active", !autonomous);
    $("automation-tab-plan").setAttribute("aria-selected", String(!autonomous));
    $("automation-tab-autonomous").classList.toggle("active", autonomous);
    $("automation-tab-autonomous").setAttribute("aria-selected", String(autonomous));
    $("automation-pane-plan").hidden = autonomous;
    $("automation-pane-autonomous").hidden = !autonomous;
    $("save-automation").hidden = autonomous;
    $("start-parkinsons-autonomous").hidden = !autonomous;
  }

  function syncAutonomousSchedule() {
    const enabled = $("autonomous-schedule-enabled").checked;
    const grid = document.querySelector(".autonomous-window-grid");
    grid.classList.toggle("is-disabled", !enabled);
    for (const control of grid.querySelectorAll("input, select")) control.disabled = !enabled;
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
      const journal = document.createElement("details");
      journal.className = "autonomous-journal";
      const summary = document.createElement("summary");
      summary.textContent = `Journal détaillé · ${(campaign.history || []).length} événement(s)`;
      const list = document.createElement("ol");
      for (const event of (campaign.history || []).slice().reverse()) {
        const item = document.createElement("li");
        const when = document.createElement("b");
        when.textContent = formatDate(event.at);
        item.append(when, document.createTextNode(` · ${journalLabel(event)}`));
        list.appendChild(item);
      }
      if (!list.childNodes.length) {
        const item = document.createElement("li");
        item.textContent = "La campagne n'a pas encore démarré.";
        list.appendChild(item);
      }
      journal.append(summary, list);
      card.appendChild(journal);
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
        title: "DaT Parkinson — Autonomous",
        conversation_id: state.activeConversationId,
        objective: "Traiter de manière autonome le DaT Parkinson's Challenge à partir de ses pages et règles officielles. Concevoir, implémenter et évaluer localement des solutions; choisir les validations, indicateurs, comparaisons et visualisations utiles; fournir les fichiers au format demandé par le challenge, mais ne jamais effectuer de soumission.",
        research_protocol: "Commencer par consulter les pages publiques officielles du challenge et son leaderboard, puis effectuer la recherche bibliographique publique jugée utile. Expliquer les sources, options et raisons de chaque choix dans la conversation.",
        data_policy: "Les données DrivenData restent exclusivement sur ce PC. Ne jamais transmettre de scan, ligne individuelle, identifiant, métadonnée privée ou extrait de fichier à une IA. Les processus locaux peuvent lire les données; les IA ne reçoivent que du code, de la documentation publique, des métriques agrégées et des erreurs nettoyées.",
        campaign_context: `Challenge officiel : DaT Parkinson's Challenge de DrivenData.
Pages publiques à consulter :
- accueil : https://www.drivendata.org/competitions/311/dat-parkinsons-challenge/page/987/
- description : https://www.drivendata.org/competitions/311/dat-parkinsons-challenge/page/990/
- format d'exécution : https://www.drivendata.org/competitions/311/dat-parkinsons-challenge/page/989/
- règles : https://www.drivendata.org/competitions/311/dat-parkinsons-challenge/rules/
- leaderboard : https://www.drivendata.org/competitions/311/dat-parkinsons-challenge/leaderboard/
- données : https://www.drivendata.org/competitions/311/dat-parkinsons-challenge/data/
Les données privées sont déjà disponibles uniquement pour les processus locaux via DAT_PARKINSON_DATA_ROOT. N'en lis jamais le contenu dans une session IA : écris des scripts locaux qui ne renvoient que des agrégats nettoyés.
Environnement : Windows, GPU NVIDIA RTX A5000 Laptop 16 Go. Utilise un environnement Python isolé dans le projet.
Le dépôt Git privé avec son remote origin est déjà provisionné comme workspace actif. Travaille directement dedans. Teste, commit et push chaque changement cohérent; ne commit jamais données, checkpoints, modèles, artefacts médicaux, secrets ou résultats individuels.
Crée et maintiens toi-même le code Python, le lanceur autonomous_run.ps1, les checkpoints reprenables, les métriques agrégées, les visualisations pertinentes et les fichiers finaux au format du challenge. Tu peux consulter le leaderboard mais ne dois jamais soumettre automatiquement.`,
        research_refresh_interval: 3,
        command: ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "autonomous_run.ps1"],
        working_directory: ".",
        metrics_path: "artifacts/metrics.json",
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
        checkpoint_path: "checkpoints/latest",
        resume_command: ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "autonomous_run.ps1", "-Resume"],
        stop_signal_path: "artifacts/STOP_REQUESTED",
        stop_grace_seconds: 30,
        mode: $("autonomous-mode").value,
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
    setView(prefill.view === "autonomous" ? "autonomous" : "plan");
    syncStartFields();
    syncAutonomousSchedule();
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

  return { load, open, save, setView, syncAutonomousSchedule, syncStartFields, startParkinsons };
};
