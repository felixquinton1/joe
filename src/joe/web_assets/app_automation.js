window.createAutomationModule = ({ state, $, fetcher }) => {
  let plans = [];
  let campaigns = [];
  // Rappel de celui qui a ouvert le formulaire : un plan confié au
  // planificateur ne doit plus rester affiché comme « à valider ».
  let onScheduled = null;

  const formatDate = value => value
    ? new Date(Number(value) * 1000).toLocaleString()
    : "Maintenant";

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
    heading.textContent = `Arbre d’expériences · ${summary.comparable || 0}/${summary.final || 0} comparable(s), ${summary.partial || 0} partiel(s) · meilleur ${formatMetric(summary.best_metric)}`;
    const meta = document.createElement("p");
    meta.textContent = `${analysis.metric_name || "métrique"} (${analysis.metric_direction || "max"}) · ${(analysis.checkpoints || []).filter(item => item.resume_ready).length} checkpoint(s) prêt(s) · ${usage.prompts || 0} prompt(s) · ${usage.model_calls || 0}/${budget.max_model_calls ?? "∞"} appel(s) · ${usage.known_tokens || 0}/${budget.max_tokens ?? "non borné"} tokens connus${usage.unknown_usage_calls ? ` · ${usage.unknown_usage_calls} appel(s) sans télémétrie` : ""}`;
    const tree = document.createElement("ol");
    tree.className = "experiment-tree";
    for (const node of analysis.experiments || []) {
      const item = document.createElement("li");
      item.className = `metric-${node.quality || "missing"}`;
      const label = document.createElement("b");
      label.textContent = `#${node.iteration} · ${formatMetric(node.metric)} · ${node.status} · ${node.validation_status}`;
      const detail = document.createElement("small");
      detail.textContent = [node.variant, node.hypothesis || node.reason || "Expérience sans hypothèse structurée"].filter(Boolean).join(" · ");
      if (node.reason && node.hypothesis) detail.title = node.reason;
      item.append(label, detail);
      tree.appendChild(item);
    }
    if (!tree.childNodes.length) {
      const empty = document.createElement("li");
      empty.textContent = "Aucune expérience locale enregistrée.";
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
    title.textContent = "Comparaison des campagnes";
    const table = document.createElement("div");
    for (const campaign of comparable) {
      const analysis = campaign.analysis;
      const row = document.createElement("p");
      const direction = analysis.metric_direction === "min" ? "↓" : "↑";
      row.textContent = `${campaign.title} · ${analysis.metric_name} ${direction} · meilleur ${formatMetric(analysis.summary.best_metric)} · gain ${formatMetric(analysis.summary.improvement)} · ${analysis.summary.final}/${analysis.summary.total} résultats finaux · ${analysis.usage.known_tokens} tokens connus`;
      table.appendChild(row);
    }
    panel.append(title, table);
    return panel;
  }

  function campaignActivity(campaign) {
    if (["completed", "cancelled", "blocked"].includes(campaign.status)) {
      return { active: false, text: campaign.error ? `Arrêté · ${campaign.error}` : "Aucune commande en cours" };
    }
    if (campaign.manual_hold) {
      return { active: false, text: "Pause manuelle · le projet peut être modifié dans le chat puis repris en Autonomous" };
    }
    const activeStep = [...(campaign.history || [])].reverse().find(
      event => event.kind === "agent_step" && event.status === "running"
    );
    if (campaign.current_run_id) {
      const actor = activeStep?.provider ? activeStep.provider.toUpperCase() : "L’IA";
      return {
        active: true,
        text: `${actor} travaille · ${campaign.phase} · en cours depuis ${formatElapsed(activeStep?.at || campaign.updated_at)}`
      };
    }
    if (campaign.status === "experimenting") {
      return { active: true, text: "Expérience locale en cours · commandes, sortie et métriques surveillées par Joe" };
    }
    if (campaign.state === "preparing") {
      const running = campaign.preflight?.status === "running";
      return {
        active: running,
        text: running
          ? "Préflight en cours · environnement et ressources vérifiés par Joe"
          : `Préflight en attente du démarrage${campaign.next_start_at ? ` · ${formatDate(campaign.next_start_at)}` : ""}`
      };
    }
    if (campaign.status === "scheduled" && campaign.phase === "experiment") {
      return { active: true, text: "Prochaine commande locale en préparation · lancement automatique imminent" };
    }
    if (campaign.status === "evaluating") {
      return { active: true, text: "Résultats récupérés · prochaine analyse en préparation" };
    }
    if (campaign.status === "paused") {
      return { active: false, text: `En pause planifiée${campaign.next_start_at ? ` · reprise ${formatDate(campaign.next_start_at)}` : ""}` };
    }
    return { active: false, text: campaign.error ? `Arrêté · ${campaign.error}` : "Aucune commande en cours" };
  }

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
    const comparison = comparisonPanel(campaigns);
    if (comparison) autonomousTarget.appendChild(comparison);
    for (const campaign of campaigns.slice(0, 8)) {
      const card = document.createElement("article");
      card.className = `automation-card ${campaign.status}`;
      card.innerHTML = `<div><strong></strong><span></span></div><small></small><p class="autonomous-activity" role="status"><i></i><b></b></p><div class="autonomous-actions"><button type="button" data-action="cancel">Annuler</button><button type="button" data-action="handoff">Passer en manuel</button><button type="button" data-action="resume">Reprendre Autonomous</button><button type="button" data-action="chat">Continuer dans le chat</button></div>`;
      card.querySelector("strong").textContent = campaign.title;
      card.querySelector("span").textContent = campaign.status;
      card.querySelector("small").textContent = `itération ${campaign.iteration}/${campaign.max_iterations} · phase ${campaign.phase}`;
      if (campaign.status === "paused" && campaign.next_start_at) {
        card.querySelector("small").textContent += ` · reprise ${formatDate(campaign.next_start_at)}`;
      }
      const activity = campaignActivity(campaign);
      const activityNode = card.querySelector(".autonomous-activity");
      activityNode.classList.toggle("active", activity.active);
      activityNode.querySelector("b").textContent = activity.text;
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
      card.append(analysisPanel(campaign), journal);
      const terminal = ["completed", "cancelled", "blocked"].includes(campaign.status);
      const cancelButton = card.querySelector('[data-action="cancel"]');
      cancelButton.hidden = terminal;
      cancelButton.onclick = async () => {
        if (!window.confirm("Interrompre cette campagne Autonomous ?")) return;
        await fetcher(`/api/autonomous/${campaign.id}/cancel`, { method: "POST" });
        await load();
      };
      const handoffButton = card.querySelector('[data-action="handoff"]');
      handoffButton.hidden = terminal || campaign.manual_hold;
      handoffButton.onclick = async () => {
        if (!window.confirm("Mettre Autonomous en pause et reprendre le projet manuellement ?")) return;
        const response = await fetcher(`/api/autonomous/${campaign.id}/handoff`, { method: "POST" });
        const payload = await response.json();
        if (!response.ok) return window.alert(payload.error || "Impossible de passer en manuel.");
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
        if (!response.ok) return window.alert(payload.error || "Impossible de reprendre la campagne.");
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
    if (!$("autonomous-risk-ack").checked) {
      window.alert("Confirme d’abord que tu as compris le fonctionnement expérimental et le risque de consommation de crédits.");
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
