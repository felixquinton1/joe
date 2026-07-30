(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.JoeI18n = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  const translations = {
    fr: {
      language: "Langue", slogan: "L’IA à la mode chez les jeunes",
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
      cancel: "Annuler", save: "Enregistrer"
    },
    en: {
      language: "Language", slogan: "AI, the way the cool kids do it",
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
      cancel: "Cancel", save: "Save"
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
    return selected;
  }
  return { apply, initialLanguage, supported, translate, translations };
});
