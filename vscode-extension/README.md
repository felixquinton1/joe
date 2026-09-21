# Joe for VS Code

This extension is a lightweight launcher for Joe Web.

## Requirements

- open a project with VS Code, locally or through Remote-SSH;
- install the extension on the workspace host;
- install the `joe` command on that same host;
- keep the default server address, `http://127.0.0.1:8765`, unless needed.

## Controls

- `▶` starts Joe for the current workspace;
- `🌐` opens Joe Web, including Remote-SSH port forwarding;
- `↻` refreshes server status;
- `⟳` restarts the instance on the configured port;
- `■` stops that instance;
- `?` opens this guide.

Conversations, prompts, models, permissions, and results stay in Joe Web. The
extension deliberately avoids maintaining a second chat interface.

Automatic start, restart, and stop currently use `tmux`. Without `tmux`, run
`joe web` in a terminal and use **Open Joe Web** from the extension.

## Development

```bash
npm ci
npm run check
npm test
npm run package
```

Le `.vsix` produit reste local et n’est pas commité.
