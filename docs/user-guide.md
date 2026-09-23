# Joe user guide

Joe connects a local interface to the Codex, Claude Code, Gemini, Copilot and
Cursor CLIs already installed and authenticated on the machine. It never asks
for or stores their passwords.

The Cursor CLI installs under the name `agent`, with no window: the `cursor`
command launches the editor and is not suitable. Joe also accepts the older
name `cursor-agent`, kept for compatibility. Joe drives it through its
documented options and cannot read its remaining quota; an exhausted quota is
still detected when the run starts, and the request falls back to another
provider.

The model selector is populated only for CLIs that publish their catalogue:
Codex exposes one, and Claude's is maintained inside Joe. Gemini and Copilot
offer no list and therefore only propose `auto`, which lets the CLI choose; the
model you want can be imposed in the project settings.

## Installation

Joe requires Python 3.10 or newer. `pipx` is recommended: it isolates the
dependencies while making the `joe` command available everywhere.

From the GitHub repository:

```bash
pipx install git+https://github.com/felixquinton1/joe.git
```

To work on Joe itself:

```bash
git clone https://github.com/felixquinton1/joe.git
cd joe
python -m pip install -e .
```

The Python package is platform-independent. Linux, macOS and Windows use the
same commands. Joe uses `tmux` when available on Linux and macOS, and a native
detached process on Windows. Both survive closing the launching terminal.
Windows state and logs live below `%LOCALAPPDATA%\\Joe\\run`. Use
`joe web --foreground` to keep the server attached deliberately.

Check the installation:

```bash
joe --version
joe doctor
```

The provider CLIs remain separate prerequisites. `joe doctor --live` actually
contacts them and may consume a small amount of quota; plain `joe doctor` sends
no request.

## Three ways to use Joe

### Terminal

A single request:

```bash
joe "Review this project without modifying any file"
joe --agent claude --mode review "Check this change"
printf "Summarise this repository" | joe -C /path/to/project
```

A fully command-line session:

```bash
joe cli
```

`joe chat` is a kept alias. `/exit` leaves the session. `--dry-run` prints the
chosen route without calling any agent.

### Hybrid routing

Local commands that are certain stay deterministic and call no model. For a
natural request that is genuinely ambiguous, Joe can ask a light model already
available to return a bounded JSON classification: intent, complexity,
workflow, provider, model tier and effort.

The classifier is chosen from known quotas, availability, recent failures and
observed latency. It gets six seconds, does not chain several providers, and
falls back to the local router on failure. The `light`, `standard`, `strong`
and `long-context` tiers are then resolved against the catalogue each CLI
actually publishes.

Classification can neither grant a permission, nor reduce a write intent
detected locally, nor impose a consensus on a simple task. An agent, model,
effort or workflow chosen explicitly by the user always takes precedence. The
mechanism can be disabled with `JOE_DISABLE_LLM_ROUTER=1`.

### Web interface

Joe Web starts by default with the private `maintainer` profile. A deliberately
limited instance starts with `joe web --profile viewer` or
`joe web --profile operator`. The local browser is authenticated automatically
when opened by `joe` or by the VS Code extension. A URL typed by hand never
receives the secret: run `joe` again to pair a new browser. The secret lives
outside the project and must not be copied into a repository.

Under `--profile viewer` or `--profile operator` the interface stays usable:
reading projects is allowed, but project mutations and active quota refreshes
answer `403` with an explicit message.

The "Web access" setting, per project or per conversation, constrains only the
providers that expose a network switch — today Codex alone. The Claude, Gemini
and Copilot CLIs offer no equivalent option: unticking it does not remove their
network access. The interface states this real scope under the checkbox, from
the `network_control_providers` field of `GET /api/status`.

To pair a browser again after clearing cookies:

```bash
joe url
```

`joe url --print` shows the sensitive link only when it has to be passed by
hand to a browser on the same machine. If you suspect a local leak,
`joe auth rotate` renews the secret immediately, revokes older sessions and
opens a fresh pairing.

The profile limits the actions a given instance accepts. Instances belonging to
the same system account do share the same secret, however: `--profile viewer`
reduces the risk of a slip, but is not a delegation to another user.

From the project you want to work on:

```bash
joe web
```

Joe listens only on `127.0.0.1:8765` by default. It uses `tmux` when
available on Linux and macOS and a detached process managed by Joe on Windows.
The same `stop`, `restart` and `kill` commands work on each platform.
Windows additionally records the detached server output for `joe logs`. To
keep the server in the terminal:

```bash
joe web --foreground
```

### VS Code and Remote-SSH

Install the `.vsix` file in the VS Code window that holds the workspace. Under
Remote-SSH, choose "Install in SSH": the extension and `127.0.0.1:8765` are
then both on the remote host.

The Joe view acts as a launcher for the Web interface: start, open, refresh,
restart and stop the instance on the current port. Under Remote-SSH, "Open"
uses VS Code's port forwarding automatically. Conversations, prompts and
results stay exclusively in Joe Web.

VS Code's native zoom applies to the Joe view:

- Windows/Linux: `Ctrl++`, `Ctrl+-`, `Ctrl+0`;
- macOS: `Cmd++`, `Cmd+-`, `Cmd+0`;
- command palette: `Joe: Zoom in`, `Joe: Zoom out`, `Joe: Reset zoom`.

## Tasks and worktrees

Every run creates a durable task, visible in the tracking panel. It keeps its
state, provider, model and a summary of its changes without duplicating the
conversation.

The panel also shows the compact pipeline "Request → Implementation →
Validation → Diff → Delivery". A conversation reads "Running" during the run,
then "Done" until it is next opened.

### Automatic resumption after a quota reset

The project option "Resume automatically after a quota reset" is on by default.
If no suitable provider has enough headroom, the task moves to "Waiting for
quota". Joe keeps the prompt, the attachments, the worktree and the attempt
count, then refreshes the quotas at the expected time. When the CLI publishes
no time, Joe retries with a progressive delay bounded between five minutes and
one hour.

If the new window is still not enough, the task waits for the next one. If a
quota is reached in the middle of long work, the changes already present in the
worktree are kept and the request resumes taking that state into account. A
wait can be cancelled with the usual stop button.

Joe still picks another provider when that lets it finish the task without
sacrificing the requested workflow. An explicit agent or mode selection takes
precedence and is attempted immediately.

Pending approvals stay visible in the same panel after a reload. They can be
accepted or refused later; an accepted approval covers only the request it was
recorded for.

An **Autonomous** campaign additionally carries a durable attempt identity and
process manifest. After a server restart, Joe:

- reattaches to the local experiment if its process is still alive;
- reuses a result already written instead of running the command again;
- resumes from a checkpoint compatible with the configured resume command;
- returns to planning when no reliable checkpoint exists;
- stops after three repeated losses of the same step instead of looping.

Time spent waiting for a quota is not deducted from the active budget. The
campaign log reports every recovery, and the interface shows the next known
resume time.

Experiment commands receive `JOE_AUTONOMOUS_ATTEMPT_ID`, a stable key to record
when submitting externally (cluster, API, CI), and
`JOE_AUTONOMOUS_OUTPUT_DIR`. A remote launcher must check that key before
submitting the same operation twice.

When a project enables "Isolate changes in a Git worktree", Joe creates a
`joe/<id>` branch and works outside the main checkout. At the end of the run:

- **View diff** shows the files and the patch;
- **Integrate** commits the changes then merges the branch, only if the main
  repository is clean;
- **Delete** explicitly discards the worktree and its branch.

If the main branch has moved on, Joe rebases the task first. A conflict
triggers a bounded resolution by the task's agent, preserving both objectives
and running the targeted tests it was asked for. If resolution fails or leaves
markers, the rebase is aborted and the worktree stays available for review and
another attempt.

## Files and search

The `+` button, or dragging onto the composer, adds a file to the project
library. The **Tools** menu attaches it to a request, opens it or deletes it.
`@file-name` also attaches the matching file. Joe shows the attachments
actually sent before you submit.

When a mentioned file also exists in the project, Joe does not inject its
library copy: the CLI reads the live original instead. The library copy is a
snapshot taken when it was imported, so sending both would hand the model two
versions of the same file with no way to tell which one is current.

The search box in the left column covers conversations, tasks and file names
across every project. Results stay entirely local.

## CLI features: `@`, commands and MCP servers

Joe runs each CLI in **a single non-interactive call**. That constraint
explains everything below: what is an instruction to the model carries through,
what drives a session has nothing to drive.

| What you type | Effect |
| --- | --- |
| `@file` | works: the CLI expands the paths it recognises |
| `/my-command` defined in `.claude/commands/` | works: the CLI runs it before calling the model |
| `/compact`, `/clear`, `/login`, `/model` | no effect: there is no session to drive. The CLI returns neither an answer nor an error, and Joe explains that emptiness rather than showing a blank bubble |

To compact a history, use Joe's own conversation compaction, which works on the
history Joe holds itself.

### MCP servers and external tools

Joe passes **no MCP options** to the CLIs: each one's configuration applies
exactly as it does outside Joe. A server declared for Claude Code in
`.mcp.json` or in its settings, or for Codex in `~/.codex/config.toml`, is
therefore available during a run. MCP tool calls appear in the activity panel,
just like shell commands.

Permission is the remaining question, and it deserves precision before relying
on an external database. An MCP tool **always** asks for explicit
authorisation, even to read. A non-interactive call offers no channel to give
it, so the request is refused. Measured across the three levels:

| Project access level | MCP tool |
| --- | --- |
| Read-only | refused — "requested permissions […] but you haven't granted it" |
| Project write | refused, for the same reason |
| **Full access** | **works** |

In other words: today an MCP tool only runs under Joe with full access. That is
not a limitation of Joe but of non-interactive mode, and it applies just as
much when you call the CLI that way yourself.

To use one at a narrower level, tick **"Allow the MCP tools already configured
in the CLIs"** in the project settings. Joe then asks the CLI which servers it
knows and pre-authorises them, which lets a read-only run reach a database
without switching to full access.

That setting is off by default, deliberately: an MCP tool acts outside the
project — database, network, external service. "I may modify this project" does
not amount to "I may act outside it", so widening that stays an explicit
decision, project by project.

Two limits worth knowing. A server declared in the project's `.mcp.json` stays
"pending approval" until you run the CLI interactively once to approve it;
those added with `claude mcp add` are already approved. And only Claude Code
exposes a queryable inventory: with the other providers the setting has no
effect.

Database credentials stay in the MCP server's own configuration, never in Joe:
it neither sees nor records them.

## Capabilities and security

The public extension takes cautious defaults:

- `Joe: Allow Workspace Writes` is disabled: requests sent from VS Code are
  forced to read-only;
- `Joe: Enable Maintenance Actions` is disabled: restarting the server is
  hidden and refused;
- unapproved workspaces can neither create a conversation nor send a request.

These settings prevent accidental actions, but they are not a strong
authorisation: a user with access to the same system account and to the local
API can bypass them. Genuinely restricted public profiles have to be enforced
by the Joe server.

When a project or a conversation requests `danger-full-access`, Joe suspends
the launch and shows an "Allow once" confirmation. Without it, no run is
created and no message is added to the history. Once confirmed, Codex, Claude
or Gemini receives its real full mode for that run only. This run-level
confirmation works in Joe Web and VS Code; the per-command prompts specific to
each CLI remain future work.

Joe must not be exposed directly on the Internet. A non-local bind is refused
without `--allow-remote`, and that option is reserved for a network environment
that is already protected.

## Useful commands

```bash
joe --help
joe doctor [-C /project]
joe web --foreground [-C /project]
joe restart [-C /project]
joe stop [--port 8765]
joe kill
joe logs [--port 8765] [--tail 200]  # Windows background server
joe sync
```

`restart`, `stop` and `kill` manage tmux sessions on Linux/macOS and native
detached processes on Windows. On Windows, `logs` reads the persistent server
log. Closing a client interface does not erase any conversation.


## Quick troubleshooting

- "Server unreachable": run `joe web` on the same machine as the extension.
- Remote-SSH shows the wrong project: check that the extension is installed on
  the SSH side and that Joe was started on that host.
- Conversations are not listed in VS Code: that is deliberate, use
  "Joe: Open Web interface".
- Port already in use: pick another `--port` and mirror the same URL in
  `Joe: Server Url`.
- Missing agent: install and authenticate its CLI, then run `joe doctor` again.

Conversations and logs belong to the target project, never to the Python
package or to the extension.

## Autonomous plans and quotas

The clock button in the activity panel opens **Autonomous plans**. It lets you
pick a conversation, a start time and a sequence of simple steps (one per
line). Joe runs a single step at a time, keeps its state across restarts, and
waits for a known quota reset instead of giving up.

The **Reserved AI** section is a project setting. In automatic mode, Joe
balances providers according to the task and the known headroom. If Claude,
Codex, Gemini or Copilot is reserved, Joe makes it the primary agent for the
project's new automatic requests and waits for its next known window when that
headroom is insufficient. The REVIEW and CONSENSUS workflows may still call
another agent for the review. An agent chosen explicitly in a conversation
stays a one-off, higher-priority choice.

A plan is deliberately bounded to 24 steps and 5 corrections per step. It stops
on a Git conflict, a missing permission or a required human validation. Full
access is never granted to an autonomous plan. Worktree integration can be
automatic, but commit and push remain governed by the project's delivery
settings.
