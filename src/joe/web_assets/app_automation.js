window.createAutomationModule = ({ state, $, fetcher }) => {
  let plans = [];

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
  }

  async function load() {
    const response = await fetcher("/api/automations");
    if (!response.ok) return;
    plans = await response.json();
    render();
  }

  async function open() {
    const project = activeProject();
    if (!project || !state.activeConversationId) {
      window.alert("Choisis d’abord une conversation.");
      return;
    }
    $("automation-project").textContent = project.name;
    $("automation-provider").value = project.quota_provider || "";
    $("automation-title").value = "";
    $("automation-steps").value = "";
    $("automation-when").value = "";
    await load();
    $("automation-dialog").showModal();
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
    const when = $("automation-when").value;
    const response = await fetcher("/api/automations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: $("automation-title").value,
        conversation_id: state.activeConversationId,
        steps,
        scheduled_for: when ? new Date(when).getTime() / 1000 : Date.now() / 1000,
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
    await load();
  }

  return { load, open, save };
};
