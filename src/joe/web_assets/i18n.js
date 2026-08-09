(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.JoeI18n = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  const translations = {
    fr: {
      language: "Langue",
      conversations: "Conversations", activity: "Activité",
      loading: "Chargement…", quotas: "Quotas", project: "Projet",
      agent: "Agent", workflow: "Workflow", automatic: "Automatique",
      configuration: "Options", model: "Modèle", effort: "Effort",
      permissions: "Permissions", active_conversation: "Conversation active",
      accomplish: "Que veux-tu accomplir ?", send: "Envoyer",
      queue: "Ajouter à la file", stop: "Interrompre", stopping: "Arrêt…",
      ready: "Prêt", running: "En cours", done: "Terminé", failed: "Échec",
      provider_default: "Défaut du fournisseur", model_default: "Défaut du modèle",
      prompt_placeholder: "Ex. Ajoute une option pour désactiver la loss Gamma…",
      composer_hint: "Entrée pour envoyer · Maj+Entrée pour une nouvelle ligne",
      empty_title: "Un seul point d’entrée, plusieurs intelligences.",
      empty_text: "Décris simplement ton besoin. Joe choisira le workflow et transmettra le contexte du projet.",
      live: "Temps réel", agents: "Agents", evidence: "Preuves et diagnostic",
      evidence_text: "À ouvrir seulement pour contrôler une affirmation ou diagnostiquer un blocage.",
      preferences: "Préférences", defaults_title: "Réglages des nouvelles conversations",
      defaults_intro: "Ces choix servent de point de départ. Chaque conversation peut ensuite conserver ses propres réglages.",
      preferred_agent: "Agent principal", automatic_recommended: "Automatique — recommandé",
      preferred_agent_help: "Joe privilégie cet agent, puis utilise un fallback si nécessaire.",
      default_workflow: "Workflow par défaut",
      default_workflow_help: "En automatique, Joe adapte le nombre d’agents à la demande.",
      tasks: "Tâches", tasks_help: "Runs durables et worktrees",
      no_tasks: "Aucune tâche récente", current_workspace: "Workspace courant",
      review_task: "À examiner", integrated: "Intégrée", cancelled: "Interrompue",
      integrating: "Synchronisation", resolving: "Résolution", conflict: "Conflit",
      integration_failed: "L’intégration nécessite ton attention",
      view_diff: "Voir le diff", integrate: "Intégrer", delete: "Supprimer",
      isolated_worktree: "Worktree isolé", task_changes: "Modifications de la tâche",
      close: "Fermer",
      cancel: "Annuler", save: "Enregistrer",
      new_project: "Nouveau projet", automation_type: "Type d’automatisation",
      automation_intro: "Choisis le type d’automatisation à configurer.",
      step_plan: "Plan par étapes", step_plan_help: "Une liste d’actions déterministes",
      autonomous_campaign: "Campagne Autonomous", autonomous_campaign_help: "Recherche et expérimentation en boucle",
      plan_configuration: "Configuration du plan", plan_configuration_help: "Chaque étape démarre après validation de la précédente.",
      quota_strategy: "Stratégie de quotas", reserved_ai: "IA réservée", automatic_balancing: "Équilibrage automatique",
      title: "Titre", start: "Démarrage", start_now: "Dès maintenant", start_at: "À une date précise",
      start_quota_reset: "Au rechargement des quotas (+1 min)", date: "Date", steps: "Étapes",
      steps_placeholder: "Une étape par ligne :\nImplémenter la fonctionnalité\nExécuter les tests\nAnalyser les résultats et corriger",
      review: "Relecture", fast: "Rapide", access: "Accès", project_write: "Écriture projet", read_only: "Lecture seule",
      max_corrections: "Corrections max", auto_integrate: "Intégrer automatiquement chaque worktree validé",
      push_project_setting: "Le push reste régi par le réglage du projet.", recent_plans: "Plans récents",
      research_campaign: "Campagne de recherche", research_loop_help: "Boucle bornée : recherche → code → expérience → analyse.",
      reasoning: "Raisonnement", one_ai: "Une IA", ai_review: "Une IA + relecture", multi_ai_consensus: "Consensus multi-IA",
      active_budget: "Budget actif (minutes)", known_token_budget: "Budget tokens connu", optional: "Facultatif",
      max_model_calls: "Appels modèle max", automatic_calculation: "Calcul automatique", execution_window: "Fenêtre d’exécution",
      daily_window: "Limiter la campagne à une plage quotidienne", beginning: "Début", end: "Fin", days: "Jours",
      every_day: "Tous les jours", weekdays: "Du lundi au vendredi", schedule_resume_help: "En dehors de cette plage, Joe interrompt proprement les runs, enregistre les checkpoints puis reprend automatiquement.",
      resources_optional: "Ressources (facultatif)", policy: "Politique", automatic_choice: "Choix automatique",
      gpu_only: "GPU uniquement", cpu_only: "CPU uniquement", gpu_index: "Indice GPU", details: "Précisions",
      resource_notes_placeholder: "Ex. préserver 4 Go de VRAM", resource_help: "Sans précision, le préflight choisit automatiquement les ressources disponibles au démarrage.",
      full_traceability: "Traçabilité complète", traceability_help: "Les recherches, modèles appelés, choix, commits, expériences et métriques apparaissent dans le journal.",
      experimental_feature: "Fonctionnalité expérimentale", autonomous_risk: "Autonomous peut enchaîner sans intervention des recherches, appels de modèles, commandes et expériences. Les limites réduisent le risque mais ne garantissent ni le coût, ni le résultat, ni l’absence d’erreur. Vérifie le budget et les permissions avant de lancer.",
      autonomous_consent: "J’ai compris que la campagne continue sans moi et peut consommer mes crédits IA.",
      recent_campaigns: "Campagnes récentes", schedule_plan: "Programmer le plan", launch_dat: "Lancer la campagne DaT Parkinson",
      now: "Maintenant", no_scheduled_plan: "Aucun plan programmé.", no_autonomous_campaign: "Aucune campagne Autonomous.",
      experiment_tree: "Arbre d’expériences", best: "meilleur", metric: "métrique", unbounded: "non borné",
      known_tokens: "tokens connus", campaign_comparison: "Comparaison des campagnes", gain: "gain", final_results: "résultats finaux",
      no_experiment: "Aucune expérience locale enregistrée.", no_command: "Aucune commande en cours",
      cancel_campaign_confirm: "Interrompre cette campagne Autonomous ?", manual_handoff: "Passer en manuel",
      resume_autonomous: "Reprendre Autonomous", continue_chat: "Continuer dans le chat", detailed_log: "Journal détaillé",
      campaign_not_started: "La campagne n’a pas encore démarré.", choose_conversation: "Choisis d’abord une conversation.",
      risk_confirmation: "Confirme d’abord que tu as compris le fonctionnement expérimental et le risque de consommation de crédits.",
      start_autonomous_failed: "Impossible de démarrer Autonomous.", save_quota_failed: "Impossible d’enregistrer la stratégie de quotas.",
      schedule_failed: "Impossible de programmer ce plan.", language_changed: "Langue de l’interface mise à jour"
      ,manual_pause_activity: "Pause manuelle · le projet peut être modifié dans le chat puis repris en Autonomous",
      experiment_running: "Expérience locale en cours · commandes, sortie et métriques surveillées par Joe",
      preflight_running: "Préflight en cours · environnement et ressources vérifiés par Joe", preflight_waiting: "Préflight en attente du démarrage",
      command_preparing: "Prochaine commande locale en préparation · lancement automatique imminent",
      results_preparing: "Résultats récupérés · prochaine analyse en préparation", scheduled_pause: "En pause planifiée",
      resume: "reprise", iteration: "itération", phase: "phase", steps_count: "étapes",
      handoff_confirm: "Mettre Autonomous en pause et reprendre le projet manuellement ?",
      handoff_failed: "Impossible de passer en manuel.", resume_failed: "Impossible de reprendre la campagne."
      ,search_history: "Rechercher dans l’historique", advanced_options: "modèle · effort · permissions",
      attach_files: "Joindre des fichiers", tools: "Outils", resize_conversations: "Redimensionner les conversations",
      resize_activity: "Redimensionner le suivi", autonomous_plans: "Plans autonomes", personal_usage: "Consommation personnelle",
      filter_tasks: "Filtrer les tâches", active_plural: "Actives", review_plural: "À valider", all: "Toutes",
      orchestrator_warning: "Joe est un orchestrateur", orchestrator_warning_text: "Les modèles et API restent des services tiers. Surveille tes quotas, crédits et runs actifs : fermer cette page n’arrête pas le travail en cours.",
      new_conversation: "Nouvelle conversation", conversation_running: "En cours", conversation_complete: "Terminée",
      move_project: "Déplacer le projet", expand_conversations: "Déplier les conversations", collapse_conversations: "Replier les conversations",
      add_conversation: "Nouvelle conversation dans ce projet", move_conversation: "Déplacer la conversation",
      pin: "Épingler", unpin: "Désépingler", no_conversation: "Aucune conversation", try_another_keyword: "Essaie un autre mot-clé.",
      messages: "messages", never: "jamais", no_call: "Aucun appel"
      ,waiting_quota: "En attente du quota", open_task: "Ouvrir le prompt de cette tâche", files: "fichiers",
      resume_capitalized: "Reprise", conversation: "Conversation", request_stage: "Demande", implementation_stage: "Réalisation",
      validation_stage: "Validation", diff_stage: "Diff", delivery_stage: "Livraison"
      ,automation: "Automatisation", plan_autonomous_work: "Planifier un travail autonome",
      server_automation: "Une automatisation continue côté serveur", server_automation_warning: "Elle peut appeler des services IA payants même si l’onglet ou VS Code est fermé. Fixe des limites, surveille tes quotas et utilise « Annuler » pour l’interrompre.",
      quota_help: "Joe peut attendre la prochaine fenêtre de quota. Une relecture ou un consensus peut appeler une autre IA.",
      window_help: "Les runs sont interrompus proprement, sauvegardés puis repris au prochain créneau.",
      resource_creation_help: "Tu peux répondre à ces informations ou les laisser vides. Joe vérifiera les ressources au début du premier créneau, pas lors de la création.",
      ai_working: "L’IA travaille", running_since: "en cours depuis", routing: "routage en cours", local_command: "commande locale",
      ai_step: "étape IA", event: "événement", recorded: "enregistré", experiment: "expérience", planned_pause: "pause planifiée",
      calls: "appels", file_singular: "fichier", file_plural: "fichiers", comparable: "comparable(s)", partial: "partiel(s)"
      ,scheduled: "Planifiée", blocked: "Bloquée", paused: "En pause", planning: "Planification", research: "Recherche",
      experimenting: "Expérimentation", evaluating: "Évaluation", crashed: "Crash", timed_out: "Délai dépassé",
      unverified: "non vérifié", comparable_status: "comparable"
      ,compute: "calcul", longest_run: "run le plus long", substantive_runs: "runs substantiels", gpu_measured: "GPU mesuré"
      ,project_trash: "Corbeille des projets", move_to_trash: "Mettre à la corbeille", reversible_action: "Action réversible",
      trash_project_question: "Mettre ce projet à la corbeille ?", trash_project_explanation: "Le projet et ses conversations disparaîtront de Joe, mais pourront être restaurés. Aucun fichier ni dépôt sur le disque ne sera supprimé.",
      confirm_trash: "Confirmer la mise à la corbeille", reversible_deletion: "Suppression réversible",
      project_trash_help: "Restaurer un projet remet également ses conversations dans Joe. Les fichiers sur le disque ne sont jamais supprimés.",
      restore: "Restaurer", delete_permanently: "Supprimer définitivement", empty_trash: "La corbeille est vide.", conversations_count: "conversation(s)",
      permanent_delete_confirm: "Supprimer définitivement ce projet et ses conversations de Joe ? Les fichiers sur le disque resteront intacts."
    },
    en: {
      language: "Language",
      conversations: "Conversations", activity: "Activity",
      loading: "Loading…", quotas: "Quotas", project: "Project",
      agent: "Agent", workflow: "Workflow", automatic: "Automatic",
      configuration: "Options", model: "Model", effort: "Effort",
      permissions: "Permissions", active_conversation: "Active conversation",
      accomplish: "What do you want to accomplish?", send: "Send",
      queue: "Add to queue", stop: "Stop", stopping: "Stopping…",
      ready: "Ready", running: "Running", done: "Done", failed: "Failed",
      provider_default: "Provider default", model_default: "Model default",
      prompt_placeholder: "E.g. Add an option to disable Gamma loss…",
      composer_hint: "Enter to send · Shift+Enter for a new line",
      empty_title: "One entry point, multiple intelligences.",
      empty_text: "Describe what you need. Joe will choose the workflow and pass the relevant project context.",
      live: "Live", agents: "Agents", evidence: "Evidence and diagnostics",
      evidence_text: "Open only to verify a claim or diagnose a blocked operation.",
      preferences: "Preferences", defaults_title: "New conversation defaults",
      defaults_intro: "These choices are a starting point. Each conversation can then keep its own settings.",
      preferred_agent: "Primary agent", automatic_recommended: "Automatic — recommended",
      preferred_agent_help: "Joe prioritizes this agent, then falls back when needed.",
      default_workflow: "Default workflow",
      default_workflow_help: "In automatic mode, Joe adapts the number of agents to the request.",
      tasks: "Tasks", tasks_help: "Durable runs and worktrees",
      no_tasks: "No recent tasks", current_workspace: "Current workspace",
      review_task: "Review needed", integrated: "Integrated", cancelled: "Cancelled",
      integrating: "Synchronizing", resolving: "Resolving", conflict: "Conflict",
      integration_failed: "Integration needs your attention",
      view_diff: "View diff", integrate: "Integrate", delete: "Delete",
      isolated_worktree: "Isolated worktree", task_changes: "Task changes",
      close: "Close",
      cancel: "Cancel", save: "Save",
      new_project: "New project", automation_type: "Automation type",
      automation_intro: "Choose the type of automation to configure.",
      step_plan: "Step-by-step plan", step_plan_help: "A deterministic list of actions",
      autonomous_campaign: "Autonomous campaign", autonomous_campaign_help: "Iterative research and experimentation",
      plan_configuration: "Plan configuration", plan_configuration_help: "Each step starts after the previous one is validated.",
      quota_strategy: "Quota strategy", reserved_ai: "Reserved AI", automatic_balancing: "Automatic balancing",
      title: "Title", start: "Start", start_now: "Start now", start_at: "At a specific date",
      start_quota_reset: "When quotas reset (+1 min)", date: "Date", steps: "Steps",
      steps_placeholder: "One step per line:\nImplement the feature\nRun the tests\nAnalyze the results and fix issues",
      review: "Review", fast: "Fast", access: "Access", project_write: "Project write access", read_only: "Read only",
      max_corrections: "Max corrections", auto_integrate: "Automatically integrate each validated worktree",
      push_project_setting: "Push behavior follows the project setting.", recent_plans: "Recent plans",
      research_campaign: "Research campaign", research_loop_help: "Bounded loop: research → code → experiment → analysis.",
      reasoning: "Reasoning", one_ai: "One AI", ai_review: "One AI + review", multi_ai_consensus: "Multi-AI consensus",
      active_budget: "Active budget (minutes)", known_token_budget: "Known token budget", optional: "Optional",
      max_model_calls: "Max model calls", automatic_calculation: "Automatic calculation", execution_window: "Execution window",
      daily_window: "Limit the campaign to a daily window", beginning: "Start", end: "End", days: "Days",
      every_day: "Every day", weekdays: "Monday to Friday", schedule_resume_help: "Outside this window, Joe stops runs safely, saves checkpoints, then resumes automatically.",
      resources_optional: "Resources (optional)", policy: "Policy", automatic_choice: "Automatic choice",
      gpu_only: "GPU only", cpu_only: "CPU only", gpu_index: "GPU index", details: "Details",
      resource_notes_placeholder: "E.g. keep 4 GB of VRAM free", resource_help: "If unspecified, preflight automatically selects available resources at startup.",
      full_traceability: "Full traceability", traceability_help: "Research, model calls, decisions, commits, experiments, and metrics appear in the log.",
      experimental_feature: "Experimental feature", autonomous_risk: "Autonomous can chain research, model calls, commands, and experiments without intervention. Limits reduce risk but do not guarantee cost, outcome, or freedom from errors. Check budgets and permissions before starting.",
      autonomous_consent: "I understand that the campaign continues without me and may consume my AI credits.",
      recent_campaigns: "Recent campaigns", schedule_plan: "Schedule plan", launch_dat: "Launch the DaT Parkinson campaign",
      now: "Now", no_scheduled_plan: "No scheduled plan.", no_autonomous_campaign: "No Autonomous campaign.",
      experiment_tree: "Experiment tree", best: "best", metric: "metric", unbounded: "unbounded",
      known_tokens: "known tokens", campaign_comparison: "Campaign comparison", gain: "gain", final_results: "final results",
      no_experiment: "No local experiment recorded.", no_command: "No command running",
      cancel_campaign_confirm: "Stop this Autonomous campaign?", manual_handoff: "Switch to manual",
      resume_autonomous: "Resume Autonomous", continue_chat: "Continue in chat", detailed_log: "Detailed log",
      campaign_not_started: "The campaign has not started yet.", choose_conversation: "Choose a conversation first.",
      risk_confirmation: "First confirm that you understand the experimental behavior and the risk of consuming credits.",
      start_autonomous_failed: "Unable to start Autonomous.", save_quota_failed: "Unable to save the quota strategy.",
      schedule_failed: "Unable to schedule this plan.", language_changed: "Interface language updated"
      ,manual_pause_activity: "Manual pause · the project can be edited in chat and then resumed in Autonomous",
      experiment_running: "Local experiment running · commands, output, and metrics monitored by Joe",
      preflight_running: "Preflight running · environment and resources checked by Joe", preflight_waiting: "Preflight waiting for startup",
      command_preparing: "Preparing the next local command · automatic launch imminent",
      results_preparing: "Results received · preparing the next analysis", scheduled_pause: "Scheduled pause",
      resume: "resume", iteration: "iteration", phase: "phase", steps_count: "steps",
      handoff_confirm: "Pause Autonomous and continue the project manually?",
      handoff_failed: "Unable to switch to manual mode.", resume_failed: "Unable to resume the campaign."
      ,search_history: "Search history", advanced_options: "model · effort · permissions",
      attach_files: "Attach files", tools: "Tools", resize_conversations: "Resize conversations",
      resize_activity: "Resize activity panel", autonomous_plans: "Autonomous plans", personal_usage: "Personal usage",
      filter_tasks: "Filter tasks", active_plural: "Active", review_plural: "To review", all: "All",
      orchestrator_warning: "Joe is an orchestrator", orchestrator_warning_text: "Models and APIs remain third-party services. Monitor your quotas, credits, and active runs: closing this page does not stop ongoing work.",
      new_conversation: "New conversation", conversation_running: "Running", conversation_complete: "Completed",
      move_project: "Move project", expand_conversations: "Expand conversations", collapse_conversations: "Collapse conversations",
      add_conversation: "New conversation in this project", move_conversation: "Move conversation",
      pin: "Pin", unpin: "Unpin", no_conversation: "No conversation", try_another_keyword: "Try another keyword.",
      messages: "messages", never: "never", no_call: "No call"
      ,waiting_quota: "Waiting for quota", open_task: "Open this task prompt", files: "files",
      resume_capitalized: "Resume", conversation: "Conversation", request_stage: "Request", implementation_stage: "Implementation",
      validation_stage: "Validation", diff_stage: "Diff", delivery_stage: "Delivery"
      ,automation: "Automation", plan_autonomous_work: "Schedule autonomous work",
      server_automation: "Server-side continuous automation", server_automation_warning: "It may call paid AI services even when the tab or VS Code is closed. Set limits, monitor quotas, and use Cancel to stop it.",
      quota_help: "Joe can wait for the next quota window. A review or consensus may call another AI.",
      window_help: "Runs are stopped safely, saved, and resumed during the next window.",
      resource_creation_help: "You may provide these details or leave them blank. Joe will check resources at the start of the first window, not during creation.",
      ai_working: "The AI is working", running_since: "running for", routing: "routing", local_command: "local command",
      ai_step: "AI step", event: "event", recorded: "recorded", experiment: "experiment", planned_pause: "scheduled pause",
      calls: "calls", file_singular: "file", file_plural: "files", comparable: "comparable", partial: "partial"
      ,scheduled: "Scheduled", blocked: "Blocked", paused: "Paused", planning: "Planning", research: "Research",
      experimenting: "Experimenting", evaluating: "Evaluating", crashed: "Crashed", timed_out: "Timed out",
      unverified: "unverified", comparable_status: "comparable"
      ,compute: "compute", longest_run: "longest run", substantive_runs: "substantive runs", gpu_measured: "measured GPU"
      ,project_trash: "Project trash", move_to_trash: "Move to trash", reversible_action: "Reversible action",
      trash_project_question: "Move this project to trash?", trash_project_explanation: "The project and its conversations will disappear from Joe, but can be restored. No files or repositories on disk will be deleted.",
      confirm_trash: "Confirm move to trash", reversible_deletion: "Reversible deletion",
      project_trash_help: "Restoring a project also restores its conversations in Joe. Files on disk are never deleted.",
      restore: "Restore", delete_permanently: "Delete permanently", empty_trash: "Trash is empty.", conversations_count: "conversation(s)",
      permanent_delete_confirm: "Permanently delete this project and its conversations from Joe? Files on disk will remain untouched."
    }
  };

  function supported(value) {
    return value && value.toLowerCase().startsWith("en") ? "en" : "fr";
  }
  function initialLanguage(storage, navigatorLanguage) {
    return supported(storage?.getItem("joe-language") || navigatorLanguage || "fr");
  }
  function translate(language, key) {
    return translations[supported(language)][key] || translations.fr[key] || key;
  }
  function apply(document, language) {
    const selected = supported(language);
    document.documentElement.lang = selected;
    for (const node of document.querySelectorAll("[data-i18n]")) {
      node.textContent = translate(selected, node.dataset.i18n);
    }
    for (const node of document.querySelectorAll("[data-i18n-placeholder]")) {
      node.placeholder = translate(selected, node.dataset.i18nPlaceholder);
    }
    for (const node of document.querySelectorAll("[data-i18n-title]")) {
      node.title = translate(selected, node.dataset.i18nTitle);
    }
    for (const node of document.querySelectorAll("[data-i18n-aria-label]")) {
      node.setAttribute("aria-label", translate(selected, node.dataset.i18nAriaLabel));
    }
    return selected;
  }
  return { apply, initialLanguage, supported, translate, translations };
});
