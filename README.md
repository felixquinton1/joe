# Joe

Joe is a lightweight local orchestrator for Codex, Claude Code, Gemini CLI, and
GitHub Copilot CLI. It routes a natural-language request without requiring
workflow verbs, carries compact project context between providers, and stores
full run logs outside the active prompt.

## Usage

```bash
joe "Ajoute une option pour désactiver la loss Gamma"
joe
joe chat
joe --agent claude "Qu'en pense l'autre ?"
joe --mode review "Vérifie puis corrige ce changement"
joe --dry-run "Propose une architecture de cache"
joe web -C /path/to/project
```

Run `joe --help` for all options. Joe creates `.agentflow/` in the target
project for compatibility with the original memory layout. The product and
executable are named Joe.

## Safety model

- Analysis and review calls are read-only.
- Implementation calls may edit only the selected working directory.
- FAST makes one provider call unless that provider fails.
- REVIEW makes one primary call and one read-only review call.
- CONSENSUS makes two independent read-only proposals, two cross-reviews, and
  one synthesis. It never edits the repository.
- Full stdout/stderr live under `.agentflow/runs/`; active memory is rewritten
  with bounded content.

`config.yaml` is JSON-compatible YAML so Joe can stay dependency-free.

## Local web interface

Start the control room from the project you want the agents to work on:

```bash
cd /path/to/project
joe
```

Joe opens `http://127.0.0.1:8765`. The interface streams provider stdout and
stderr, shows provider status and personal quota resets in a compact top-right
popover when the provider CLI exposes them, provides stable
agent/workflow/model/effort/permission controls,
and loads the latest 100 run logs from the project's `.agentflow/runs/` folder.
Joe never estimates missing quotas: unsupported providers are marked clearly.
Native JSON streams expose observable progress such as commands, tools, and
files read or changed; Joe does not expose private chain-of-thought.
Final answers are rendered locally as safe Markdown, including headings,
tables, lists, links, inline code, and fenced code blocks.
Use `joe chat` for the original terminal conversation and `joe web` when you
need explicit web-server options.

Use `joe web --no-browser` on a remote server when automatic browser opening is
not useful, then forward the port:

```bash
ssh -L 8765:127.0.0.1:8765 user@server
```

Open `http://127.0.0.1:8765` on your computer. The server binds only to
localhost by default.

## Provider updates

Joe can detect installed CLI version changes without consuming AI quota:

```bash
joe sync
```

To let Codex inspect and implement relevant provider features, followed by a
read-only Claude review:

```bash
joe sync --apply
```

Use `joe sync --apply --force` for a full audit when versions have not changed.
The implementation mode does not commit automatically, so its diff remains
available for inspection and testing.
