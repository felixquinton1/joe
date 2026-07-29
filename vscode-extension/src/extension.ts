import * as vscode from "vscode";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

import {
  JoeApiError,
  JoeClient,
  JoeConversation,
  JoeRunEvent,
  JoeStatus,
} from "./api";
import { planRestart, waitForJoe } from "./restart";
import {
  chooseRunToResume,
  completedResponse,
  JoeRunBookmark,
  updateBookmark,
} from "./runRecovery";

const executeFile = promisify(execFile);

type JoeNode =
  | { kind: "status"; status: JoeStatus }
  | { kind: "conversation"; conversation: JoeConversation }
  | { kind: "message"; label: string; description?: string };

class JoeOverviewProvider implements vscode.TreeDataProvider<JoeNode> {
  private readonly changed = new vscode.EventEmitter<JoeNode | undefined>();
  readonly onDidChangeTreeData = this.changed.event;

  constructor(
    private client: JoeClient,
    private selectedConversationId?: string
  ) {}

  setClient(client: JoeClient): void {
    this.client = client;
    this.refresh();
  }

  refresh(): void {
    this.changed.fire(undefined);
  }

  select(conversationId: string): void {
    this.selectedConversationId = conversationId;
    this.refresh();
  }

  getTreeItem(node: JoeNode): vscode.TreeItem {
    if (node.kind === "status") {
      const item = new vscode.TreeItem(
        `Joe ${node.status.version}`,
        vscode.TreeItemCollapsibleState.None
      );
      item.description = `API ${node.status.api_version}`;
      item.tooltip = [
        `Projet : ${node.status.project}`,
        `Agents disponibles : ${node.status.providers.join(", ")}`,
        "Actualise la vue si l’état du serveur change.",
      ].join("\n");
      item.iconPath = new vscode.ThemeIcon("pass-filled");
      return item;
    }
    if (node.kind === "conversation") {
      const item = new vscode.TreeItem(
        node.conversation.title || "Conversation sans titre",
        vscode.TreeItemCollapsibleState.None
      );
      item.description = [
        node.conversation.id === this.selectedConversationId
          ? "sélectionnée"
          : "",
        node.conversation.project_id || "",
      ].filter(Boolean).join(" · ") || undefined;
      item.tooltip = [
        node.conversation.title || "Conversation sans titre",
        "Cliquer pour sélectionner cette conversation.",
        "Les permissions enregistrées restent appliquées.",
      ].join("\n");
      item.iconPath = new vscode.ThemeIcon(
        node.conversation.id === this.selectedConversationId
          ? "check"
          : "comment-discussion"
      );
      item.command = {
        command: "joe.selectConversation",
        title: "Sélectionner la conversation",
        arguments: [node.conversation],
      };
      return item;
    }
    const item = new vscode.TreeItem(
      node.label,
      vscode.TreeItemCollapsibleState.None
    );
    item.description = node.description;
    item.tooltip = node.description;
    item.iconPath = new vscode.ThemeIcon("warning");
    return item;
  }

  async getChildren(): Promise<JoeNode[]> {
    try {
      const [status, conversations] = await Promise.all([
        this.client.status(),
        this.client.conversations(),
      ]);
      return [
        { kind: "status", status },
        ...conversations.map(
          (conversation): JoeNode => ({ kind: "conversation", conversation })
        ),
      ];
    } catch (error) {
      return [
        {
          kind: "message",
          label: "Joe indisponible",
          description: error instanceof Error ? error.message : String(error),
        },
      ];
    }
  }
}

export function activate(context: vscode.ExtensionContext): void {
  const serverUrl = (): string =>
    vscode.workspace.getConfiguration("joe").get(
      "serverUrl",
      "http://127.0.0.1:8765"
    );
  let selectedConversationId = context.workspaceState.get<string>(
    "joe.selectedConversationId"
  );
  let currentRun = context.workspaceState.get<JoeRunBookmark>(
    "joe.currentRun"
  );
  let currentRunId: string | undefined;
  let currentRunAbort: AbortController | undefined;
  const output = vscode.window.createOutputChannel("Joe");
  const provider = new JoeOverviewProvider(
    new JoeClient(serverUrl()),
    selectedConversationId
  );
  const tree = vscode.window.createTreeView("joe.overview", {
    treeDataProvider: provider,
  });

  const saveRun = async (
    bookmark: JoeRunBookmark | undefined
  ): Promise<void> => {
    currentRun = bookmark;
    await context.workspaceState.update("joe.currentRun", bookmark);
  };

  const followRun = async (
    initialBookmark: JoeRunBookmark,
    reconnecting = false
  ): Promise<void> => {
    if (currentRunId) {
      return;
    }
    let bookmark = initialBookmark;
    currentRunId = bookmark.runId;
    currentRunAbort = new AbortController();
    await saveRun(bookmark);
    output.clear();
    if (reconnecting) {
      output.appendLine(
        `[Reprise de la tâche ${bookmark.runId.slice(0, 8)} après l’événement ${bookmark.lastEventId}]`
      );
      if (bookmark.streamTruncated) {
        output.appendLine(
          "[Le début du flux reste disponible dans l’historique Joe.]"
        );
      }
      if (bookmark.streamedText) {
        output.append(bookmark.streamedText);
      }
    } else {
      output.appendLine(`Vous : ${bookmark.request}`);
      output.appendLine("");
    }
    output.show(true);
    let streamedText = bookmark.streamedText;
    let terminalEventSeen = false;
    try {
      const client = new JoeClient(serverUrl());
      for await (const event of client.events(
        bookmark.runId,
        bookmark.lastEventId,
        currentRunAbort.signal
      )) {
        streamedText = renderEvent(output, event, streamedText);
        bookmark = updateBookmark(bookmark, event, streamedText);
        await saveRun(bookmark);
        terminalEventSeen =
          terminalEventSeen ||
          ["complete", "error", "cancelled"].includes(event.type);
      }
      if (terminalEventSeen) {
        await saveRun(undefined);
      }
      provider.refresh();
    } catch (error) {
      if (error instanceof JoeApiError && error.status === 404) {
        try {
          const conversation = await new JoeClient(serverUrl()).conversation(
            bookmark.conversationId
          );
          const response = completedResponse(conversation, bookmark.runId);
          if (response) {
            output.appendLine("\n\n[Résultat retrouvé dans l’historique]");
            output.appendLine(response);
          } else {
            output.appendLine(
              "\n[La tâche n’est plus active et aucun résultat final n’a été trouvé.]"
            );
          }
          await saveRun(undefined);
        } catch (historyError) {
          showError(historyError);
        }
      } else if (!(error instanceof Error && error.name === "AbortError")) {
        output.appendLine(
          "\n[Connexion interrompue. Joe reprendra cette tâche au prochain chargement.]"
        );
        showError(error);
      }
    } finally {
      currentRunId = undefined;
      currentRunAbort = undefined;
    }
  };

  const resumeKnownRun = async (): Promise<void> => {
    if (currentRunId) {
      return;
    }
    try {
      const client = new JoeClient(serverUrl());
      await client.status();
      const bookmark = chooseRunToResume(
        await client.activeRuns(),
        currentRun,
        selectedConversationId
      );
      if (bookmark) {
        selectedConversationId = bookmark.conversationId;
        await context.workspaceState.update(
          "joe.selectedConversationId",
          bookmark.conversationId
        );
        provider.select(bookmark.conversationId);
        await followRun(bookmark, true);
      }
    } catch (error) {
      if (currentRun) {
        output.appendLine(
          "[Joe est indisponible. La tâche mémorisée sera reprise plus tard.]"
        );
      }
    }
  };

  context.subscriptions.push(
    tree,
    output,
    vscode.commands.registerCommand("joe.refresh", () => {
      provider.refresh();
      void resumeKnownRun();
    }),
    vscode.commands.registerCommand("joe.openGuide", async () => {
      const document = await vscode.workspace.openTextDocument(
        vscode.Uri.joinPath(context.extensionUri, "README.md")
      );
      await vscode.window.showTextDocument(document, { preview: true });
    }),
    vscode.commands.registerCommand("joe.zoomIn", () =>
      vscode.commands.executeCommand("workbench.action.zoomIn")
    ),
    vscode.commands.registerCommand("joe.zoomOut", () =>
      vscode.commands.executeCommand("workbench.action.zoomOut")
    ),
    vscode.commands.registerCommand("joe.zoomReset", () =>
      vscode.commands.executeCommand("workbench.action.zoomReset")
    ),
    vscode.commands.registerCommand(
      "joe.selectConversation",
      async (conversation: JoeConversation) => {
        selectedConversationId = conversation.id;
        await context.workspaceState.update(
          "joe.selectedConversationId",
          conversation.id
        );
        provider.select(conversation.id);
        void vscode.window.showInformationMessage(
          `Conversation sélectionnée : ${conversation.title || conversation.id}`
        );
      }
    ),
    vscode.commands.registerCommand("joe.createConversation", async () => {
      if (!vscode.workspace.isTrusted) {
        void vscode.window.showWarningMessage(
          "La création d’une conversation exige un workspace approuvé."
        );
        return;
      }
      try {
        const conversation = await new JoeClient(
          serverUrl()
        ).createConversation();
        selectedConversationId = conversation.id;
        await context.workspaceState.update(
          "joe.selectedConversationId",
          conversation.id
        );
        provider.select(conversation.id);
        void vscode.window.showInformationMessage(
          "Nouvelle conversation Joe sélectionnée."
        );
      } catch (error) {
        showError(error);
      }
    }),
    vscode.commands.registerCommand("joe.sendPrompt", async () => {
      if (!vscode.workspace.isTrusted) {
        void vscode.window.showWarningMessage(
          "L’envoi d’une demande exige un workspace approuvé."
        );
        return;
      }
      if (currentRunId) {
        void vscode.window.showWarningMessage(
          "Une requête Joe est déjà en cours dans cette fenêtre."
        );
        return;
      }
      if (!selectedConversationId) {
        void vscode.window.showWarningMessage(
          "Sélectionne ou crée d’abord une conversation Joe."
        );
        return;
      }
      const request = await vscode.window.showInputBox({
        title: "Envoyer une demande à Joe",
        prompt: "La demande utilise les réglages enregistrés de la conversation.",
        ignoreFocusOut: true,
      });
      if (!request?.trim()) {
        return;
      }
      const client = new JoeClient(serverUrl());
      try {
        await client.status();
        const executionMode = vscode.workspace.getConfiguration("joe").get(
          "allowWorkspaceWrites",
          false
        )
          ? undefined
          : "read-only";
        let runId: string;
        try {
          runId = await client.startRun(
            selectedConversationId,
            request.trim(),
            executionMode
          );
        } catch (error) {
          if (!(error instanceof JoeApiError && error.status === 428)) {
            throw error;
          }
          const approval = await vscode.window.showWarningMessage(
            "Cette tâche demande un accès complet au projet et aux commandes.",
            {
              modal: true,
              detail: "L’autorisation vaut uniquement pour ce run.",
            },
            "Autoriser une fois"
          );
          if (approval !== "Autoriser une fois") {
            return;
          }
          runId = await client.startRun(
            selectedConversationId,
            request.trim(),
            executionMode,
            true
          );
        }
        await followRun({
          runId,
          conversationId: selectedConversationId,
          request: request.trim(),
          lastEventId: 0,
          streamedText: "",
        });
      } catch (error) {
        showError(error);
      }
    }),
    vscode.commands.registerCommand("joe.cancelRun", async () => {
      if (!currentRunId) {
        void vscode.window.showInformationMessage(
          "Aucune requête Joe lancée depuis cette fenêtre."
        );
        return;
      }
      try {
        if (await new JoeClient(serverUrl()).cancelRun(currentRunId)) {
          output.appendLine("\n[Annulation demandée]");
        }
      } catch (error) {
        showError(error);
      }
    }),
    vscode.commands.registerCommand("joe.restart", async () => {
      if (!vscode.workspace.getConfiguration("joe").get(
        "enableMaintenanceActions",
        false
      )) {
        void vscode.window.showWarningMessage(
          "Active d’abord « Joe: Enable Maintenance Actions » dans les réglages."
        );
        return;
      }
      if (!vscode.workspace.isTrusted) {
        void vscode.window.showWarningMessage(
          "Le redémarrage de Joe exige un workspace approuvé."
        );
        return;
      }
      const client = new JoeClient(serverUrl());
      const plan = await planRestart(
        client,
        vscode.workspace.workspaceFolders?.[0]?.uri.fsPath
      );
      if (plan.mode === "blocked") {
        void vscode.window.showWarningMessage(plan.message);
        return;
      }
      if (plan.mode === "error") {
        void vscode.window.showErrorMessage(plan.message);
        return;
      }
      if (plan.mode === "force") {
        const forced = await vscode.window.showWarningMessage(
          "Joe ne répond pas. Forcer son redémarrage peut interrompre une tâche.",
          { modal: true },
          "Forcer le redémarrage"
        );
        if (forced !== "Forcer le redémarrage") {
          return;
        }
      } else {
        const confirmation = await vscode.window.showWarningMessage(
          `Redémarrer Joe pour ${plan.project} ?`,
          { modal: true },
          "Redémarrer"
        );
        if (confirmation !== "Redémarrer") {
          return;
        }
      }
      const endpoint = new URL(serverUrl());
      try {
        const args = [
          "restart",
          "-C",
          plan.project,
          "--host",
          endpoint.hostname,
          "--port",
          endpoint.port || "8765",
        ];
        if (plan.mode === "force") {
          args.push("--force");
        }
        await executeFile("joe", args);
        await waitForJoe(new JoeClient(serverUrl()));
        provider.refresh();
        void vscode.window.showInformationMessage("Joe a été redémarré.");
        const externalUrl = await vscode.env.asExternalUri(
          vscode.Uri.parse(serverUrl())
        );
        await vscode.env.openExternal(externalUrl);
      } catch (error) {
        const detail =
          typeof error === "object" && error && "stderr" in error
            ? String(error.stderr).trim()
            : error instanceof Error
              ? error.message
              : String(error);
        void vscode.window.showErrorMessage(
          detail || "Impossible de redémarrer Joe."
        );
      }
    }),
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration("joe.serverUrl")) {
        provider.setClient(new JoeClient(serverUrl()));
      }
    })
  );

  void resumeKnownRun();
}

export function deactivate(): void {}

function renderEvent(
  output: vscode.OutputChannel,
  event: JoeRunEvent,
  streamedText: string
): string {
  if (event.type === "stream" && typeof event.text === "string") {
    output.append(event.text);
    return streamedText + event.text;
  }
  if (event.type === "complete" && typeof event.response === "string") {
    if (event.response.trim() !== streamedText.trim()) {
      if (streamedText) {
        output.appendLine("\n\nRésultat final :");
      }
      output.appendLine(event.response);
    }
    output.appendLine("\n[Terminé]");
    return streamedText;
  }
  if (event.type === "error") {
    output.appendLine(`\n[Erreur] ${event.message || "Erreur Joe"}`);
    return streamedText;
  }
  if (event.type === "cancelled") {
    output.appendLine("\n[Annulé]");
  }
  return streamedText;
}

function showError(error: unknown): void {
  const prefix =
    error instanceof JoeApiError && error.status === 409
      ? "Conversation occupée"
      : "Erreur Joe";
  const detail = error instanceof Error ? error.message : String(error);
  void vscode.window.showErrorMessage(`${prefix} : ${detail}`);
}
