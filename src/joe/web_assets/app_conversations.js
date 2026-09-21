window.createJoeConversations = function createJoeConversations({
  state,
  $,
  escapeHtml,
  closeMobilePanels,
  clearConversation,
  addMessage,
  renderAnswer,
  renderGitReport,
  renderHistoricalRunSummary,
  attachPlanControls,
  translate,
  applySettings,
  refreshSelectMenu,
  renderWorkflowUpdate,
  renderPromptQueue,
  fetcher
}) {
  const lastConversationKey = "joe-last-conversation-id";
  const tr = (key, params) => translate ? translate(key, params) : key;
  let draggedItem = null;
  let historyFilter = "";
  let searchTimer = null;
  let renderedConversationId = null;
  let renderedMessageCount = 0;

  $("history-search").addEventListener("input", event => {
    historyFilter = event.target.value.trim().toLocaleLowerCase();
    renderConversations();
    clearTimeout(searchTimer);
    searchTimer = setTimeout(loadGlobalSearch, 180);
  });

  async function loadGlobalSearch() {
    const target = $("global-search-results");
    if (!historyFilter) {
      target.replaceChildren();
      target.classList.add("hidden");
      return;
    }
    const response = await fetcher(
      `/api/search?q=${encodeURIComponent(historyFilter)}`
    );
    if (!response.ok) return;
    const results = await response.json();
    target.replaceChildren();
    for (const result of results.slice(0, 12)) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "global-search-item";
      button.innerHTML = `
        <small>${escapeHtml(result.type || "conversation")}</small>
        <strong>${escapeHtml(result.title)}</strong>
        <span>${escapeHtml(result.snippet || "")}</span>`;
      if (result.task_id) {
        // Un résultat de type tâche doit ouvrir la tâche, pas seulement la
        // conversation qui la porte.
        button.onclick = () => window.dispatchEvent(new CustomEvent(
          "joe:open-task",
          { detail: { taskId: result.task_id } }
        ));
      } else if (result.conversation_id) {
        button.onclick = () => selectConversation(result.conversation_id);
      } else if (result.file_id) {
        button.onclick = () => window.open(
          `/api/files/${encodeURIComponent(result.file_id)}/download?project=${encodeURIComponent(result.project_id || "free")}`,
          "_blank",
          "noopener"
        );
      }
      target.appendChild(button);
    }
    target.classList.toggle("hidden", !target.children.length);
  }

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
      let remembered = "";
      try {
        remembered = window.localStorage.getItem(lastConversationKey) || "";
      } catch {
        // Storage may be unavailable in a hardened/private browser context.
      }
      const initial = state.conversations.find(item => item.id === remembered)
        || state.conversations[0];
      await selectConversation(initial.id);
    }
  }

  // Les noms semés par le serveur ne connaissent pas la langue de l'interface.
  // Tant que l'utilisateur ne les a pas renommés, on les affiche traduits, y
  // compris les anciens noms français d'une installation antérieure.
  const SEEDED_PROJECT_NAMES = {
    "Scratchpad": "project_scratchpad",
    "Conversation libre": "project_scratchpad",
    "Main project": "project_main",
    "Projet principal": "project_main"
  };

  function projectLabel(project) {
    const key = SEEDED_PROJECT_NAMES[project.name];
    return key ? tr(key) : project.name;
  }

  function renderConversations() {
    const target = $("conversations");
    target.replaceChildren();
    let visibleCount = 0;
    for (const project of state.projects) {
      const group = document.createElement("section");
      group.className = `project-group ${project.collapsed ? "collapsed" : ""}`;
      group.dataset.projectId = project.id;
      const header = document.createElement("div");
      header.className = "project-group-head";
      const projectDrag = smallButton("⋮⋮", tr("move_project"), () => {});
      projectDrag.classList.add("drag-handle");
      projectDrag.draggable = true;
      projectDrag.ondragstart = event => beginDrag(event, "project", project.id);
      header.appendChild(projectDrag);
      const projectName = document.createElement("strong");
      projectName.textContent = projectLabel(project);
      header.appendChild(projectName);
      const projectActions = document.createElement("div");
      const collapse = smallButton(
        project.collapsed ? "▸" : "▾",
        tr(project.collapsed ? "expand_conversations" : "collapse_conversations"),
        () => toggleProjectCollapsed(project, group, collapse)
      );
      const editProject = smallButton("⚙", tr("edit_project_context"), () => openProject(project));
      const addConversation = smallButton("＋", tr("add_conversation"), () => createConversation(true, project.id));
      projectActions.append(collapse, editProject, addConversation);
      header.appendChild(projectActions);
      header.ondragover = allowDrop;
      header.ondrop = event => dropOnProject(event, project.id);
      group.appendChild(header);
      const conversations = state.conversations
        .filter(item => item.project_id === project.id)
        .filter(item => {
          if (!historyFilter) return true;
          const text = [item.title, ...(item.messages || []).map(message => message.content || "")]
            .join(" ").toLocaleLowerCase();
          return text.includes(historyFilter);
        })
        .sort((left, right) => Number(right.pinned) - Number(left.pinned));
      visibleCount += conversations.length;
      const conversationList = document.createElement("div");
      conversationList.className = "project-conversations";
      const conversationListInner = document.createElement("div");
      conversationListInner.className = "project-conversations-inner";
      for (const conversation of conversations) {
        const row = document.createElement("div");
        const running = state.runs.has(conversation.id);
        const completed = (
          conversation.unread_completion
          && !running
          && conversation.id !== state.activeConversationId
        );
        row.className = [
          "conversation-item",
          conversation.id === state.activeConversationId ? "active" : "",
          running ? "running" : "",
          completed ? "completed-unread" : ""
        ].filter(Boolean).join(" ");
        row.dataset.conversationId = conversation.id;
        row.ondragover = allowDrop;
        row.ondrop = event => dropOnConversation(event, conversation);
        const drag = document.createElement("button");
        drag.className = "pin-button drag-handle";
        drag.title = tr("move_conversation");
        drag.textContent = "⋮";
        drag.draggable = true;
        drag.ondragstart = event => beginDrag(event, "conversation", conversation.id);
        const button = document.createElement("button");
        button.className = "history-item";
        const date = formatLastCall(conversation.last_call_at);
        const stateLabel = running
          ? `<b class="conversation-status running">${tr("conversation_running")}</b>`
          : completed
          ? `<b class="conversation-status complete">${tr("conversation_complete")}</b>`
          : `${conversation.message_count ?? conversation.messages?.length ?? 0} messages`;
        button.innerHTML = `<strong>${escapeHtml(conversation.title)}</strong><span title="${escapeHtml(date.exact)}">${stateLabel} · ${escapeHtml(date.short)}</span>`;
        button.onclick = () => selectConversation(conversation.id);
        const pin = document.createElement("button");
        pin.className = `pin-button ${conversation.pinned ? "pinned" : ""}`;
        pin.title = tr(conversation.pinned ? "unpin" : "pin");
        pin.textContent = conversation.pinned ? "★" : "☆";
        pin.onclick = () => togglePin(conversation);
        const rename = document.createElement("button");
        rename.className = "pin-button";
        rename.title = tr("rename");
        rename.textContent = "✎";
        rename.onclick = () => renameConversation(conversation);
        const remove = document.createElement("button");
        remove.className = "pin-button delete-button";
        remove.title = tr("delete");
        remove.textContent = "×";
        remove.onclick = () => confirmDeleteConversation(conversation);
        row.append(drag, button, rename, pin, remove);
        conversationListInner.appendChild(row);
      }
      conversationList.appendChild(conversationListInner);
      group.appendChild(conversationList);
      target.appendChild(group);
    }
    const status = $("history-filter-status");
    status.textContent = historyFilter
      ? `${visibleCount} ${tr("messages")}`
      : "";
    if (historyFilter && visibleCount === 0) {
      const empty = document.createElement("div");
      empty.className = "history-empty";
      empty.innerHTML = `<strong>${tr("no_conversation")}</strong><span>${tr("try_another_keyword")}</span>`;
      target.appendChild(empty);
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

  async function toggleProjectCollapsed(project, group, button) {
    project.collapsed = !project.collapsed;
    group.classList.toggle("collapsed", project.collapsed);
    button.textContent = project.collapsed ? "▸" : "▾";
    button.title = tr(project.collapsed ? "expand_conversations" : "collapse_conversations");
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
      return { short: tr("never"), exact: tr("no_call") };
    }
    return {
      short: new Intl.DateTimeFormat(document.documentElement.lang, {
        day: "numeric",
        month: "short",
        hour: "2-digit",
        minute: "2-digit"
      }).format(date),
      exact: new Intl.DateTimeFormat(document.documentElement.lang, {
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

  async function selectConversation(conversationId, runId = "") {
    preserveActivePanel();
    closeMobilePanels();
    const conversation = await fetcher(`/api/conversations/${conversationId}`).then(response => response.json());
    state.activeConversationId = conversationId;
    state.activeProjectId = conversation.project_id || "free";
    try {
      window.localStorage.setItem(lastConversationKey, conversationId);
    } catch {
      // Keeping the conversation usable matters more than persistence.
    }
    if (conversation.unread_completion) {
      conversation.unread_completion = false;
      const cached = state.conversations.find(item => item.id === conversationId);
      if (cached) cached.unread_completion = false;
      await fetcher(`/api/conversations/${conversationId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ unread_completion: false })
      }).catch(() => null);
    }
    renderConversations();
    clearConversation();
    renderedConversationId = conversationId;
    renderedMessageCount = 0;
    $("conversation-title").textContent = conversation.title;
    let requestedMessage = null;
    let lastAssistantBubble = null;
    for (const [messageIndex, message] of conversation.messages.entries()) {
      renderedMessageCount = messageIndex + 1;
      if (message.role === "user" && message.content.startsWith("<autonomous_skill>")) {
        continue;
      }
      let bubble;
      if (message.role === "user") {
        bubble = addMessage(
          "Toi",
          message.content,
          "user",
          { suppressScroll: true }
        );
      } else {
        bubble = addMessage(
          tr("joe_summary"),
          "",
          "assistant",
          { suppressScroll: true }
        );
        renderHistoricalRunSummary(message, bubble);
        renderAnswer(bubble, message.content);
        if (message.git_report) renderGitReport(message.git_report, message.run_id);
        lastAssistantBubble = bubble;
      }
      const wrapper = bubble.closest(".message");
      wrapper.dataset.historyIndex = String(messageIndex);
      if (message.run_id) wrapper.dataset.runId = message.run_id;
      if (!requestedMessage && runId && message.run_id === runId) {
        requestedMessage = wrapper;
      }
    }
    // Un plan encore en attente doit rester lisible et validable dans la fenêtre
    // principale même après un rechargement, pas seulement dans le panneau Tâches.
    attachPlanControls(conversationId, lastAssistantBubble);
    if (!conversation.messages.length) {
      $("messages").innerHTML = `<div class="empty-state"><span class="empty-mark">J</span><h3>${tr("new_conversation")}</h3></div>`;
    }
    const conversationViewport = document.querySelector(".conversation");
    if (requestedMessage) {
      // Positionner le viewport avant le prochain rendu évite de montrer un
      // long défilement depuis le début de la conversation.
      const viewportBox = conversationViewport.getBoundingClientRect();
      const messageBox = requestedMessage.getBoundingClientRect();
      conversationViewport.scrollTop = Math.max(
        0,
        conversationViewport.scrollTop
          + messageBox.top
          - viewportBox.top
          - (conversationViewport.clientHeight - messageBox.height) / 2
      );
      requestedMessage.classList.add("task-focus");
      window.setTimeout(() => requestedMessage.classList.remove("task-focus"), 1800);
    } else {
      conversationViewport.scrollTop = conversationViewport.scrollHeight;
    }
    applySettings(conversation.settings || {});
    window.dispatchEvent(new CustomEvent("joe:conversation-selected"));
    restoreConversationPanel(conversationId);
    const running = state.runs.has(conversationId);
    $("send").disabled = false;
    $("send").querySelector("span").textContent = t(running ? "queue" : "send");
    $("stop").classList.toggle("hidden", !running);
    $("run-state").textContent = t(running ? "running" : "ready");
    $("run-state").className = `run-state ${running ? "running" : "idle"}`;
    if (running) {
      const activeRun = state.runs.get(conversationId);
      const existing = [...document.querySelectorAll(".message.assistant")].find(
        node => activeRun.runId && node.dataset.runId === activeRun.runId
      );
      const bubble = existing?.querySelector(".bubble") || addMessage(
        "Joe",
        tr("background_task"),
        "assistant"
      );
      activeRun.bubble = bubble;
      for (const event of activeRun.workflow.values()) {
        renderWorkflowUpdate(event, bubble, activeRun.runId);
      }
    }
    renderPromptQueue();
  }

  async function refreshActiveConversation() {
    const conversationId = state.activeConversationId;
    if (!conversationId) return;
    const response = await fetcher(`/api/conversations/${conversationId}`);
    if (!response.ok || conversationId !== state.activeConversationId) return;
    const conversation = await response.json();
    const messages = conversation.messages || [];
    if (
      renderedConversationId !== conversationId
      || messages.length < renderedMessageCount
    ) {
      await selectConversation(conversationId);
      return;
    }
    if (messages.length === renderedMessageCount) return;
    const viewport = document.querySelector(".conversation");
    const follow = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight < 48;
    for (let index = renderedMessageCount; index < messages.length; index += 1) {
      const message = messages[index];
      renderedMessageCount = index + 1;
      if (message.role === "user" && message.content.startsWith("<autonomous_skill>")) {
        continue;
      }
      let bubble;
      if (message.role === "user") {
        const existing = [...document.querySelectorAll(".message.user")].find(
          node => (
            message.run_id && node.dataset.runId === message.run_id
          ) || (
            !node.dataset.historyIndex
            && node.querySelector(".bubble")?.dataset.source === message.content
          )
        );
        if (existing) {
          existing.dataset.historyIndex = String(index);
          if (message.run_id) existing.dataset.runId = message.run_id;
          continue;
        }
        bubble = addMessage("Toi", message.content, "user", { suppressScroll: true });
      } else {
        const activeRun = state.runs.get(conversationId);
        const existing = message.run_id && activeRun?.runId === message.run_id
          ? activeRun.bubble?.closest(".message")
          : [...document.querySelectorAll(".message.assistant")].find(
              node => message.run_id && node.dataset.runId === message.run_id
            );
        if (existing) {
          bubble = existing.querySelector(".bubble");
          renderHistoricalRunSummary(message, bubble);
          renderAnswer(bubble, message.content);
          if (message.git_report) renderGitReport(message.git_report, message.run_id);
          existing.dataset.historyIndex = String(index);
          existing.dataset.runId = message.run_id;
          continue;
        }
        bubble = addMessage(tr("joe_summary"), "", "assistant", { suppressScroll: true });
        renderHistoricalRunSummary(message, bubble);
        renderAnswer(bubble, message.content);
        if (message.git_report) renderGitReport(message.git_report, message.run_id);
      }
      const wrapper = bubble.closest(".message");
      wrapper.dataset.historyIndex = String(index);
      if (message.run_id) wrapper.dataset.runId = message.run_id;
    }
    renderedMessageCount = messages.length;
    const cached = state.conversations.find(item => item.id === conversationId);
    if (cached) {
      cached.updated_at = conversation.updated_at;
      cached.message_count = messages.length;
    }
    if (follow) requestAnimationFrame(() => { viewport.scrollTop = viewport.scrollHeight; });
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
      // Le titre part dans la langue de l'interface : le serveur ne peut
      // pas la deviner, et écrivait donc « Nouvelle conversation » en
      // français jusque dans une interface anglaise.
      body: JSON.stringify({ project_id: projectId, title: tr("new_conversation") })
    }).then(response => response.json());
    if (select) {
      await loadConversations(false);
      await selectConversation(conversation.id);
    }
    return conversation;
  }

  async function renameConversation(conversation) {
    const title = window.prompt(tr("rename_conversation_prompt"), conversation.title);
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
      const payload = await response.json().catch(() => ({}));
      $("delete-conversation-dialog").close();
      // 404 : la conversation n'est pas dans le journal servi. Joe a ete
      // relance depuis un autre dossier, et l'echec generique laissait croire
      // a une suppression refusee.
      if (response.status === 404) {
        await loadConversations(false).catch(() => {});
        window.alert(tr("conversation_gone"));
        return;
      }
      window.alert(payload.error || tr("conversation_delete_failed"));
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
    $("project-dialog-title").textContent = tr("new_project");
    $("save-project").textContent = tr("create");
    $("project-name").value = "";
    $("project-root").value = "";
    $("project-extra-roots").value = "";
    $("project-remote-access").checked = true;
    $("project-auto-delivery").checked = false;
    $("project-isolated-worktrees").checked = false;
    $("project-quota-automation").checked = true;
    updateAutoDeliveryHelp();
    $("project-ai-access").value = "manual";
    refreshSelectMenu($("project-ai-access"));
    $("project-context").value = "";
    $("skill-name").value = "";
    $("skill-instructions").value = "";
    $("skill-source").value = "";
    $("project-skills").innerHTML = `<small>${tr("save_project_for_skills")}</small>`;
    setSkillProjectAvailability(false);
    $("trash-project").hidden = true;
    loadGlobalSkills();
    $("project-dialog").showModal();
    requestAnimationFrame(() => $("project-name").focus());
  }

  function openProject(project) {
    state.editingProjectId = project.id;
    $("project-dialog-title").textContent = tr("edit_project");
    $("save-project").textContent = tr("save");
    $("project-name").value = project.name;
    $("project-root").value = project.workspace_root || "";
    $("project-extra-roots").value = (project.additional_roots || []).join("\n");
    $("project-remote-access").checked = project.web_access !== false
      && project.remote_access !== false;
    $("project-auto-delivery").checked = Boolean(project.auto_commit_push);
    $("project-isolated-worktrees").checked = Boolean(project.isolated_worktrees);
    $("project-quota-automation").checked = project.quota_automation !== false;
    updateAutoDeliveryHelp();
    $("project-ai-access").value = project.ai_access || "manual";
    refreshSelectMenu($("project-ai-access"));
    $("project-context").value = project.context || "";
    $("skill-name").value = "";
    $("skill-instructions").value = "";
    $("skill-source").value = "";
    setSkillProjectAvailability(true);
    $("trash-project").hidden = ["main", "free"].includes(project.id);
    loadProjectSkills(project.id).catch(() => {});
    loadGlobalSkills();
    $("project-dialog").showModal();
  }

  function confirmTrashProject() {
    const project = state.projects.find(item => item.id === state.editingProjectId);
    if (!project || ["main", "free"].includes(project.id)) return;
    $("trash-project-name").textContent = project.name;
    $("trash-project-dialog").showModal();
  }

  async function trashProject(event) {
    event.preventDefault();
    const projectId = state.editingProjectId;
    if (!projectId) return;
    const response = await fetcher(`/api/projects/${projectId}/trash`, { method: "POST" });
    const payload = await response.json();
    if (!response.ok) return window.alert(payload.error || tr("project_trash_failed"));
    $("trash-project-dialog").close();
    $("project-dialog").close();
    state.editingProjectId = null;
    if (state.activeProjectId === projectId) {
      state.activeProjectId = "free";
      state.activeConversationId = null;
    }
    await loadConversations(false);
    const activeProjects = new Set(state.projects.map(item => item.id));
    const next = state.conversations.find(item => activeProjects.has(item.project_id));
    if (next) await selectConversation(next.id);
    else clearConversation();
  }

  async function openProjectTrash() {
    const response = await fetcher("/api/project-trash");
    const projects = response.ok ? await response.json() : [];
    const target = $("project-trash-list");
    target.replaceChildren();
    if (!projects.length) {
      const empty = document.createElement("small");
      empty.textContent = tr("empty_trash");
      target.appendChild(empty);
    }
    for (const project of projects) {
      const row = document.createElement("article");
      row.className = "project-trash-row";
      const details = document.createElement("div");
      const name = document.createElement("strong");
      name.textContent = project.name;
      const meta = document.createElement("small");
      meta.textContent = `${project.conversation_count || 0} ${tr("conversations_count")}${project.workspace_root ? ` · ${project.workspace_root}` : ""}`;
      details.append(name, meta);
      const restore = document.createElement("button");
      restore.type = "button";
      restore.textContent = tr("restore");
      restore.onclick = async () => {
        const restored = await fetcher(`/api/projects/${project.id}/restore`, { method: "POST" });
        if (!restored.ok) return;
        await loadConversations(false);
        await openProjectTrash();
      };
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "danger-button";
      remove.textContent = tr("delete_permanently");
      remove.onclick = async () => {
        if (!window.confirm(`${project.name}\n\n${tr("permanent_delete_confirm")}`)) return;
        const deleted = await fetcher(`/api/projects/${project.id}`, { method: "DELETE" });
        if (!deleted.ok) return;
        await openProjectTrash();
      };
      row.append(details, restore, remove);
      target.appendChild(row);
    }
    if (!$("project-trash-dialog").open) $("project-trash-dialog").showModal();
  }

  $("trash-project").onclick = confirmTrashProject;
  $("confirm-trash-project").onclick = trashProject;
  $("open-project-trash").onclick = openProjectTrash;

  const skillScopeLabel = scope => tr({
    project: "project_scope", configured: "configured_scope", global: "shared_scope"
  }[scope] || "project_scope");

  function renderSkillList(target, skills, { emptyText, promotable = false }) {
    target.replaceChildren();
    if (!skills.length) {
      const empty = document.createElement("small");
      empty.textContent = emptyText;
      target.appendChild(empty);
      return;
    }
    for (const skill of skills) {
      const row = document.createElement("span");
      const name = document.createElement("b");
      name.textContent = skill.name;
      const meta = document.createElement("small");
      const scope = skillScopeLabel(skill.scope);
      meta.textContent = skill.active ? `${tr("active_label")} · ${scope}` : scope;
      row.append(name, meta);
      if (promotable && skill.scope === "project") {
        const promote = document.createElement("button");
        promote.type = "button";
        promote.className = "skill-promote";
        promote.textContent = tr("make_shared");
        promote.title = tr("make_shared_help");
        promote.onclick = () => promoteSkill(skill.name);
        row.appendChild(promote);
      }
      // Un skill listé est toujours actif : sans ces deux gestes, on ne peut
      // ni vérifier ce qu'il demande aux fournisseurs, ni le retirer.
      const global = skill.scope === "global";
      const show = document.createElement("button");
      show.type = "button";
      show.className = "skill-show";
      show.textContent = tr("view");
      show.title = tr("view_skill_help");
      show.onclick = () => showSkill(skill.name, global);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "skill-delete";
      remove.textContent = tr("delete");
      remove.title = global
        ? tr("remove_global_skill")
        : tr("remove_project_skill");
      remove.onclick = () => deleteSkill(skill.name, global);
      row.append(show, remove);
      target.appendChild(row);
    }
  }

  async function loadProjectSkills(projectId) {
    const skills = await fetcher(`/api/projects/${projectId}/skills`).then(response => response.json());
    renderSkillList($("project-skills"), skills, {
      emptyText: tr("no_project_skills"),
      promotable: true,
    });
  }

  async function loadGlobalSkills() {
    try {
      const skills = await fetcher("/api/skills/global").then(response => response.json());
      renderSkillList($("global-skills"), skills, {
        emptyText: tr("no_shared_skills"),
      });
    } catch {
      /* liste facultative : on ignore les erreurs réseau */
    }
  }

  function skillPath(name, global) {
    return global
      ? `/api/skills/global/${encodeURIComponent(name)}`
      : `/api/projects/${state.editingProjectId}/skills/${encodeURIComponent(name)}`;
  }

  async function showSkill(name, global) {
    const response = await fetcher(skillPath(name, global));
    const skill = await response.json();
    if (!response.ok) {
      window.alert(skill.message || tr("skill_not_found"));
      return;
    }
    window.alert(`${skill.name}\n\n${skill.content}`);
  }

  async function deleteSkill(name, global) {
    const scope = tr(global ? "all_projects_scope" : "this_project_scope");
    if (!window.confirm(tr("delete_skill_confirm", { name, scope }))) return;
    const response = await fetcher(skillPath(name, global), { method: "DELETE" });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      window.alert(error.message || tr("skill_delete_failed"));
      return;
    }
    await refreshSkills();
  }

  async function refreshSkills() {
    if (state.editingProjectId) await loadProjectSkills(state.editingProjectId);
    await loadGlobalSkills();
  }

  async function promoteSkill(name) {
    if (!state.editingProjectId) return;
    const response = await fetcher(`/api/projects/${state.editingProjectId}/skills/promote`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    const payload = await response.json();
    if (!response.ok) {
      window.alert(payload.message || tr("skill_promote_failed"));
      return;
    }
    await loadGlobalSkills();
  }

  function selectedSkillScope(name) {
    return document.querySelector(`input[name="${name}"]:checked`)?.value || "project";
  }

  function setSkillProjectAvailability(available) {
    for (const name of ["skill-create-scope", "skill-import-scope"]) {
      const projectChoice = document.querySelector(`input[name="${name}"][value="project"]`);
      const globalChoice = document.querySelector(`input[name="${name}"][value="global"]`);
      projectChoice.disabled = !available;
      if (!available) globalChoice.checked = true;
      else projectChoice.checked = true;
    }
  }

  $("create-skill").onclick = async () => {
    const name = $("skill-name").value.trim();
    const instructions = $("skill-instructions").value.trim();
    if (!name || !instructions) {
      window.alert(tr("skill_fields_required"));
      return;
    }
    const scope = selectedSkillScope("skill-create-scope");
    if (scope === "project" && !state.editingProjectId) return;
    const endpoint = scope === "global"
      ? "/api/skills/global/create"
      : `/api/projects/${state.editingProjectId}/skills/create`;
    const response = await fetcher(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, instructions }),
    });
    const payload = await response.json();
    if (!response.ok) {
      window.alert(payload.message || tr("skill_create_failed"));
      return;
    }
    $("skill-name").value = "";
    $("skill-instructions").value = "";
    if (scope === "global") await loadGlobalSkills();
    else await loadProjectSkills(state.editingProjectId);
  };

  $("import-skill").onclick = async () => {
    const source = $("skill-source").value.trim();
    if (!source) return;
    const scope = selectedSkillScope("skill-import-scope");
    if (scope === "project" && !state.editingProjectId) return;
    const endpoint = scope === "global"
      ? "/api/skills/global/import"
      : `/api/projects/${state.editingProjectId}/skills/import`;
    const response = await fetcher(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source }),
    });
    const payload = await response.json();
    if (!response.ok) {
      window.alert(payload.message || tr("skill_import_failed"));
      return;
    }
    $("skill-source").value = "";
    if (scope === "global") await loadGlobalSkills();
    else await loadProjectSkills(state.editingProjectId);
  };

  function updateAutoDeliveryHelp() {
    const help = $("project-auto-delivery-help");
    if (!help) return;
    help.textContent = $("project-auto-delivery").checked
      ? tr("auto_delivery_on")
      : tr("auto_delivery_off");
  }

  $("project-auto-delivery").onchange = updateAutoDeliveryHelp;

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
          web_access: $("project-remote-access").checked,
          auto_commit_push: $("project-auto-delivery").checked,
          isolated_worktrees: $("project-isolated-worktrees").checked,
          quota_automation: $("project-quota-automation").checked,
          ai_access: $("project-ai-access").value,
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
    refreshActiveConversation,
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
