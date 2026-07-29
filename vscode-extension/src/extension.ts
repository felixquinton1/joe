import * as vscode from "vscode";

import { JoeClient, JoeConversation, JoeStatus } from "./api";

type JoeNode =
  | { kind: "status"; status: JoeStatus }
  | { kind: "conversation"; conversation: JoeConversation }
  | { kind: "message"; label: string; description?: string };

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
        `Joe ${node.status.version}`,
        vscode.TreeItemCollapsibleState.None
      );
      item.description = `API ${node.status.api_version}`;
      item.tooltip = `${node.status.project}\n${node.status.providers.join(", ")}`;
      item.iconPath = new vscode.ThemeIcon("pass-filled");
      return item;
    }
    if (node.kind === "conversation") {
      const item = new vscode.TreeItem(
        node.conversation.title || "Conversation sans titre",
        vscode.TreeItemCollapsibleState.None
      );
      item.description = node.conversation.project_id || undefined;
      item.iconPath = new vscode.ThemeIcon("comment-discussion");
      return item;
    }
    const item = new vscode.TreeItem(
      node.label,
      vscode.TreeItemCollapsibleState.None
    );
    item.description = node.description;
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
  const provider = new JoeOverviewProvider(new JoeClient(serverUrl()));
  const tree = vscode.window.createTreeView("joe.overview", {
    treeDataProvider: provider,
  });

  context.subscriptions.push(
    tree,
    vscode.commands.registerCommand("joe.refresh", () => provider.refresh()),
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration("joe.serverUrl")) {
        provider.setClient(new JoeClient(serverUrl()));
      }
    })
  );
}

export function deactivate(): void {}
