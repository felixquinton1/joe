# Joe

**A visual control room for your AI coding agents.**

[![CI](https://github.com/felixquinton1/joe/actions/workflows/ci.yml/badge.svg)](https://github.com/felixquinton1/joe/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB.svg)](https://www.python.org/)
[![License: MPL-2.0](https://img.shields.io/badge/license-MPL--2.0-blue.svg)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/joe-orchestrator.svg)](https://pypi.org/project/joe-orchestrator/)

Joe brings Codex, Claude Code, Gemini CLI, GitHub Copilot CLI, and Cursor CLI
into one persistent local workspace. Write a request naturally; Joe selects an
appropriate workflow and agent, carries project context between providers, and
shows what is happening as it runs.

It uses the CLI subscriptions you already have. Joe is not an AI provider,
proxy, or additional billing service.

## What Joe brings together

- **One visual workspace** for projects, conversations, tasks, approvals, Git
  changes, provider activity, quotas, and settings.
- **Automatic routing** based on the request, model capabilities, provider
  availability, recent failures, and known quota windows.
- **Persistent context** that lets Codex, Claude, Gemini, Copilot, and Cursor
  continue the same project without asking you to restate everything.
- **Live, honest progress** showing the provider, model, effort, stage, tools,
  fallbacks, and failures that actually occurred.
- **Concurrent conversations** with queued prompts, cancellation, durable task
  states, and optional Git worktree isolation.
- **Human control** over permissions, approvals, diffs, integration, commits,
  and autonomous limits.

## Workflows

Joe chooses a workflow automatically, while always allowing a manual override.

| Mode | How it works | Best suited to |
| --- | --- | --- |
| **FAST** | One agent answers or implements directly | Questions and focused changes |
| **REVIEW** | One agent works, another reviews, then at most one justified correction pass runs | Meaningful implementation work |
| **CONSENSUS** | Independent proposals run in parallel, each is challenged, then a final synthesis reconciles the evidence | Research, architecture, scientific plans, and important decisions |

### Consensus, visible from start to finish

Consensus is not a black box. Joe exposes each independent proposal and
cross-review as it completes, with the real provider, model, and effort used
for every stage.

The synthesis is produced only after the available opinions and reviews have
finished. If a provider fails or reaches a quota, Joe reports the degradation
and uses an eligible fallback instead of pretending that every stage ran.

## Autonomous work

Joe can turn a longer objective into a bounded sequence of steps and continue
it without keeping the browser open. An autonomous campaign can:

- plan, implement, run tests, inspect results, and make a limited correction;
- prefer a particular provider and wait for its quota window to reset;
- schedule work for a later time or a defined overnight/weekend window;
- enforce maximum steps, model calls, retries, runtime, and stop conditions;
- checkpoint progress and resume after a Joe restart or provider interruption;
- reattach to a still-running process instead of launching the same attempt
  twice;
- stop for human review when recovery is unsafe or repeatedly fails.

Quota waiting does not consume the campaign's active-time budget. Unknown
capacity is never treated as guaranteed capacity, and a missing result is never
fabricated to keep a workflow moving.

> [!WARNING]
> Autonomous execution can run commands and modify files through the selected
> provider. Review project roots, permissions, resource limits, Git policy, and
> validation steps before leaving a campaign unattended.

## Install and open Joe

Joe requires Python 3.10 or newer and at least one supported provider CLI,
installed and authenticated separately.

Install the Python package in an isolated environment:

```bash
pipx install joe-orchestrator
```

Then open a terminal in a project and run:

```bash
joe
```

Joe opens the local Web interface on `127.0.0.1:8765`. On Linux and macOS,
`tmux` can keep the server alive after the terminal or remote connection closes.

Check the local installation without consuming provider quota:

```bash
joe doctor
```

Use `joe doctor --live` only when you intentionally want Joe to send one short
request to every installed provider.

### Supported provider CLIs

| Provider | Command expected by Joe |
| --- | --- |
| OpenAI Codex | `codex` |
| Claude Code | `claude` |
| Gemini CLI | `gemini` |
| GitHub Copilot CLI | `copilot` |
| Cursor CLI | `cursor-agent` |

Joe discovers the models and options exposed by the installed CLIs whenever
possible. Availability, quota precision, tool access, and model controls still
depend on each provider.

## The Web control room

The Web interface is Joe's primary product surface. It includes:

- persistent projects, subprojects, conversations, and full-text search;
- conversation-specific agent, workflow, model, effort, and permission choices;
- streaming activity without exposing private chain-of-thought;
- active, queued, completed, blocked, and interrupted task states;
- structured approval prompts for broader access or sensitive operations;
- Markdown, tables, code blocks, and mathematical formula rendering;
- Git summaries, file-level diffs, isolated worktrees, and explicit integration;
- project and shared skills, files, quota status, and provider diagnostics;
- English and French interface text stored as a per-browser preference.

Several conversations can run concurrently. A conversation owns at most one
active run; later prompts can wait in its queue.

## Projects, context, and Git

Joe maintains compact project context under `.agentflow/` so that a different
provider can continue the work without receiving the entire transcript every
time:

```text
.agentflow/
├── project.md       # stable context and conventions
├── session.md       # current objective, decisions, and affected files
├── handoff.md       # compact handoff for the next provider
├── config.yaml      # project settings
└── runs/            # complete execution records
```

Conversation data, approvals, pending tasks, local backups, and provider logs
remain local and are excluded from project Git by default.

For modifying tasks, projects can use an isolated Git worktree and a
`joe/<task-id>` branch. Joe keeps the main checkout untouched, presents the
diff, and lets you integrate or discard the result explicitly. If integration
conflicts, resolution is limited to three agent passes; otherwise the worktree
is preserved for human review. Joe never applies a silent “last writer wins”
policy.

Automatic commit and push behavior is configured per project. Keep it disabled
when you want selective rejection to remain available.

## Permissions and privacy

Joe binds to localhost by default and pairs the browser with a local secret
stored outside the project. It supports `viewer`, `operator`, and `maintainer`
capability ceilings. Provider fallbacks preserve the same or a stricter
permission level and cannot silently grant broader access.

Joe limits project work to the configured root and explicit additional roots.
Network access follows the provider's native controls; currently only Codex
exposes a switch Joe can enforce. The interface states this limitation rather
than claiming to sandbox providers that do not support it.

Prompts and selected project context are sent to the provider CLI chosen for
each stage. The provider's terms, retention policies, quotas, and charges still
apply. See [SECURITY.md](SECURITY.md) for Joe's security model and vulnerability
reporting process.

## Optional terminal usage

The Web interface is the default, but the same orchestrator can be used from a
terminal:

```bash
joe "Explain the current architecture"
joe --agent claude "Review the proposed API"
joe --mode consensus "Compare these two designs"
joe cli
```

Useful lifecycle commands:

```bash
joe url          # reopen an authenticated Web session
joe restart      # restart Joe Web
joe stop         # stop the current instance
joe kill         # stop all Joe tmux instances
joe auth rotate  # rotate the local browser/API secret
```

## VS Code companion

The repository contains a small VS Code companion under
[`vscode-extension/`](vscode-extension/). It starts, opens, refreshes, restarts,
and stops Joe on the current local or Remote-SSH host. It deliberately opens
the same Web control room instead of maintaining a second, divergent interface.

The extension is currently distributed from the source repository rather than
the VS Code Marketplace. To build it locally:

```bash
cd vscode-extension
npm ci
npm test
npm run package
```

## Project status

Joe is an alpha project for local, single-user workflows. The Python package
and public Git repository are the first distribution targets. Provider CLIs
are third-party tools and can introduce breaking behavior when they update.

CI runs the Python suite on Python 3.10 and 3.12, performs installation checks
on Linux, macOS, and Windows, validates the Web assets, and tests the VS Code
companion.

For development:

```bash
git clone https://github.com/felixquinton1/joe.git
cd joe
python -m pip install -e ".[dev]"
pytest -q
node --test tests/test_web_assets.mjs
```

Documentation:

- [User guide](docs/user-guide.md)
- [HTTP and SSE API contract](docs/api-contract.md)
- [Contributing guide](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)
- [Release guide](docs/releasing.md)

## License

Joe is licensed under the [Mozilla Public License 2.0](LICENSE).
Copyright © 2026 Félix Quinton.

The license covers the source code, not the Joe name, logo, or identity of the
official project. See [TRADEMARKS.md](TRADEMARKS.md).
