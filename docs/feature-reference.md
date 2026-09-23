# Joe feature reference

This document is the functional reference for Joe. It describes what the
product does, how its main features interact, and where its current boundaries
are. For installation steps and day-to-day commands, see the
[user guide](user-guide.md). For the wire contract, see the
[HTTP/SSE API contract](api-contract.md).

The reference follows the current `1.3.x` product line. Provider capabilities
can still change when their third-party CLIs change.

## 1. Product model

Joe is a local control room for AI command-line agents. It does not provide a
model or replace a provider account. It coordinates CLIs that are already
installed and authenticated on the same machine, gives them a shared project
context, and presents their work through one persistent interface.

Joe is designed around five durable objects:

| Object | Purpose |
| --- | --- |
| **Project** | Defines the working roots, shared context, permissions, Git policy, skills, and provider preferences. |
| **Conversation** | Holds the user-visible discussion and its conversation-specific routing choices. |
| **Run** | Represents one submitted request and its live provider execution. A run is not a conversation. |
| **Task** | Tracks the durable state, result, approvals, Git changes, and recovery information associated with a run. |
| **Automation** | Runs a scheduled step plan or a bounded autonomous campaign without requiring an open browser. |

The Web interface is the primary product surface. The terminal and VS Code
companion use the same local server and the same persisted data.

## 2. Supported AI providers

Joe detects and drives the following local CLIs:

- OpenAI Codex;
- Claude Code;
- Antigravity CLI;
- GitHub Copilot CLI;
- Cursor CLI (`agent`, with `cursor-agent` retained for compatibility);
- Gemini CLI for enterprise installations that still support it. Consumer
  Gemini CLI accounts are considered retired and are not selected by default.

Only installed and enabled providers are eligible. A provider can be disabled
from **Options → Preferences → AI CLIs** without uninstalling it.

Joe uses each provider's existing authentication and configuration. It never
asks for or stores provider passwords or API keys. Native provider tools,
commands, model catalogues, MCP configuration, retention policies, billing,
and quotas remain the provider's responsibility.

### Model and effort control

Each conversation can leave the provider, model, and reasoning effort on
automatic selection or override them for the next request. Joe only displays
models it can identify safely from the provider catalogue or from its maintained
compatibility data. If a CLI exposes no reliable catalogue, `auto` delegates
the choice to that CLI.

The activity panel records the provider, resolved model, and effort for every
real provider call, including fallbacks and individual workflow stages.

## 3. Routing

Joe combines deterministic rules with an optional lightweight LLM classifier.

1. Explicit user choices always win.
2. Unambiguous local actions and obvious intents use the deterministic router.
3. A genuinely ambiguous request may be classified once by a fast available
   model.
4. Provider health, quota headroom, recent failures, latency, task complexity,
   requested capabilities, and model tier are considered before admission.
5. If classification fails or times out, Joe falls back to the deterministic
   route instead of blocking the request.

The classifier returns a bounded decision: intent, complexity, workflow,
provider, model tier, effort, and confidence. It cannot broaden permissions,
downgrade a write intent already detected locally, or override an explicit
agent or workflow choice.

`JOE_DISABLE_LLM_ROUTER=1` disables LLM classification while preserving the
deterministic router.

### Fallback behavior

When a provider is unavailable, unhealthy, unauthenticated, over quota, or
incompatible with a stage, Joe can select another eligible provider. A fallback:

- keeps the same or a stricter permission ceiling;
- is shown explicitly in the activity stream;
- does not pretend the original provider completed the stage;
- respects an explicitly fixed provider unless quota-aware waiting was chosen;
- never treats unknown quota data as guaranteed capacity.

## 4. Workflows

### FAST

FAST makes one provider call. It is intended for questions, analysis, research,
small edits, and focused implementation work. The selected provider can use its
normal tools within the run's permission ceiling.

### REVIEW

REVIEW uses two distinct roles:

1. the primary provider answers or implements the request;
2. another eligible provider reopens the actual changed files and checks the
   implementation against the original request;
3. when the run is allowed to modify files, the reviewer applies justified
   corrections directly instead of merely writing a critique.

The reviewer receives no broader permissions than the primary provider. For a
read-only request, it remains read-only and returns an assessment. The final
summary describes the resulting state after review, not the primary provider's
obsolete pre-review state.

### CONSENSUS

CONSENSUS is intended for research, architecture, experimental strategy, and
decisions where independent views matter.

- eligible proposals run in parallel;
- proposals are produced independently;
- cross-reviews challenge assumptions, evidence, and disagreements;
- progress is visible stage by stage;
- the final synthesis is written only after the available stages finish;
- failed or quota-limited stages are reported as degraded, not fabricated;
- an eligible fallback may replace a failed participant when possible.

Consensus reduces variance but is not a proof of truth. Agreement between
models can still reflect shared assumptions or shared training biases.

### Plan-first execution

The composer can request a plan before any modifying work begins. The plan is
shown for acceptance, adjustment, or rejection. Once accepted, execution is a
separate run and can be assigned to the provider best suited to implement it.

## 5. Projects and conversations

### Projects

A project stores:

- one primary working root and optional additional roots;
- stable project context and conventions;
- default provider and workflow preferences;
- AI access, network, MCP, quota, worktree, and Git delivery policies;
- project-specific skills;
- conversations, tasks, files, approvals, and automations.

Projects can be nested for organization. **Free conversation** is the neutral
home for discussions that do not belong to a repository or long-lived task.
Projects can be reordered, collapsed, moved to a reversible trash, restored,
or permanently removed from Joe. Trashing a project never deletes its files
or Git repository from disk.

### Conversations

Conversations retain their own agent, workflow, model, effort, permission, and
ordering choices. They can be renamed, pinned, reordered, deleted with
confirmation, and searched across projects.

Several conversations can run concurrently. One conversation owns at most one
active run; additional prompts can wait in its queue. A queued prompt can be
copied, and active work can be interrupted before a revised request is sent.

The conversation list shows running and newly completed work. Opening a
completed conversation clears its unread completion marker. Refreshing or
restarting Joe restores the last opened conversation and reconciles its active
run from server state.

### Response rendering

The transcript supports:

- Markdown headings, lists, links, tables, and code blocks;
- syntax-preserving copy actions;
- inline and block mathematics rendered with vendored KaTeX;
- structured questions rendered as action buttons;
- automatic scrolling while the reader is at the bottom;
- stable local scroll position while the reader is inspecting older content.

Joe streams status and tool activity, not private chain-of-thought.

## 6. Context and memory

Joe maintains a compact handoff under `.agentflow/` so different providers can
continue the same work without receiving the full transcript on every call.

| File or directory | Role |
| --- | --- |
| `project.md` | Stable project facts and conventions. |
| `session.md` | Current objective, decisions, and affected files. |
| `handoff.md` | Compact context for the next provider. |
| `config.yaml` | Project-level Joe settings. |
| `runs/` | Durable execution records and recovery information. |
| `skills/` | Project skills loaded into provider context. |

Long conversations are compacted in the background when they cross the
configured threshold. Compaction reduces future context size; it does not
replace the visible conversation history.

Repository-state questions receive fresh repository evidence rather than
being answered only from an older handoff. Joe records the observed revision
for evidence that depends on the current checkout.

## 7. Tasks, queues, and recovery

Every submitted request creates a durable task. Task states distinguish queued,
running, waiting, blocked, completed, failed, cancelled, and interrupted work.
The task panel links back to the exact prompt or response in its conversation.

The compact delivery pipeline tracks:

`Request → Implementation → Validation → Diff → Delivery`

### Recovery levels

Joe distinguishes several kinds of recovery:

- **display recovery** reconstructs the visible transcript after a reconnect;
- **run recovery** reattaches to a provider process that is still alive;
- **quota recovery** waits until the next known provider window;
- **automation recovery** resumes the current durable step or campaign attempt;
- **safe restart** reruns a step only when no reliable checkpoint or finished
  result exists.

Recovery metadata prevents a finished response from being displayed twice and
prevents a known external experiment from being submitted twice. When exact
resumption is impossible, Joe states that the step will restart rather than
claiming it continued from a checkpoint.

## 8. Permissions and approvals

### Project AI access

Projects expose three access policies:

| Policy | Ordinary behavior |
| --- | --- |
| **Read-only** | Providers can inspect but cannot run modifying commands or edit project files. |
| **Manual** | Ordinary project work pauses for user confirmation. |
| **Automatic** | Ordinary work can proceed unattended inside the configured project roots. This is the default for new projects. |

`manual` and `auto` have the same project boundary. Automatic mode removes the
ordinary confirmation; it does not grant unrestricted machine access.

### Full access

`danger-full-access` is a separate capability. It always requires a durable,
single-use approval validated by a `maintainer` server profile, regardless of
whether the project uses manual or automatic access. A request-body flag cannot
self-approve the run, and a consumed approval cannot be replayed.

### Server profiles

- `viewer` can read the interface and project data;
- `operator` can operate permitted runs but cannot mutate protected project
  administration or approve full access;
- `maintainer` can manage projects and approve broader capabilities.

The server binds to loopback by default and pairs browsers with a local secret
stored outside the repository. `joe auth rotate` revokes existing browser
sessions. Non-loopback serving requires the explicit `--allow-remote` option
and an already protected network; Joe does not provide TLS termination.

### Roots and network access

File work is limited to the project's primary and additional configured roots.
Web and network behavior otherwise follows each provider's native CLI. At
present, only Codex exposes a network switch Joe can enforce; the interface
states that limitation beside the control.

## 9. Git and worktree delivery

Joe captures repository state before and after modifying work and attributes
the run's changes separately from pre-existing user changes.

The Git review presents:

- total files changed, insertions, and deletions;
- a collapsible file list and per-file diff;
- keep or reject actions while changes remain uncommitted;
- clear attribution when unrelated changes predated the run.

### Isolated worktrees

When enabled, a modifying task receives a dedicated Git worktree and
`joe/<task-id>` branch. The main checkout remains untouched while the task
runs. The user can view the diff, integrate it, or discard the worktree.

If the target branch moved, Joe rebases the task before integration. Conflicts
receive at most three resolution passes. Joe never applies a silent
last-writer-wins policy: unresolved conflicts stop integration, notify the
user, and preserve the worktree for manual inspection.

### Delivery policy

Automatic commit and push are configured per project. When enabled, validated
changes are delivered without an additional selective-rejection window. When
disabled, the user can inspect and reject selected changes before committing.

## 10. Files, documents, and search

The project library accepts uploaded or dragged files and keeps them scoped to
their project. Files can be attached from the **Tools** menu or referenced with
`@name`. Joe shows the attachments that will actually be sent before launch.

If an attachment also exists live inside the project, Joe favors the live file
to avoid giving the provider two conflicting versions. DOCX documents are
extracted locally with XML entity protections before their text is supplied to
a provider. File size and request-body limits prevent unbounded uploads.

Search covers conversations, tasks, and file names across projects. Results
open the corresponding conversation, task location, or file. Search data stays
local.

## 11. Skills

Joe has a visual skill manager with two scopes:

- **project skills**, used only by one project;
- **shared skills**, applied to every project.

A skill can be created from plain-language instructions in the interface,
created from a direct conversation request, or imported from an existing
`SKILL.md` file or directory. Existing provider-specific instructions can be
copied into Joe's shared format when their content is portable.

Joe loads the resulting skill instructions into the context passed to eligible
providers. Skill creation is a local deterministic action when the request is
unambiguous, so it does not spend model tokens merely to write a small
`SKILL.md` file.

## 12. Quotas and provider health

The quota panel represents only data the provider actually exposes. It keeps
unknown availability distinct from an exhausted quota and displays reset times
when known. Provider-specific account plans and windows are shown without
inventing a universal quota model.

Routing uses:

- installation and authentication state;
- recent process, timeout, and quota failures;
- fresh versus stale quota data;
- remaining headroom and reset time when available;
- observed provider latency;
- explicit provider reservations.

The refresh action updates supported providers. `joe doctor` checks local
installation and storage without spending quota. `joe doctor --live` sends a
short real prompt to every installed provider and therefore consumes a small
amount of quota.

## 13. Scheduled plans and Autonomous campaigns

### Scheduled step plans

A step plan executes an ordered list of bounded requests. It can start now, at
a chosen date, or after a known quota reset. Each step starts only after the
previous one has completed and been validated.

Plans support:

- FAST, REVIEW, or CONSENSUS per step;
- read-only or project-write access;
- a preferred provider or automatic balancing;
- bounded correction attempts;
- automatic integration of validated worktrees;
- durable status across browser and server restarts.

Full access is never granted silently to an unattended plan.

### Autonomous campaigns

Autonomous campaigns implement a bounded loop such as:

`research → code → experiment → inspect metrics → correct → decide`

Configuration includes:

- objective and context;
- FAST, REVIEW, or CONSENSUS reasoning;
- active-time, known-token, and model-call budgets;
- maximum steps and correction attempts;
- experiment command, metrics file, metric name, and optimization direction;
- optional daily execution window and weekday restriction;
- CPU/GPU policy, GPU index, and resource notes;
- provider reservation and quota-aware waiting.

The campaign persists attempt identities, subprocess metadata, checkpoints,
results, decisions, commits, and metrics. It can reattach to a living process,
reuse an already written result, invoke a configured resume command, or return
to planning when safe resumption is impossible. Three repeated losses of the
same step stop the campaign instead of creating an infinite loop.

Waiting for quota or for the next schedule window does not consume active-time
budget. Closing the browser does not stop the server-side campaign.

## 14. Activity, diagnostics, and transparency

The activity panel shows meaningful events rather than appending repetitive
heartbeat rows. A single live indicator updates elapsed time while new rows are
reserved for actual stages, commands, tool calls, fallbacks, errors, and
results.

Joe records:

- route and routing reason;
- real provider, model, and effort;
- workflow stage transitions;
- command and tool activity exposed by the CLI;
- fallback and degradation reasons;
- task duration and terminal status;
- Git changes and delivery outcome;
- automation checkpoints, experiments, and metrics.

Technical detail remains available in the activity panel while the main
conversation favors the plan, decisions, important progress, and final result.

## 15. Interfaces and lifecycle

### Web

Joe Web provides the complete project, conversation, task, automation, quota,
skill, approval, and Git experience. It is bilingual (English and French),
stores the language per browser, supports responsive panels, and uses native
browser zoom.

### CLI

The CLI supports one-shot requests, an interactive terminal session, routing
previews, explicit provider/workflow choices, diagnostics, URL pairing, token
rotation, synchronization, and server lifecycle commands.

### VS Code companion

The VS Code extension is deliberately a thin launcher. It starts, opens,
refreshes, restarts, and stops the same Joe Web instance on a local or
Remote-SSH host. It does not maintain a second conversation interface or a
second data store.

### Background process

On Linux and macOS, Joe uses `tmux` when available. On Windows, it uses a native
detached process and persistent log file. `joe stop`, `joe restart`, and
`joe kill` abstract over both mechanisms.

## 16. API and persistence

The local server exposes a versioned HTTP/SSE API used by Joe Web and the VS
Code companion. Server-sent events stream run progress and support cursor-based
reconnection while events remain available. Durable conversation and task
history reconstructs the visible state after in-memory events expire.

Writes use bounded request bodies, path validation, and atomic or locked local
persistence where concurrent clients can act. Conversation data, approvals,
provider logs, and `.agentflow` runtime state are ignored by project Git by
default and can be backed up separately.

## 17. Intentional limitations

Joe currently targets local, single-user use. In particular:

- it is not a hosted AI service or model gateway;
- it does not share one provider account between users;
- it does not guarantee provider quotas, prices, models, or CLI stability;
- it does not expose Joe directly to the Internet safely on its own;
- it does not provide a collaborative multi-user permission system;
- it does not expose or synthesize private model chain-of-thought;
- it does not silently merge unresolved Git conflicts;
- it does not promise exact checkpoint resumption when the underlying tool or
  experiment provides no resumable state;
- it cannot enforce a network-off mode for providers whose CLI has no such
  control;
- it cannot pre-authorize arbitrary MCP actions without support from the
  provider CLI.

These boundaries are surfaced explicitly so that a degraded or unsupported
operation is visible rather than represented as a successful one.
