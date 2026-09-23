# Joe HTTP/SSE API — MVP contract

> Status: validated. Current client contract: `api_version: "1.2"`.

## Scope and compatibility

This contract covers the interface the Web client and the future VS Code client
need. It is not a remote or multi-user API.

- Transport: HTTP/1.1 on `127.0.0.1:8765` by default.
- Structured bodies: UTF-8 JSON.
- Events: Server-Sent Events (SSE).
- Maximum JSON body size: 1 MiB.
- `version` identifies the Joe package.
- `api_version` identifies this contract independently of the package.
- A client accepts added fields within the same major version.
- A removal, a rename or a change of meaning requires a new major API version.

Joe 1.1 authenticates the local API and enforces a capability profile. A
non-local bind stays refused without an explicit option.

Joe 1.2 tightens two points without adding a route:

- `full_access_approved` in the body of `POST /api/runs` **no longer authorises
  anything**. Full access requires a durable `approval_id` in the `approved`
  state, consumed exactly once. The field is ignored if still sent: a client
  that relied on it now receives `428` along with the `approval_id` to have
  approved. No published 1.x client depended on that field.
- Downloading and deleting an attachment are partitioned per project and expect
  `?project={id}`. An identifier alone no longer takes a file out of its
  project: a mismatch answers `404`.

## MVP endpoint matrix

| Method | Endpoint | Success | Useful errors | Client use |
|---|---|---:|---:|---|
| GET | `/api/status` | 200 | — | identity, compatibility, project, providers |
| POST | `/api/pair` | 200 | 401 | exchange the fragment for a cookie |
| POST | `/api/auth/rotate` | 200 | 401, 403 | revoke sessions and rotate the secret |
| GET | `/api/capabilities` | 200 | — | models, efforts and execution modes |
| GET | `/api/usage` | 200 | — | cached quotas |
| GET | `/api/usage?force=1` | 200 | — | explicit quota refresh |
| GET | `/api/projects` | 200 | — | project list |
| GET | `/api/projects/{id}` | 200 | 404 | one project |
| POST | `/api/projects` | 201 | 400, 413 | create a project |
| PATCH | `/api/projects/{id}` | 200 | 400, 404, 411, 413 | modify a project |
| GET | `/api/conversations` | 200 | — | conversation list |
| GET | `/api/conversations/{id}` | 200 | 404 | history and settings |
| POST | `/api/conversations` | 201 | 400, 413 | create a conversation |
| PATCH | `/api/conversations/{id}` | 200 | 400, 404, 411, 413 | settings and rename |
| DELETE | `/api/conversations/{id}` | 200 | 404, 409 | deletion |
| POST | `/api/runs` | 202 | 400, 409, 411, 413 | start a run |
| GET | `/api/runs/active` | 200 | — | reconciliation after a reconnection |
| GET | `/api/events/{run_id}` | 200 | 404 | SSE stream of an in-memory run |
| POST | `/api/runs/{run_id}/cancel` | 202 | 404 | cancellation |
| GET | `/api/history` | 200 | — | list of persistent logs |
| GET | `/api/history/{run_id}` | 200 | 404 | one full persistent log |
| GET | `/api/projects/{id}/skills` | 200 | 404 | project skills (with the `active` flag and `scope`) |
| POST | `/api/projects/{id}/skills/import` | 201 | 400, 404 | import a skill shared by every provider |
| POST | `/api/projects/{id}/skills/promote` | 201 | 400, 404 | promote a project skill to a shared one |
| GET | `/api/skills/global` | 200 | — | skills shared across every project |

The Git review endpoints (`reject`) are still used by the Web client but are
not part of the first, read-only VS Code client.

### Durable tasks

| Method | Route | Role | Result |
|---|---|---|---|
| `GET` | `/api/tasks` | viewer | Recent tasks |
| `GET` | `/api/tasks/{id}/diff` | viewer | Worktree diff |
| `POST` | `/api/tasks/{id}/integrate` | maintainer | Asynchronous integration accepted (`202`) |
| `DELETE` | `/api/tasks/{id}` | maintainer | Task and worktree deleted |

## Minimal schemas

### Status

`GET /api/status` returns at least:

```json
{
  "version": "0.x.y",
  "api_version": "1.2",
  "project": "/path/to/project",
  "providers": ["codex", "claude", "antigravity", "copilot", "cursor-agent"],
  "provider_catalog": [{"id": "codex", "label": "Codex"}],
  "modes": ["fast", "review", "consensus"],
  "network_control_providers": ["codex"]
}
```

The client checks the major version of `api_version` before any mutation.

`network_control_providers` lists the providers whose command genuinely varies
with the project's or conversation's "Web access" setting. The other CLIs
expose no egress switch: the setting does not constrain them, and a client
should say so to the user rather than presenting a blanket guarantee.

## Authentication and roles

`GET /api/status` and the static files stay public, for diagnostics and for
opening Joe Web, but distribute no secret. The CLI opens a URL whose fragment
carries the token; the page exchanges it once through `POST /api/pair` for an
`HttpOnly`, `SameSite=Strict` cookie valid for 30 days, then clears the
fragment from history. Every other endpoint requires that cookie or
`Authorization: Bearer <token>`.

The token is generated in the user data directory with `0600` permissions; it
is never placed in the project or in Git.

| Profile | Capabilities |
|---|---|
| `viewer` | cached quotas, conversations, history, events, **reading** projects |
| `operator` | `viewer` capabilities, conversations and ordinary runs |
| `maintainer` | `operator` capabilities, project mutation, Git rejection and full project access |

An unauthenticated request receives `401`; a valid but insufficient token
receives `403`. The one-off `428` confirmation for full access remains required
even for a `maintainer`.

`GET /api/projects` is available from `viewer` upwards: Joe Web needs it to
render, and restricting it made the interface unusable under
`--profile viewer` or `--profile operator`. Every project **mutation** stays
reserved to `maintainer`.

Actively refreshing quotas (`GET /api/usage?force=1`) requires `maintainer`,
because it starts a provider subprocess.

A `viewer` may write the single `unread_completion` field of a conversation:
acknowledging a read state is not a content mutation. Any other field in a
`PATCH /api/conversations/{id}` requires `operator` and answers `403`
otherwise.

`POST /api/auth/rotate`, reserved to `maintainer`, atomically replaces the
server secret and immediately revokes earlier cookies and Bearer tokens.

### Starting a run

`POST /api/runs` accepts:

```json
{
  "request": "User request",
  "conversation_id": "identifier",
  "agent": "codex",
  "mode": "review",
  "model": "gpt-5.6-sol",
  "effort": "high",
  "execution_mode": "workspace-write"
}
```

Only `request` and `conversation_id` are mandatory. Absent values fall back to
routing and to the conversation settings. Success returns `{"run_id": "…"}`
with HTTP 202.

The legacy `danger-full-access` mode asks for an HTTP 428 confirmation, but
does not disable the provider's sandbox. Once confirmed, it grants full access
limited to the conversation's project root and to the additional roots
explicitly configured for that same project.

Confirmation always goes through a durable approval. The `428` returns
`{"approval": "full-access", "approval_id": "…"}`; the client has that approval
granted (`PATCH /api/approvals/{id}` with `{"decision": "approved"}`, reserved
to `maintainer`), then resubmits the same `request` with `approval_id`
attached. The approval is then consumed and cannot be replayed: an identical
second launch produces a new `428`. No request-body field can substitute for
that cycle.

Two simultaneous launches for the same conversation produce exactly one HTTP
202 and one HTTP 409. The rejection persists no second user message.

## Errors

JSON validation errors return an `{"error": "…"}` object:

- 400: invalid JSON, invalid value or invalid identifier;
- 409: conversation busy, or operation incompatible with the current state;
- 411: missing `Content-Length` header when a body is required;
- 413: body larger than 1 MiB.

Some legacy endpoints still return `null` or a boolean with a 404. A client must
therefore decide from the HTTP code first, and never infer success from the
shape of the body alone. A future harmonisation of 404 bodies will be additive
within 1.x as long as the existing fields are kept.

## SSE contract

`GET /api/events/{run_id}` returns `text/event-stream`.

Every event contains:

```text
id: 2
data: {"event_id":2,"at":1785310000.0,"type":"complete",...}
```

Guarantees:

- `event_id` starts at 1 and increases monotonically within a run;
- the SSE id and `data.event_id` are identical;
- `Last-Event-ID: N` resumes strictly after `N`;
- `?after=N` provides the same semantics for clients that cannot control the
  SSE header;
- keepalives are `: keepalive` comments and do not move the cursor;
- the stream closes after the last event of a finished run;
- a run absent from memory returns 404.

## Reconciliation

After an interruption, the client follows this order:

1. call `/api/runs/active`;
2. if the run is active, resume the SSE after the last `event_id` received;
3. if the run has finished but is still in memory, resume the SSE the same way
   until it closes;
4. if it is no longer in memory, rebuild the display from the conversation then
   from `/api/history/{run_id}`;
5. never automatically replay a mutation to recreate a lost stream.

## Contract evidence

The dedicated tests in `tests/test_api_contract.py` lock down:

- identity and independent versioning;
- JSON errors and maximum size;
- atomic reservation with HTTP 409;
- identical behaviour of the `Last-Event-ID` and `after` cursors.

The more detailed functional tests stay in `tests/test_web.py`.

## Automations (API 1.3)

- `GET /api/automations` lists persistent sequential plans;
- `POST /api/automations` creates a plan with `conversation_id`, `steps`,
  `scheduled_for`, `mode`, `execution_mode`, `max_retries` and
  `auto_integrate`;
- `POST /api/automations/{id}/cancel` cancels the plan and its active run.

Mutations require the `maintainer` profile. `execution_mode` is limited to
`read-only` and `workspace-write`: an autonomous plan cannot approve its own
full access.
