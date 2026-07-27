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
- REVIEW makes one primary call and one read-only review call, followed by at
  most one justified correction pass.
- CONSENSUS makes two independent read-only proposals, two cross-reviews, and
  one synthesis. It never edits the repository.
- Full stdout/stderr live under `.agentflow/runs/`; active memory is rewritten
  with bounded content.

`config.yaml` is JSON-compatible YAML so Joe can stay dependency-free.

## Storage boundaries

Joe keeps product code and user data separate:

- the Joe repository contains only source code, tests, and documentation;
- `<project>/.agentflow/project.md` and `config.yaml` are stable, shareable
  project context that may be committed deliberately;
- conversations, run logs, `session.md`, `handoff.md`, rejection patches, and
  local backups are ignored by the target project's Git repository;
- every conversation write is also mirrored outside all repositories under
  `$XDG_DATA_HOME/joe/backups/<project-id>/conversations.json` (default:
  `~/.local/share/joe/backups/...`).

Each generated `.agentflow/` contains its own `.gitignore`, so this separation
also applies to projects that do not yet have a root `.gitignore`.

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
Gemini headless runs expose per-model token and request statistics but not the
global remaining percentage. Joe records those local statistics under
`$XDG_DATA_HOME/joe/gemini_usage.json` and displays today's Joe consumption,
last call, and models used. The exact global snapshot remains available through
`/stats model` in an interactive Gemini CLI session.
Automatic FAST routing remains task-first, then balances Codex and Claude when
the preferred provider has 20% or less remaining and its reset is at least 24
hours away. Short five-hour windows therefore remain useful instead of being
prematurely preserved. At 5% or less it switches regardless of reset distance. A manually
selected provider always wins, and REVIEW/CONSENSUS semantics are unchanged.
Routing itself is deterministic and does not call a model. It never waits for
fresh quota or model-catalog probes: the latest background cache is used, and
the interface reports the local routing duration separately from provider
processing. Naming exactly one provider in a request (for example, "teste
Gemini") routes directly to that provider unless the menu explicitly overrides
it.
Claude routing uses the official local cache refreshed by `/usage`; Joe does
not send `/usage` as a paid non-interactive model prompt.
For requests that are moderately complex, Joe dynamically selects the first
current Codex/Claude model exposed by the installed CLI and uses `high`
reasoning effort. Explicit model and effort choices always take precedence.
Capability questions and other simple FAST answers use `low` effort, even when
the question is long. Question wording such as "est-ce que tu peux..." is not
treated as an implementation order merely because it mentions modifying code.
Large implementation requests are routed to REVIEW automatically: the primary
agent implements, the other agent audits without editing, and the primary gets
at most one correction pass when the reviewer explicitly reports justified
issues. During CONSENSUS, Joe shows each completed proposal and cross-review in
a structured progress panel; only the final synthesis is posted as Joe's global
answer.
If a provider reaches a limit during a run, Joe adds a visible notice with the
known reset windows (including model-specific Claude windows when exposed) and
the installed fallback providers/models it will try automatically. A model
switch within the same provider is not presented as a way around a shared
quota.
Native JSON streams expose observable progress such as commands, tools, and
files read or changed; Joe does not expose private chain-of-thought.
Final answers are rendered locally as safe Markdown, including headings,
tables, lists, links, inline code, and fenced code blocks.

Conversations are persistent per project. Each conversation keeps its full
message history and independent agent, workflow, model, effort, and permission
settings. Conversations can be pinned and several can run concurrently; avoid
launching concurrent write tasks against the same files.
While a conversation is running, additional prompts can be queued with their
current agent/model settings. They start in order after the active response and
can be copied or removed before execution. Copy controls are also available on
user prompts, final answers, proposals, reviews, and implementation plans.

An active run can be interrupted from its conversation. Joe terminates the
provider process tree, removes the unfinished turn, restores the original
prompt in the composer, and lets it be edited and relaunched. Scrollable panels
follow new content only while the user remains near the bottom.

After every run, Joe independently records the branch, the local HEAD and
`origin/dev` before and after execution. It displays the resulting file list
with insertion/deletion counts, so a provider cannot merely claim that a fetch
or merge happened. File changes are kept by default. They can be rejected when
the repository was clean at task start, HEAD did not change, and no concurrent
run makes an exact restoration unsafe. Commits and merges are reported but are
never automatically rewritten. Codex's explicit full-access permission is
available for Git metadata writes such as fetch, pull, and merge.

Within one repository, conversations can be grouped into logical sub-projects.
The repository-level `project.md` remains global, while each sub-project adds a
shared context automatically injected into all its conversations. Conversation
and sub-project names can be edited from the sidebar.
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
