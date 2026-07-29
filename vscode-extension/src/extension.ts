import * as vscode from "vscode";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

import { JoeClient, JoeStatus } from "./api";
import { planRestart, waitForJoe } from "./restart";

const executeFile = promisify(execFile);

type JoeNode =
  | { kind: "status"; status: JoeStatus }
  | { kind: "message"; label: string; description: string };

class JoeOverviewProvider implements vscode.TreeDataProvider<JoeNode> {
  private readonly changed = new vscode.EventEmitter<JoeNode | undefined>();
  readonly onDidChangeTreeData = this.changed.event;

  constructor(private client: JoeClient) {}

  setClient(client: JoeClient): void {
    this.client = client;
    this.refresh();
  }

  refresh(): void {
    this.changed.fire(undefined);
  }

  getTreeItem(node: JoeNode): vscode.TreeItem {
    if (node.kind === "status") {
      const item = new vscode.TreeItem(
        `Joe ${node.status.version} · disponible`,
        vscode.TreeItemCollapsibleState.None
      );
      item.description = "Ouvrir Joe Web pour travailler";
      item.tooltip = [
        `Projet : ${node.status.project}`,
        `API : ${node.status.api_version}`,
        `Agents : ${node.status.providers.join(", ")}`,
      ].join("\n");
      item.iconPath = new vscode.ThemeIcon("pass-filled");
      item.command = {
        command: "joe.openWeb",
        title: "Ouvrir Joe Web",
      };
      return item;
    }
    const item = new vscode.TreeItem(
      node.label,
      vscode.TreeItemCollapsibleState.None
    );
    item.description = node.description;
    item.tooltip = "Démarre Joe, puis ouvre son interface Web.";
    item.iconPath = new vscode.ThemeIcon("circle-slash");
    item.command = {
      command: "joe.start",
      title: "Démarrer Joe",
    };
    return item;
  }

  async getChildren(): Promise<JoeNode[]> {
    try {
      return [{ kind: "status", status: await this.client.status() }];
    } catch (error) {
      return [{
        kind: "message",
        label: "Joe indisponible",
        description: error instanceof Error ? error.message : String(error),
      }];
    }
  }
}

export function activate(context: vscode.ExtensionContext): void {
  const serverUrl = (): string =>
    vscode.workspace.getConfiguration("joe").get(
      "serverUrl",
      "http://127.0.0.1:8765"
    );
  const workspace = (): string | undefined =>
    vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  const provider = new JoeOverviewProvider(new JoeClient(serverUrl()));
  const tree = vscode.window.createTreeView("joe.overview", {
    treeDataProvider: provider,
  });

  const openWeb = async (): Promise<void> => {
    await new JoeClient(serverUrl()).status();
    const externalUrl = await vscode.env.asExternalUri(
      vscode.Uri.parse(serverUrl())
    );
    await vscode.env.openExternal(externalUrl);
  };

  context.subscriptions.push(
    tree,
    vscode.commands.registerCommand("joe.refresh", () => provider.refresh()),
    vscode.commands.registerCommand("joe.openWeb", async () => {
      try {
        await openWeb();
      } catch (error) {
        showError(error, "Joe est indisponible. Utilise d’abord Démarrer.");
      }
    }),
    vscode.commands.registerCommand("joe.start", async () => {
      if (!vscode.workspace.isTrusted) {
        void vscode.window.showWarningMessage(
          "Le démarrage de Joe exige un workspace approuvé."
        );
        return;
      }
      const project = workspace();
      if (!project) {
        void vscode.window.showWarningMessage(
          "Ouvre d’abord le dossier du projet à utiliser avec Joe."
        );
        return;
      }
      const endpoint = new URL(serverUrl());
      try {
        await executeFile("joe", [
          "web",
          "-C",
          project,
          "--host",
          endpoint.hostname,
          "--port",
          endpoint.port || "8765",
          "--no-browser",
        ]);
        await waitForJoe(new JoeClient(serverUrl()));
        provider.refresh();
        await openWeb();
      } catch (error) {
        showError(
          error,
          "Impossible de démarrer Joe. Sans tmux, lance `joe web` dans un terminal."
        );
      }
    }),
    vscode.commands.registerCommand("joe.restart", async () => {
      if (!vscode.workspace.isTrusted) {
        void vscode.window.showWarningMessage(
          "Le redémarrage de Joe exige un workspace approuvé."
        );
        return;
      }
      const plan = await planRestart(
        new JoeClient(serverUrl()),
        workspace()
      );
      if (plan.mode === "blocked" || plan.mode === "error") {
        void vscode.window.showWarningMessage(plan.message);
        return;
      }
      const label = plan.mode === "force"
        ? "Forcer le redémarrage"
        : "Redémarrer";
      const confirmation = await vscode.window.showWarningMessage(
        plan.mode === "force"
          ? "Joe ne répond pas. Forcer le redémarrage peut interrompre une tâche."
          : `Redémarrer Joe pour ${plan.project} ?`,
        { modal: true },
        label
      );
      if (confirmation !== label) {
        return;
      }
      const endpoint = new URL(serverUrl());
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
      try {
        await executeFile("joe", args);
        await waitForJoe(new JoeClient(serverUrl()));
        provider.refresh();
        await openWeb();
      } catch (error) {
        showError(error, "Impossible de redémarrer Joe.");
      }
    }),
    vscode.commands.registerCommand("joe.stop", async () => {
      const confirmation = await vscode.window.showWarningMessage(
        "Arrêter cette instance Joe ? Les tâches actives seront interrompues.",
        { modal: true },
        "Arrêter Joe"
      );
      if (confirmation !== "Arrêter Joe") {
        return;
      }
      const endpoint = new URL(serverUrl());
      try {
        await executeFile("joe", [
          "stop",
          "--port",
          endpoint.port || "8765",
        ]);
        provider.refresh();
        void vscode.window.showInformationMessage("Joe a été arrêté.");
      } catch (error) {
        showError(error, "Impossible d’arrêter Joe.");
      }
    }),
    vscode.commands.registerCommand("joe.openGuide", async () => {
      const document = await vscode.workspace.openTextDocument(
        vscode.Uri.joinPath(context.extensionUri, "README.md")
      );
      await vscode.window.showTextDocument(document, { preview: true });
    }),
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration("joe.serverUrl")) {
        provider.setClient(new JoeClient(serverUrl()));
      }
    })
  );
}

export function deactivate(): void {}

function showError(error: unknown, fallback: string): void {
  const detail =
    typeof error === "object" && error && "stderr" in error
      ? String(error.stderr).trim()
      : error instanceof Error
        ? error.message
        : String(error);
  void vscode.window.showErrorMessage(detail || fallback);
}
