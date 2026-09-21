# Joe

**A local control room for your AI coding CLIs.**

[![CI](https://github.com/felixquinton1/joe/actions/workflows/ci.yml/badge.svg)](https://github.com/felixquinton1/joe/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB.svg)](https://www.python.org/)
[![License: MPL-2.0](https://img.shields.io/badge/license-MPL--2.0-blue.svg)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/joe-orchestrator.svg)](https://pypi.org/project/joe-orchestrator/)

Joe routes plain-language requests across Codex, Claude Code, Gemini CLI,
GitHub Copilot CLI, and Cursor CLI. It keeps project context portable between
providers, supports independent review and consensus workflows, and gives you
one persistent interface for conversations, tasks, permissions, diffs, and
provider activity.

Joe runs on your machine and uses the provider accounts and CLIs you already
have. It is not an AI provider, proxy, or billing service.

> [!IMPORTANT]
> Joe can launch commands and edit files through the selected provider. Review
> project roots, permissions, autonomous limits, and Git changes before relying
> on unattended execution. Closing the browser does not stop server-side work.

## Why Joe?

- **One natural-language entry point.** Ask a question or request a change;
  workflow verbs are optional.
- **Provider-aware routing.** Joe considers task type, model capabilities,
  availability, recent failures, and known personal quota windows.
- **Shared project context.** Move between providers without rebuilding the
  conversation by hand.
- **Controlled collaboration.** Use one agent, an implementation with an
  independent review, or a multi-agent consensus.
- **Persistent work.** Conversations, tasks, queued prompts, approvals, and run
  logs survive browser and server restarts.
- **Safe parallel changes.** Optional Git worktrees isolate modifying tasks and
  keep integration explicit.
- **Local-first operation.** The Web UI, history, memory, and control plane stay
  on the machine running Joe.

## Status

Joe is an alpha project intended for local, single-user workflows. CI runs the
test suite on Python 3.10 and 3.12, plus installation checks on Linux, macOS,
and Windows with Python 3.12. Provider behavior can still change when
third-party CLIs update.

## Requirements

- Python 3.10 or newer
- One or more supported provider CLIs, installed and authenticated separately
- Git for repository-aware features
- `tmux` on Linux/macOS if you want the Web server to remain detached

| Provider | Expected command |
| --- | --- |
| OpenAI Codex | `codex` |
| Claude Code | `claude` |
| Gemini CLI | `gemini` |
| GitHub Copilot CLI | `copilot` |
| Cursor CLI | `cursor-agent` |

Joe never stores provider passwords. Availability, models, quotas, and network
controls depend on what each installed CLI exposes.

## Installation

Once the first public release is available, install Joe in an isolated
environment:

```bash
pipx install joe-orchestrator
joe doctor
```

Until then, install the current repository build:

```bash
pipx install git+https://github.com/felixquinton1/joe.git
joe doctor
```

For development:

```bash
git clone https://github.com/felixquinton1/joe.git
cd joe
python -m pip install -e .
pytest -q
```

`joe doctor` checks local configuration without sending prompts. Use
`joe doctor --live` only when you deliberately want a short real request sent
to every installed provider.

## Quick start

Open a terminal in the project Joe should work on and run:

```bash
joe
```

Joe starts the local Web interface on `127.0.0.1:8765`. When `tmux` is
available, the server runs in a detached `joe-8765` session.

You can also send a one-shot request or use the full terminal interface:

```bash
joe "Explain the current architecture"
joe "Add an option to disable Gamma loss"
joe --agent claude "Review the proposed API"
joe --mode consensus "Choose between these two architectures"
joe cli
```

Useful lifecycle commands:

```bash
joe url          # reopen an authenticated Web session
joe restart      # restart the current Joe Web server
joe stop         # stop the current instance
joe kill         # stop all Joe tmux instances
joe auth rotate  # revoke browser sessions and rotate the local secret
```

Run `joe --help` and `joe web --help` for the complete command reference.

## Workflows

Joe selects a workflow automatically unless you override it.

| Workflow | Behavior | Typical use |
| --- | --- | --- |
| **FAST** | One provider call | Questions and small, focused changes |
| **REVIEW** | Primary execution, independent read-only review, at most one correction pass | Meaningful implementations |
| **CONSENSUS** | Two independent proposals, two cross-reviews, one final synthesis | Important or difficult-to-reverse decisions |

Independent consensus stages run in parallel where possible. A provider failure
is surfaced explicitly; incomplete synthesis output is rejected and routed to
an available fallback instead of being published as a final answer.

Routing starts with deterministic local rules. For ambiguous requests, Joe may
use one fast, bounded classifier call to select the intent, workflow, provider,
model tier, and reasoning effort. Explicit user choices always take precedence,
and the classifier cannot grant broader permissions.

## Web interface

Joe Web is the main control room. It provides:

- persistent projects, subprojects, and conversations;
- per-conversation provider, workflow, model, effort, and permission choices;
- streaming provider activity without exposing private chain-of-thought;
- active and completed task states, queued prompts, and cancellation;
- structured approvals for operations that need broader access;
- Markdown and mathematical formula rendering;
- Git change summaries, file-level diffs, and explicit integration controls;
- project files, shared skills, search, quotas, and provider diagnostics;
- autonomous plans with bounded steps, retries, schedules, and resource limits.

Several conversations can run concurrently. One conversation owns at most one
active run, while additional prompts can wait in its queue.

### VS Code

The extension in [`vscode-extension/`](vscode-extension/) is intentionally a
small launcher for Joe Web. It can start, open, refresh, restart, and stop the
server in the current VS Code or Remote-SSH host. Conversations and model
controls remain in the Web UI so Joe has only one rich interface to maintain.

Build the extension locally with:

```bash
cd vscode-extension
npm ci
npm test
npm run package
```

## Permissions and security

Joe binds to localhost by default and authenticates browser and API clients
with a local secret stored outside the project. Opening Joe through the CLI or
VS Code pairs the browser without placing the secret in server logs.

The server supports three capability ceilings:

```bash
joe web --profile viewer
joe web --profile operator
joe web --profile maintainer
```

- **viewer** reads projects, conversations, and results;
- **operator** can run allowed tasks;
- **maintainer** can manage projects and approve full-access operations.

Analysis and consensus are read-only by default. Modifying requests inherit the
selected project policy. Provider fallbacks preserve the same or a stricter
permission level; switching providers never silently grants more access.

Joe restricts providers to the configured project root and explicit additional
roots. Network access can only be controlled when the provider exposes a native
switch—currently Codex. Joe reports this limitation instead of claiming to
sandbox providers that cannot be sandboxed through their CLI.

Remote binds require `--allow-remote` and remain authenticated. Prefer a local
bind with SSH or VS Code port forwarding:

```bash
joe web --no-browser
ssh -L 8765:127.0.0.1:8765 user@server
```

See [SECURITY.md](SECURITY.md) for vulnerability reporting and the supported
security model.

## Tasks, worktrees, and Git

Every request creates a durable task linked to its conversation. Tasks record
their state, provider, model, run, and delivery result without replacing the
chat history.

Projects may isolate modifying tasks in Git worktrees. Joe then creates a
`joe/<task-id>` branch, keeps the main checkout untouched, and offers three
explicit actions when work finishes:

- inspect the diff;
- integrate the branch;
- discard the isolated worktree.

If the base branch advanced, Joe rebases before integration. Conflict
resolution is bounded to three agent passes. Failed resolution is reported to
the user and the worktree is preserved—Joe never applies a silent
"last writer wins" policy.

Automatic commit and push behavior is configured per project. Keep it disabled
when you want file-level rejection to remain available.

## Context and storage

Joe creates a small `.agentflow/` directory in each project:

```text
.agentflow/
├── project.md       # stable project context
├── session.md       # current objective and decisions
├── handoff.md       # compact provider handoff
├── config.yaml      # project settings
└── runs/            # full execution records
```

Active context is bounded and rewritten rather than allowed to grow forever.
Long conversations can be compacted in the background while their complete
history remains available.

Local conversation data, pending tasks, approvals, and backups are excluded
from project Git by default. Conversation writes are mirrored under
`$XDG_DATA_HOME/joe/backups/` (normally `~/.local/share/joe/backups/`). Run-log
retention is bounded by age, count, and total size.

## Skills and project instructions

Project skills live under `.agentflow/skills/` and are shared with every
provider through Joe's context layer. Global skills can be reused across
projects. Existing provider skills can be imported from the UI or CLI:

```bash
joe skills list -C /path/to/project
joe skills import /path/to/SKILL.md -C /path/to/project
```

Joe also supports creating a skill from a natural-language request in the Web
interface.

## Autonomous work

Joe can schedule a bounded sequence of steps, wait for provider quota resets,
run tests, inspect results, and continue later. Autonomous execution is
experimental. Configure a preferred provider, time window, maximum model calls,
maximum steps, retry policy, and stop conditions before leaving it unattended.

Unknown quota is never treated as guaranteed capacity. If a required provider
cannot continue and no suitable fallback exists, the task waits rather than
fabricating progress. A published reset time is used directly; otherwise Joe
rechecks with a bounded backoff.

Autonomous experiments use a durable attempt identifier and process manifest.
After a server restart, Joe reattaches to a still-running local process, reuses
an already completed result, or resumes from a compatible checkpoint. It never
launches the same experiment attempt twice. A lost step without a recoverable
checkpoint returns to planning, and repeated recovery failures stop for human
review instead of looping indefinitely. Time spent waiting for quota does not
consume the campaign's active-time budget.

Experiment commands receive `JOE_AUTONOMOUS_ATTEMPT_ID` as an idempotency key
for remote submissions and `JOE_AUTONOMOUS_OUTPUT_DIR` for attempt-local
artifacts. Integrations that submit external work should store and verify this
key before repeating a submission.

## Data and privacy

Joe sends prompts and selected context to the provider CLI chosen for each
stage. Provider terms, retention policies, quotas, and charges still apply.
Full stdout and stderr are stored locally in run logs and redacted for known
secret patterns before display. Do not place credentials in prompts, project
instructions, or committed `.agentflow` files.

## Development

Run the local validation suite with:

```bash
pytest -q
node --check src/joe/web_assets/app.js
node --check src/joe/web_assets/app_auth.js
node --check src/joe/web_assets/app_automation.js
node --check src/joe/web_assets/app_conversations.js
node --check src/joe/web_assets/app_usage.js
node --check src/joe/web_assets/i18n.js
node --check src/joe/web_assets/markdown.js

cd vscode-extension
npm ci
npm run check
npm test
```

The normal test suite uses doubles and does not consume provider quota. Live CLI
smokes are opt-in:

```bash
JOE_LIVE_SMOKE=1 pytest -q tests/test_live_cli_smoke.py
```

Architecture and behavior references:

- [User guide](docs/user-guide.md)
- [HTTP and SSE API contract](docs/api-contract.md)
- [VS Code integration plan](docs/vscode-extension-action-plan.md)
- [Contributing guide](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)
- [Release guide](docs/releasing.md)

## License

Joe is licensed under the [Mozilla Public License 2.0](LICENSE).
Copyright © 2026 Félix Quinton.

The license covers the source code, not the Joe name, logo, or identity of the
official project. See [TRADEMARKS.md](TRADEMARKS.md).
