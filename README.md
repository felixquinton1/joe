# Joe

> [!IMPORTANT]
> Joe is an orchestrator, not an AI provider or a billing intermediary. It calls
> the third-party CLIs, accounts and APIs configured by the user. The user is
> responsible for provider charges, quotas, granted permissions and stopping
> active work. Closing Joe Web or VS Code does not necessarily stop a server-side
> run. Autonomous features are experimental and can chain model calls, commands
> and experiments without further intervention; always configure limits and
> monitor active campaigns.

Joe is a lightweight local orchestrator for Codex, Claude Code, Gemini CLI,
GitHub Copilot CLI, and Cursor CLI. It routes a natural-language request without requiring
workflow verbs, carries compact project context between providers, and stores
full run logs outside the active prompt.

Joe is open source under the [Mozilla Public License 2.0](LICENSE). Copyright
© 2026 Félix Quinton. The license covers the code, not the Joe name, logo or
identity of the official project; see [TRADEMARKS.md](TRADEMARKS.md). External
code contributions are governed by [CONTRIBUTING.md](CONTRIBUTING.md).

## Install

Joe supports Python 3.10+ on Linux, macOS, and Windows. The repository package
can be installed in an isolated environment with:

```bash
pipx install git+https://github.com/felixquinton1/joe.git
joe doctor
```

The provider CLIs remain separate prerequisites and must already be
authenticated. See the [short user guide](docs/user-guide.md) for installation,
VS Code/Remote-SSH, safe defaults, zoom, and troubleshooting.

Full-access runs require a one-time confirmation in Joe Web or VS Code before
the provider process starts.

## Usage

```bash
joe "Ajoute une option pour désactiver la loss Gamma"
joe
joe cli
joe chat
joe --agent claude "Qu'en pense l'autre ?"
joe --mode review "Vérifie puis corrige ce changement"
joe --dry-run "Propose une architecture de cache"
joe web -C /path/to/project
```

Run `joe --help` for all options. Joe creates `.agentflow/` in the target
project for compatibility with the original memory layout. The product and
executable are named Joe.

## VS Code extension

The extension lives in `vscode-extension/`. It runs in the workspace extension
host, including Remote-SSH, and connects to Joe on the same host.

```bash
cd vscode-extension
npm ci
npm test
npm run package
```

Install the generated `.vsix` in the VS Code window connected to the target
host, then open the Joe activity-bar view. The extension deliberately remains
a small launcher: start, open, refresh, restart or stop Joe Web. Conversations,
prompts, models and results stay in the Web interface so there is only one chat
experience to maintain.

## Safety model

- Analysis and review calls are read-only.
- Implementation calls may edit only the selected working directory.
- Explicit permissions survive every fallback. Joe normalizes provider-specific
  names such as `plan`, `read-only`, and `dontAsk`, then translates the same or
  a stricter access level for the fallback provider.
- FAST makes one provider call unless that provider fails.
- REVIEW makes one primary call and one read-only review call, followed by at
  most one justified correction pass.
- CONSENSUS runs two independent read-only proposals in parallel, then the two
  cross-reviews in parallel, and finally one synthesis. It never edits the
  repository. Codex and Claude are preferred; Gemini can replace a provider
  whose known quota is too low. Process failures still abort explicitly.
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
- one dated conversation snapshot is retained per day, and schema migrations
  preserve the complete pre-migration payload before changing it;
- finalized run logs are retained for 30 days by default, with additional
  limits of 500 runs and 500 MB configurable under `run_retention`.

Each generated `.agentflow/` contains its own `.gitignore`, so this separation
also applies to projects that do not yet have a root `.gitignore`.

## Local web interface

Start the control room from the project you want the agents to work on:

```bash
cd /path/to/project
joe
```

When `tmux` is installed, Joe starts the web server in the detached
`joe-8765` session and opens `http://127.0.0.1:8765`. Closing the laptop or
terminal does not stop the server. Use `tmux attach -t joe-8765` to inspect it,
`joe kill` to stop every numbered Joe tmux session, or
`joe web --foreground` for the previous foreground behavior. `joe kill` never
touches non-Joe sessions.
The interface streams provider stdout and
stderr, shows provider status and personal quota resets in a compact top-right
popover when the provider CLI exposes them, provides stable
agent/workflow/model/effort/permission controls,
and loads the latest 100 run logs from the project's `.agentflow/runs/` folder.
Conversation history is loaded independently from optional quota and model
catalog probes, so a slow provider CLI cannot block the whole interface.
Joe never estimates missing quotas: unsupported providers are marked clearly.
Gemini headless runs expose per-model token and request statistics but no
single global token allowance: quotas depend on the model, authentication, and
subscription, and are generally expressed as request limits. Joe records its
local statistics under
`$XDG_DATA_HOME/joe/gemini_usage.json` and displays today's Joe consumption,
last call, models used, and the detected authentication category. The detailed
quota snapshot remains available through `/stats model` in an interactive
Gemini CLI session.
Automatic routing remains task-first, then balances Codex and Claude when
the preferred provider has 20% or less remaining and its reset is at least 24
hours away. Short five-hour windows therefore remain useful instead of being
prematurely preserved. At 5% or less it switches regardless of reset distance.
A manually selected provider always wins. Before REVIEW or CONSENSUS, Joe checks
the latest cached personal quota data. The minimum reserve is 3% for a simple
answer, 8% for normal FAST work, 12% for REVIEW, and 20% for CONSENSUS. A reset
within two hours relaxes the threshold down to 5%. If one participant is
constrained, Joe selects another available provider, including Gemini. If only
one provider can afford a normal FAST call, Joe runs it alone and explains the
downgrade. If none can, Joe stops before calling a model; explicitly choosing a
workflow allows a deliberate attempt. Unknown quotas are never treated as
exhausted.
Routing itself is deterministic and does not call a model. It never waits for
fresh quota or model-catalog probes: the latest background cache is used, and
the interface reports the local routing duration separately from provider
processing. Naming exactly one provider in a request (for example, "teste
Gemini") routes directly to that provider unless the menu explicitly overrides
it.
Provider health checks such as "fais un petit test de Gemini" are deliberately
minimal: Joe omits project and conversation context, selects Gemini Flash,
forbids tools, and stops after 30 seconds. A Gemini `429 RESOURCE_EXHAUSTED`
stops immediately instead of waiting through CLI backoff retries, and the test
does not silently fall back to another provider.
Claude routing uses the official local cache refreshed by `/usage`. Clicking
the quota refresh button opens a short-lived Claude terminal session and runs
`/usage` automatically. Joe reconstructs the interactive screen through tmux,
including values rendered with cursor updates; normal page loading continues
to use the cache.
For requests that are moderately complex, Joe dynamically selects the first
current Codex/Claude model exposed by the installed CLI and uses `high`
reasoning effort. Explicit model and effort choices always take precedence.
Capability questions and other simple FAST answers use `low` effort, even when
the question is long. Question wording such as "est-ce que tu peux..." is not
treated as an implementation order merely because it mentions modifying code.
Explicit negations such as "ne modifie rien", "sans changer les fichiers", and
"do not modify files" take precedence over the negated modification words.
Scoped negations still allow the rest of an explicit task: "corrige le bug mais
ne commit pas" remains an implementation request.
Large implementation requests are routed to REVIEW automatically: the primary
agent implements, the other agent audits without editing, and the primary gets
at most one correction pass when the reviewer explicitly reports justified
issues. During CONSENSUS, Joe shows the two proposals and then the two
cross-reviews progressing concurrently in a structured panel; only the final
synthesis is posted as Joe's global answer.
If a provider reaches a limit during a run, Joe adds a visible notice with the
known reset windows (including model-specific Claude windows when exposed) and
the installed fallback providers/models it will try automatically. A model
switch within the same provider is not presented as a way around a shared
quota.
Native JSON streams expose observable progress such as commands, tools, and
files read or changed; Joe does not expose private chain-of-thought.
Repository change cards are generated only for modifying requests or an
explicitly write-enabled execution. Read-only answers and analyses ignore
unrelated edits made concurrently by another terminal or agent.
Final answers are rendered locally as safe Markdown, including headings,
tables, lists, links, inline code, and fenced code blocks.
Markdown rendering lives in a separate browser module. The composer grows with
the prompt up to 42% of the viewport and uses the same font, size, line height,
case, and letter spacing as response text.

Each logical project can define one primary workspace, explicit additional
roots, and optional remote-command access. Joe starts every provider in that
workspace and forwards only the declared extra roots through the provider's
native allow-list option. Missing configured roots stop the run instead of
silently falling back to another directory. AI4Trading may therefore enable
SSH/Jean Zay without granting another project access to unrelated local files.
Projects can also define a default permission for explicit operational
validation requests such as audits, test-suite execution, smoke tests, or
`git fetch`. Ordinary questions remain read-only. An operational audit routes
to REVIEW: the primary agent performs the checks with the configured permission,
then the reviewer evaluates its recorded evidence without rerunning commands
that need write or network access.

The evidence journal separates local routing inferences, successful provider
executions, and refused/failed executions. Providers also receive an explicit
reporting contract: a blocked file or command must be reported as `Refusé`,
never as verified. This journal records observable evidence, not private model
reasoning.

Gemini has an inactivity watchdog. After 90 seconds without any output, Joe
terminates the hung process and lets the normal fallback chain select another
provider. Active runs are recorded in `.agentflow/pending_runs.json`; after a
server crash they are restarted from their saved request and conversation
context. A dead subprocess cannot resume at an instruction boundary, so Joe
truthfully relaunches the task from the beginning with the same run identifier.
After a browser refresh, the interface reattaches to active server-side event
streams instead of losing their run identifiers. If the server restarts while
the page stays open, Joe reconciles the browser state with the active server
runs: recovered tasks reconnect, while missing runs stop instead of leaving an
infinite spinner. Each server-sent event has a monotonic identifier, so a
transient stream reconnection resumes after the last received event without
replaying earlier progress. Open pages also reload when the backend version
changes.

Conversations are persistent per project. Each conversation keeps its full
message history and independent agent, workflow, model, effort, and permission
settings. Provider processes remain ephemeral: when a conversation switches
from Codex to Claude or Gemini, Joe rebuilds a bounded shared context containing
the stable project context, the latest project session/handoff, project
instructions, and recent conversation turns. Stable project context has a
reserved budget and is not displaced by a long history.
When unsummarized older messages exceed 30,000 characters, Joe starts a
tool-free Gemini Flash compaction after returning the visible answer. The full
history is retained; only the prompt representation is summarized. This policy
is configurable under `semantic_compaction` in `.agentflow/config.yaml`.
Conversations can be pinned and several can run concurrently. A conversation
owns at most one active run: concurrent submissions race through one atomic
reservation, and the rejected request receives HTTP 409 before its user message
is persisted. Avoid launching concurrent write tasks from different
conversations against the same files.
Completed live runs remain reconnectable for five minutes, with at most 50
completed runs retained in process memory. Project session/handoff updates,
run-log pruning, and Gemini usage accumulation are serialized and written
atomically so concurrent runs cannot lose an update or mix the two active
memory files.
Provider names and labels come from one static registry shared by the CLI,
router, execution engine, HTTP API, capability response, and Web selector.
Adding a provider remains an explicit source-code change; the registry is not a
dynamic plugin loader.
Projects and conversations can be reordered by drag and drop. Favorites remain
above non-favorites, each conversation shows its last-call date, and project
groups can be collapsed. The left and right panels have resize handles; their
widths intentionally reset to the ergonomic defaults after a page refresh.
On mobile, Conversations and Activity open from dedicated top-bar buttons
instead of disappearing.
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

## Diagnostics

Check storage, installed provider versions, and cached quota visibility without
sending a prompt:

```bash
joe doctor -C /path/to/project
```

Use `joe doctor --live` to send exactly one short, read-only prompt to each
installed provider and report its duration and classified failure reason.

Use `joe web --no-browser` on a remote server when automatic browser opening is
not useful, then forward the port:

```bash
ssh -L 8765:127.0.0.1:8765 user@server
```

Open `http://127.0.0.1:8765` on your computer. The server binds only to
localhost by default. Every JSON endpoint uses the same parser and rejects
invalid lengths, malformed objects, and bodies over 1 MiB. A non-local bind is
refused unless the risk is acknowledged explicitly:

```bash
joe web --host 0.0.0.0 --allow-remote
```

The HTTP API has no authentication; prefer SSH port forwarding even when
`--allow-remote` is available.

## Validation and CI

The GitHub Actions workflow runs the complete Python suite on Python 3.10 and
3.12, checks the JavaScript assets with Node.js, and exercises the installed
`joe` entry point with a read-only dry run. Run the same checks locally with:

```bash
pytest -q
node --check src/joe/web_assets/app_usage.js
node --check src/joe/web_assets/app_conversations.js
node --check src/joe/web_assets/app.js
node --check src/joe/web_assets/markdown.js
joe --dry-run -C . "Ne modifie rien, analyse seulement Joe"
```

An opt-in `live_smokes` workflow-dispatch job targets an authenticated
self-hosted runner and contacts all four provider CLIs through
`joe doctor --live`. The equivalent local command is:

```bash
JOE_LIVE_SMOKE=1 pytest -q tests/test_live_cli_smoke.py
```

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
