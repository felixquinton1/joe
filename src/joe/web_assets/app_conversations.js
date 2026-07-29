window.createJoeConversations = function createJoeConversations({
  state,
  $,
  escapeHtml,
  closeMobilePanels,
  clearConversation,
  addMessage,
  renderMarkdown,
  renderGitReport,
  renderHistoricalRunSummary,
  applySettings,
  renderWorkflowUpdate,
  renderPromptQueue,
  fetcher
}) {
  let draggedItem = null;

  async function loadConversations(selectFirst = true) {
    [state.conversations, state.projects] = await Promise.all([
      fetcher("/api/conversations").then(response => response.json()),
      fetcher("/api/projects").then(response => response.json())
    ]);
    if (!state.conversations.length) {
      const created = await createConversation(false);
      state.conversations = [created];
    }
    renderConversations();
    if (selectFirst && !state.activeConversationId) {
      await selectConversation(state.conversations[0].id);
    }
  }

  function renderConversations() {
    const target = $("conversations");
    target.replaceChildren();
    for (const project of state.projects) {
      const group = document.createElement("section");
      group.className = `project-group ${project.collapsed ? "collapsed" : ""}`;
      group.dataset.projectId = project.id;
      const header = document.createElement("div");
      header.className = "project-group-head";
      const projectDrag = smallButton("⋮⋮", "Déplacer le projet", () => {});
      projectDrag.classList.add("drag-handle");
      projectDrag.draggable = true;
      projectDrag.ondragstart = event => beginDrag(event, "project", project.id);
      header.appendChild(projectDrag);
      const projectName = document.createElement("strong");
      projectName.textContent = project.name;
      header.appendChild(projectName);
      const projectActions = document.createElement("div");
      const collapse = smallButton(
        project.collapsed ? "▸" : "▾",
        project.collapsed ? "Déplier les conversations" : "Replier les conversations",
        () => toggleProjectCollapsed(project)
      );
      const editProject = smallButton("⚙", "Modifier le contexte du sous-projet", () => openProject(project));
      const addConversation = smallButton("＋", "Nouvelle conversation dans ce sous-projet", () => createConversation(true, project.id));
      projectActions.append(collapse, editProject, addConversation);
      header.appendChild(projectActions);
      header.ondragover = allowDrop;
      header.ondrop = event => dropOnProject(event, project.id);
      group.appendChild(header);
      const conversations = state.conversations
        .filter(item => item.project_id === project.id)
        .sort((left, right) => Number(right.pinned) - Number(left.pinned));
      for (const conversation of conversations) {
        const row = document.createElement("div");
        row.className = `conversation-item ${conversation.id === state.activeConversationId ? "active" : ""}`;
        row.dataset.conversationId = conversation.id;
        row.ondragover = allowDrop;
        row.ondrop = event => dropOnConversation(event, conversation);
        const drag = document.createElement("button");
        drag.className = "pin-button drag-handle";
        drag.title = "Déplacer la conversation";
        drag.textContent = "⋮";
        drag.draggable = true;
        drag.ondragstart = event => beginDrag(event, "conversation", conversation.id);
        const button = document.createElement("button");
        button.className = "history-item";
        const date = formatLastCall(conversation.last_call_at);
        button.innerHTML = `<strong>${escapeHtml(conversation.title)}</strong><span title="${escapeHtml(date.exact)}">${state.runs.has(conversation.id) ? "● En cours" : `${conversation.messages.length} messages`} · ${escapeHtml(date.short)}</span>`;
        button.onclick = () => selectConversation(conversation.id);
        const pin = document.createElement("button");
        pin.className = `pin-button ${conversation.pinned ? "pinned" : ""}`;
        pin.title = conversation.pinned ? "Désépingler" : "Épingler";
        pin.textContent = conversation.pinned ? "★" : "☆";
        pin.onclick = () => togglePin(conversation);
        const rename = document.createElement("button");
        rename.className = "pin-button";
        rename.title = "Renommer";
        rename.textContent = "✎";
        rename.onclick = () => renameConversation(conversation);
        const remove = document.createElement("button");
        remove.className = "pin-button delete-button";
        remove.title = "Supprimer";
        remove.textContent = "×";
        remove.onclick = () => confirmDeleteConversation(conversation);
        row.append(drag, button, rename, pin, remove);
        group.appendChild(row);
      }
      target.appendChild(group);
    }
  }

  function beginDrag(event, type, id) {
    draggedItem = { type, id };
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", `${type}:${id}`);
    event.stopPropagation();
  }

  function allowDrop(event) {
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
  }

  async function dropOnProject(event, projectId) {
    event.preventDefault();
    event.stopPropagation();
    if (!draggedItem) return;
    if (draggedItem.type === "project") {
      await moveProjectBefore(draggedItem.id, projectId);
    } else {
      await moveConversation(draggedItem.id, projectId);
    }
    draggedItem = null;
  }

  async function dropOnConversation(event, targetConversation) {
    event.preventDefault();
    event.stopPropagation();
    if (draggedItem?.type !== "conversation") return;
    await moveConversation(
      draggedItem.id,
      targetConversation.project_id,
      targetConversation.id
    );
    draggedItem = null;
  }

  async function moveProjectBefore(sourceId, targetId) {
    if (sourceId === targetId) return;
    const source = state.projects.find(item => item.id === sourceId);
    if (!source || !state.projects.some(item => item.id === targetId)) return;
    state.projects = state.projects.filter(item => item.id !== sourceId);
    const targetIndex = state.projects.findIndex(item => item.id === targetId);
    state.projects.splice(targetIndex, 0, source);
    renderConversations();
    await Promise.all(state.projects.map((project, position) =>
      patchProject(project.id, { position })
    ));
  }

  async function moveConversation(sourceId, projectId, beforeId = null) {
    const source = state.conversations.find(item => item.id === sourceId);
    if (!source) return;
    source.project_id = projectId;
    const others = state.conversations.filter(
      item => item.project_id === projectId && item.id !== sourceId
    );
    const index = beforeId
      ? Math.max(0, others.findIndex(item => item.id === beforeId))
      : others.length;
    others.splice(index, 0, source);
    const outside = state.conversations.filter(
      item => item.project_id !== projectId && item.id !== sourceId
    );
    state.conversations = [...outside, ...others];
    renderConversations();
    await Promise.all(others.map((conversation, position) =>
      fetcher(`/api/conversations/${conversation.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project_id: conversation.project_id,
          position
        })
      })
    ));
  }

  async function toggleProjectCollapsed(project) {
    project.collapsed = !project.collapsed;
    renderConversations();
    await patchProject(project.id, { collapsed: project.collapsed });
  }

  function patchProject(projectId, changes) {
    return fetcher(`/api/projects/${projectId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(changes)
    });
  }

  function formatLastCall(timestamp) {
    const date = new Date(Number(timestamp || 0) * 1000);
    if (!Number.isFinite(date.getTime()) || date.getTime() === 0) {
      return { short: "jamais", exact: "Aucun appel" };
    }
    return {
      short: new Intl.DateTimeFormat("fr-FR", {
        day: "numeric",
        month: "short",
        hour: "2-digit",
        minute: "2-digit"
      }).format(date),
      exact: new Intl.DateTimeFormat("fr-FR", {
        dateStyle: "full",
        timeStyle: "short"
      }).format(date)
    };
  }

  function smallButton(label, title, action) {
    const button = document.createElement("button");
    button.className = "project-action";
    button.textContent = label;
    button.title = title;
    button.onclick = action;
    return button;
  }

  async function selectConversation(conversationId) {
    preserveActivePanel();
    closeMobilePanels();
    const conversation = await fetcher(`/api/conversations/${conversationId}`).then(response => response.json());
    state.activeConversationId = conversationId;
    state.activeProjectId = conversation.project_id || "main";
    renderConversations();
    clearConversation();
    $("conversation-title").textContent = conversation.title;
    for (const message of conversation.messages) {
      if (message.role === "user") {
        addMessage("Toi", message.content, "user");
      } else {
        const bubble = addMessage("Joe · synthèse", "", "assistant");
        renderHistoricalRunSummary(message, bubble);
        renderMarkdown(bubble, message.content);
        if (message.git_report) renderGitReport(message.git_report, message.run_id);
      }
    }
    if (!conversation.messages.length) {
      $("messages").innerHTML = '<div class="empty-state"><span class="empty-mark">J</span><h3>Nouvelle conversation</h3><p>Les réglages et l’historique de cette conversation resteront indépendants.</p></div>';
    }
    const conversationViewport = document.querySelector(".conversation");
    conversationViewport.scrollTop = conversationViewport.scrollHeight;
    applySettings(conversation.settings || {});
    restoreConversationPanel(conversationId);
    const running = state.runs.has(conversationId);
    $("send").disabled = false;
    $("send").querySelector("span").textContent = running ? "Mettre en file" : "Lancer";
    $("stop").classList.toggle("hidden", !running);
    $("run-state").textContent = running ? "En cours" : "Prêt";
    $("run-state").className = `run-state ${running ? "running" : "idle"}`;
    if (running) {
      const bubble = addMessage("Joe", "Cette tâche continue en arrière-plan…", "assistant");
      const activeRun = state.runs.get(conversationId);
      activeRun.bubble = bubble;
      for (const event of activeRun.workflow.values()) {
        renderWorkflowUpdate(event, bubble, activeRun.runId);
      }
    }
    renderPromptQueue();
  }

  function preserveActivePanel() {
    if (!state.activeConversationId) return;
    state.panels.set(state.activeConversationId, {
      agentNodes: [...$("agents").children],
      agents: state.agents,
      evidenceNodes: [...$("evidence-log").children],
      rawLog: $("raw-log").textContent,
      runState: $("run-state").textContent,
      runStateClass: $("run-state").className
    });
  }

  function restoreConversationPanel(conversationId) {
    const panel = state.panels.get(conversationId);
    if (!panel) {
      state.agents = new Map();
      $("agents").replaceChildren();
      $("evidence-log").replaceChildren();
      $("raw-log").textContent = "";
      return;
    }
    state.agents = panel.agents;
    $("agents").replaceChildren(...panel.agentNodes);
    $("evidence-log").replaceChildren(...(panel.evidenceNodes || []));
    $("raw-log").textContent = panel.rawLog;
    $("run-state").textContent = panel.runState;
    $("run-state").className = panel.runStateClass;
  }

  async function createConversation(select = true, projectId = state.activeProjectId) {
    const conversation = await fetcher("/api/conversations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_id: projectId })
    }).then(response => response.json());
    if (select) {
      await loadConversations(false);
      await selectConversation(conversation.id);
    }
    return conversation;
  }

  async function renameConversation(conversation) {
    const title = window.prompt("Nouveau nom de la conversation :", conversation.title);
    if (!title?.trim()) return;
    await fetcher(`/api/conversations/${conversation.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: title.trim() })
    });
    await loadConversations(false);
    if (conversation.id === state.activeConversationId) {
      $("conversation-title").textContent = title.trim();
    }
  }

  function confirmDeleteConversation(conversation) {
    state.deletingConversation = conversation;
    $("delete-conversation-name").textContent = conversation.title;
    $("delete-conversation-dialog").showModal();
  }

  async function deleteConversation(event) {
    event.preventDefault();
    const conversation = state.deletingConversation;
    if (!conversation) return;
    const response = await fetcher(`/api/conversations/${conversation.id}`, {
      method: "DELETE"
    });
    if (!response.ok) {
      const payload = await response.json();
      $("delete-conversation-dialog").close();
      window.alert(payload.error || "La conversation n’a pas pu être supprimée.");
      return;
    }
    $("delete-conversation-dialog").close();
    state.deletingConversation = null;
    if (conversation.id === state.activeConversationId) {
      state.activeConversationId = null;
    }
    await loadConversations(false);
    const next = state.conversations.find(
      item => item.project_id === conversation.project_id
    ) || state.conversations[0];
    if (next) await selectConversation(next.id);
  }

  function createProject() {
    state.editingProjectId = null;
    $("project-dialog-title").textContent = "Nouveau sous-projet";
    $("save-project").textContent = "Créer";
    $("project-name").value = "";
    $("project-root").value = "";
    $("project-extra-roots").value = "";
    $("project-remote-access").checked = false;
    $("project-auto-delivery").checked = false;
    $("project-execution-mode").value = "";
    $("project-context").value = "";
    $("project-dialog").showModal();
    requestAnimationFrame(() => $("project-name").focus());
  }

  function openProject(project) {
    state.editingProjectId = project.id;
    $("project-dialog-title").textContent = "Modifier le sous-projet";
    $("save-project").textContent = "Enregistrer";
    $("project-name").value = project.name;
    $("project-root").value = project.workspace_root || "";
    $("project-extra-roots").value = (project.additional_roots || []).join("\n");
    $("project-remote-access").checked = Boolean(project.remote_access);
    $("project-auto-delivery").checked = Boolean(project.auto_commit_push);
    $("project-execution-mode").value = project.default_execution_mode || "";
    $("project-context").value = project.context || "";
    $("project-dialog").showModal();
  }

  async function saveProject(event) {
    event.preventDefault();
    if (!$("project-name").reportValidity()) return;
    const creating = !state.editingProjectId;
    const response = await fetcher(
      creating ? "/api/projects" : `/api/projects/${state.editingProjectId}`,
      {
        method: creating ? "POST" : "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: $("project-name").value,
          workspace_root: $("project-root").value,
          additional_roots: $("project-extra-roots").value
            .split("\n").map(value => value.trim()).filter(Boolean),
          remote_access: $("project-remote-access").checked,
          auto_commit_push: $("project-auto-delivery").checked,
          default_execution_mode: $("project-execution-mode").value,
          context: $("project-context").value
        })
      }
    );
    const project = await response.json();
    if (creating && $("project-context").value) {
      await fetcher(`/api/projects/${project.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ context: $("project-context").value })
      });
    }
    $("project-dialog").close();
    state.activeProjectId = project.id;
    if (creating) {
      await createConversation(true, project.id);
    } else {
      await loadConversations(false);
    }
  }

  async function togglePin(conversation) {
    await fetcher(`/api/conversations/${conversation.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pinned: !conversation.pinned })
    });
    await loadConversations(false);
  }

  return {
    beginDrag,
    createConversation,
    createProject,
    deleteConversation,
    loadConversations,
    moveConversation,
    openProject,
    renameConversation,
    saveProject,
    selectConversation,
    togglePin,
    renderConversations,
    confirmDeleteConversation
  };
};
