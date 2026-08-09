window.createAutomationModule = ({ state, $, fetcher }) => {
  let plans = [];
  let campaigns = [];
  // Rappel de celui qui a ouvert le formulaire : un plan confié au
  // planificateur ne doit plus rester affiché comme « à valider ».
  let onScheduled = null;
  const tr = key => window.JoeI18n.translate(document.documentElement.lang, key);
  const statusLabel = value => ({
    scheduled: "scheduled", completed: "done", cancelled: "cancelled", blocked: "blocked",
    paused: "paused", planning: "planning", research: "research", researching: "research",
    experimenting: "experimenting", evaluating: "evaluating", running: "running",
    failed: "failed", crashed: "crashed", timed_out: "timed_out", unverified: "unverified",
    comparable: "comparable_status"
  }[value] ? tr({
    scheduled: "scheduled", completed: "done", cancelled: "cancelled", blocked: "blocked",
    paused: "paused", planning: "planning", research: "research", researching: "research",
    experimenting: "experimenting", evaluating: "evaluating", running: "running",
    failed: "failed", crashed: "crashed", timed_out: "timed_out", unverified: "unverified",
    comparable: "comparable_status"
  }[value]) : value);

  const formatDate = value => value
    ? new Date(Number(value) * 1000).toLocaleString(document.documentElement.lang)
    : tr("now");

  const formatElapsed = value => {
    const seconds = Math.max(0, Math.floor(Date.now() / 1000 - Number(value || 0)));
    if (seconds < 60) return `${seconds} s`;
    const minutes = Math.floor(seconds / 60);
    return minutes < 60 ? `${minutes} min ${seconds % 60} s` : `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
  };

  const formatMetric = value => value !== null && value !== undefined && Number.isFinite(Number(value))
    ? Number(value).toLocaleString(undefined, { maximumSignificantDigits: 6 })
    : "—";

  function analysisPanel(campaign) {
    const analysis = campaign.analysis || {};
    const summary = analysis.summary || {};
    const usage = analysis.usage || {};
    const budget = analysis.token_budget || {};
    const panel = document.createElement("details");
    panel.className = "autonomous-analysis";
    const heading = document.createElement("summary");
    heading.textContent = `${tr("experiment_tree")} · ${summary.comparable || 0}/${summary.final || 0} ${tr("comparable")}, ${summary.partial || 0} ${tr("partial")} · ${tr("best")} ${formatMetric(summary.best_metric)}`;
    const meta = document.createElement("p");
    meta.textContent = `${analysis.metric_name || tr("metric")} (${analysis.metric_direction || "max"}) · ${(analysis.checkpoints || []).filter(item => item.resume_ready).length} checkpoint(s) · ${usage.prompts || 0} prompt(s) · ${usage.model_calls || 0}/${budget.max_model_calls ?? "∞"} call(s) · ${usage.known_tokens || 0}/${budget.max_tokens ?? tr("unbounded")} ${tr("known_tokens")}`;
    const tree = document.createElement("ol");
    tree.className = "experiment-tree";
    for (const node of analysis.experiments || []) {
      const item = document.createElement("li");
      item.className = `metric-${node.quality || "missing"}`;
      const label = document.createElement("b");
      label.textContent = `#${node.iteration} · ${formatMetric(node.metric)} · ${statusLabel(node.status)} · ${statusLabel(node.validation_status)}`;
      const detail = document.createElement("small");
      detail.textContent = [node.variant, node.hypothesis || node.reason || "Expérience sans hypothèse structurée"].filter(Boolean).join(" · ");
      if (node.reason && node.hypothesis) detail.title = node.reason;
      item.append(label, detail);
      tree.appendChild(item);
    }
    if (!tree.childNodes.length) {
      const empty = document.createElement("li");
      empty.textContent = tr("no_experiment");
      tree.appendChild(empty);
    }
    panel.append(heading, meta, tree);
    return panel;
  }

  function comparisonPanel(items) {
    const comparable = items.filter(item => item.analysis?.summary).sort((left, right) => {
      const a = left.analysis;
      const b = right.analysis;
      const group = `${a.metric_name}:${a.metric_direction}`.localeCompare(`${b.metric_name}:${b.metric_direction}`);
      if (group) return group;
      const av = a.summary.best_metric;
      const bv = b.summary.best_metric;
      if (av == null) return 1;
      if (bv == null) return -1;
      return a.metric_direction === "min" ? av - bv : bv - av;
    });
    if (comparable.length < 2) return null;
    const panel = document.createElement("section");
    panel.className = "campaign-comparison";
    const title = document.createElement("b");
    title.textContent = tr("campaign_comparison");
    const table = document.createElement("div");
    for (const campaign of comparable) {
      const analysis = campaign.analysis;
      const row = document.createElement("p");
      const direction = analysis.metric_direction === "min" ? "↓" : "↑";
      row.textContent = `${campaign.title} · ${analysis.metric_name} ${direction} · ${tr("best")} ${formatMetric(analysis.summary.best_metric)} · ${tr("gain")} ${formatMetric(analysis.summary.improvement)} · ${analysis.summary.final}/${analysis.summary.total} ${tr("final_results")} · ${analysis.usage.known_tokens} ${tr("known_tokens")}`;
      table.appendChild(row);
    }
    panel.append(title, table);
    return panel;
  }

  function campaignActivity(campaign) {
    if (["completed", "cancelled", "blocked"].includes(campaign.status)) {
      return { active: false, text: campaign.error ? `${tr("stop")} · ${campaign.error}` : tr("no_command") };
    }
    if (campaign.manual_hold) {
      return { active: false, text: tr("manual_pause_activity") };
    }
    const activeStep = [...(campaign.history || [])].reverse().find(
      event => event.kind === "agent_step" && event.status === "running"
    );
    if (campaign.current_run_id) {
      const actor = activeStep?.provider ? activeStep.provider.toUpperCase() : "L’IA";
      return {
        active: true,
        text: `${actor === "L’IA" ? tr("ai_working") : `${actor} · ${tr("ai_working")}`} · ${campaign.phase} · ${tr("running_since")} ${formatElapsed(activeStep?.at || campaign.updated_at)}`
      };
    }
    if (campaign.status === "experimenting") {
      return { active: true, text: tr("experiment_running") };
    }
    if (campaign.state === "preparing") {
      const running = campaign.preflight?.status === "running";
      return {
        active: running,
        text: running
          ? tr("preflight_running")
          : `${tr("preflight_waiting")}${campaign.next_start_at ? ` · ${formatDate(campaign.next_start_at)}` : ""}`
      };
    }
    if (campaign.status === "scheduled" && campaign.phase === "experiment") {
      return { active: true, text: tr("command_preparing") };
    }
    if (campaign.status === "evaluating") {
      return { active: true, text: tr("results_preparing") };
    }
    if (campaign.status === "paused") {
      return { active: false, text: `${tr("scheduled_pause")}${campaign.next_start_at ? ` · ${tr("resume")} ${formatDate(campaign.next_start_at)}` : ""}` };
    }
    return { active: false, text: campaign.error ? `${tr("stop")} · ${campaign.error}` : tr("no_command") };
  }

  function activeProject() {
    const conversation = state.conversations.find(
      item => item.id === state.activeConversationId
    );
    return state.projects.find(item => item.id === conversation?.project_id);
  }

  function journalLabel(event) {
    if (event.kind === "agent_step") {
      const actor = [event.provider, event.model].filter(Boolean).join(" / ") || tr("routing");
      const mode = event.mode === "consensus" ? "consensus multi-IA" : event.mode || "une IA";
      const changes = event.files ? ` · ${event.files} ${tr(event.files === 1 ? "file_singular" : "file_plural")}, +${event.insertions || 0}/-${event.deletions || 0}` : "";
      const attempts = (event.attempts || []).map(item => [item.provider, item.model].filter(Boolean).join("/")).filter(Boolean);
      const participants = attempts.length ? ` · ${tr("calls")} ${attempts.join(", ")}` : "";
      return `${statusLabel(event.phase) || tr("ai_step")} · ${actor} · ${mode} · ${statusLabel(event.status)}${participants}${changes}`;
    }
    if (event.kind === "experiment") {
      const command = Array.isArray(event.command) ? event.command.join(" ") : tr("local_command");
      const primary = event.metrics?.primary_metric;
      const metrics = primary && primary.value !== null && primary.value !== undefined
        ? `${primary.name || tr("metric")}=${formatMetric(primary.value)}`
        : "";
      return `${tr("experiment")} · ${command} · ${statusLabel(event.status)} · ${event.duration_seconds || "?"} s${metrics ? ` · ${metrics}` : ""}`;
    }
    if (event.kind === "paused") return `${tr("planned_pause")} · ${tr("resume")} ${formatDate(event.next_start_at)}`;
    return `${statusLabel(event.kind) || tr("event")} · ${statusLabel(event.status) || tr("recorded")}`;
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
        <button type="button"></button>`;
      card.querySelector("strong").textContent = plan.title;
      card.querySelector("span").textContent = statusLabel(plan.status);
      card.querySelector("small").textContent =
        `${completed}/${plan.steps.length} ${tr("steps_count")} · ${formatDate(plan.scheduled_for)}`;
      const button = card.querySelector("button");
      button.textContent = tr("cancel");
      button.hidden = ["completed", "cancelled", "blocked"].includes(plan.status);
      button.onclick = async () => {
        await fetcher(`/api/automations/${plan.id}/cancel`, { method: "POST" });
        await load();
      };
      target.appendChild(card);
    }
    if (!plans.length) {
      target.innerHTML = `<small>${tr("no_scheduled_plan")}</small>`;
    }
    const autonomousTarget = $("autonomous-list");
    autonomousTarget.replaceChildren();
    const comparison = comparisonPanel(campaigns);
    if (comparison) autonomousTarget.appendChild(comparison);
    for (const campaign of campaigns.slice(0, 8)) {
      const card = document.createElement("article");
      card.className = `automation-card ${campaign.status}`;
      card.innerHTML = `<div><strong></strong><span></span></div><small></small><p class="autonomous-activity" role="status"><i></i><b></b></p><div class="autonomous-actions"><button type="button" data-action="cancel">${tr("cancel")}</button><button type="button" data-action="handoff">${tr("manual_handoff")}</button><button type="button" data-action="resume">${tr("resume_autonomous")}</button><button type="button" data-action="chat">${tr("continue_chat")}</button></div>`;
      card.querySelector("strong").textContent = campaign.title;
      card.querySelector("span").textContent = statusLabel(campaign.status);
      card.querySelector("small").textContent = `${tr("iteration")} ${campaign.iteration}/${campaign.max_iterations} · ${tr("phase")} ${statusLabel(campaign.phase)}`;
      if (campaign.status === "paused" && campaign.next_start_at) {
        card.querySelector("small").textContent += ` · ${tr("resume")} ${formatDate(campaign.next_start_at)}`;
      }
      const activity = campaignActivity(campaign);
      const activityNode = card.querySelector(".autonomous-activity");
      activityNode.classList.toggle("active", activity.active);
      activityNode.querySelector("b").textContent = activity.text;
      const journal = document.createElement("details");
      journal.className = "autonomous-journal";
      const summary = document.createElement("summary");
      summary.textContent = `${tr("detailed_log")} · ${(campaign.history || []).length}`;
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
        item.textContent = tr("campaign_not_started");
        list.appendChild(item);
      }
      journal.append(summary, list);
      card.append(analysisPanel(campaign), journal);
      const terminal = ["completed", "cancelled", "blocked"].includes(campaign.status);
      const cancelButton = card.querySelector('[data-action="cancel"]');
      cancelButton.hidden = terminal;
      cancelButton.onclick = async () => {
        if (!window.confirm(tr("cancel_campaign_confirm"))) return;
        await fetcher(`/api/autonomous/${campaign.id}/cancel`, { method: "POST" });
        await load();
      };
      const handoffButton = card.querySelector('[data-action="handoff"]');
      handoffButton.hidden = terminal || campaign.manual_hold;
      handoffButton.onclick = async () => {
        if (!window.confirm(tr("handoff_confirm"))) return;
        const response = await fetcher(`/api/autonomous/${campaign.id}/handoff`, { method: "POST" });
        const payload = await response.json();
        if (!response.ok) return window.alert(payload.error || tr("handoff_failed"));
        $("automation-dialog").close();
        window.dispatchEvent(new CustomEvent("joe:open-conversation", {
          detail: { conversationId: campaign.conversation_id }
        }));
      };
      const resumeButton = card.querySelector('[data-action="resume"]');
      resumeButton.hidden = !(terminal || campaign.manual_hold);
      resumeButton.onclick = async () => {
        const response = await fetcher(`/api/autonomous/${campaign.id}/resume`, { method: "POST" });
        const payload = await response.json();
        if (!response.ok) return window.alert(payload.error || tr("resume_failed"));
        await load();
      };
      const chatButton = card.querySelector('[data-action="chat"]');
      chatButton.hidden = !(terminal || campaign.manual_hold);
      chatButton.onclick = () => {
        $("automation-dialog").close();
        window.dispatchEvent(new CustomEvent("joe:open-conversation", {
          detail: { conversationId: campaign.conversation_id }
        }));
      };
      autonomousTarget.appendChild(card);
    }
    if (!campaigns.length) autonomousTarget.innerHTML = `<small>${tr("no_autonomous_campaign")}</small>`;
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
    if (!$("autonomous-risk-ack").checked) {
      window.alert(tr("risk_confirmation"));
      $("autonomous-risk-ack").focus();
      return;
    }
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
        objective: "Maximiser la meilleure performance locale rigoureusement validée sur le DaT Parkinson's Challenge dans le temps, les ressources de calcul et les tokens impartis. Concevoir, implémenter et évaluer les solutions les plus prometteuses à partir des règles officielles; fournir les fichiers au format demandé, sans jamais effectuer de soumission.",
        research_protocol: "Commencer par les pages officielles, le leaderboard accessible et une recherche bibliographique ciblée. Revenir à la littérature seulement si un résultat, un plateau ou un blocage méthodologique le justifie; éviter les recherches périodiques sans information nouvelle.",
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
Politique de calcul : vérifie CUDA et mesure l'utilisation GPU. Après un baseline de plomberie bref, teste rapidement au moins une architecture 2D/2.5D ou 3D adaptée et accélérée. Une architecture publique peut être réimplémentée sans poids externes; audite séparément toute licence de poids préentraînés. Ne passe pas la campagne à tuner une baseline classique manifestement limitée. Regroupe les changements et fais évaluer plusieurs variantes comparables par un même runner checkpointé lorsque c'est sûr.
Le dépôt Git privé avec son remote origin est déjà provisionné comme workspace actif. Travaille directement dedans. Teste, commit et push chaque changement cohérent; ne commit jamais données, checkpoints, modèles, artefacts médicaux, secrets ou résultats individuels.
Crée et maintiens toi-même le code Python, le lanceur autonomous_run.ps1, les checkpoints reprenables, les métriques agrégées, les visualisations pertinentes et les fichiers finaux au format du challenge. Tu peux consulter le leaderboard mais ne dois jamais soumettre automatiquement.`,
        research_refresh_interval: 0,
        command: ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "autonomous_run.ps1"],
        working_directory: ".",
        metrics_path: "artifacts/metrics.json",
        metric_name: "log_loss",
        metric_direction: "min",
        timeout_seconds: 480,
        max_iterations: 20,
        max_duration_seconds: Number($("autonomous-budget-minutes").value) * 60,
        restricted_data: true,
        token_budget: {
          max_tokens: $("autonomous-token-budget").value === ""
            ? null : Number($("autonomous-token-budget").value),
          max_model_calls: $("autonomous-call-budget").value === ""
            ? null : Number($("autonomous-call-budget").value)
        },
        resource_policy: {
          mode: $("autonomous-resource-mode").value,
          gpu_index: $("autonomous-gpu-index").value === ""
            ? null
            : Number($("autonomous-gpu-index").value),
          notes: $("autonomous-resource-notes").value.trim()
        },
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
    if (!response.ok) return window.alert(payload.error || tr("start_autonomous_failed"));
    await load();
  }

  async function open(prefill = {}) {
    const project = activeProject();
    if (!project || !state.activeConversationId) {
      window.alert(tr("choose_conversation"));
      return;
    }
    $("automation-project").textContent = project.name;
    $("automation-provider").value = project.quota_provider || "";
    $("automation-title").value = prefill.title || "";
    $("automation-steps").value = (prefill.steps || []).join("\n");
    $("automation-when").value = "";
    $("automation-start").value = "now";
    $("autonomous-risk-ack").checked = false;
    $("autonomous-resource-mode").value = "auto";
    $("autonomous-gpu-index").value = "";
    $("autonomous-resource-notes").value = "";
    $("autonomous-token-budget").value = "";
    $("autonomous-call-budget").value = "";
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
      window.alert(tr("save_quota_failed"));
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
      window.alert(payload.error || tr("schedule_failed"));
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

  return { load, open, render, save, setView, syncAutonomousSchedule, syncStartFields, startParkinsons };
};
