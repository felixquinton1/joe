# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for a security vulnerability.

Use the repository's **Security → Report a vulnerability** form to open a
private report. Include the affected version (`joe --version`) and a minimal
reproduction when possible.

The project aims to acknowledge reports within one week. Joe is maintained on
available time, so this is a target rather than a service-level commitment.

## Supported versions

Only the latest published version receives security fixes.

## Security model

Joe is a local orchestrator:

- it launches provider CLIs that are already installed and authenticated;
- it grants them the project access level selected by the user, including
  command execution and file modification when explicitly allowed;
- it binds the Web interface to loopback by default;
- remote binds require `--allow-remote` and remain authenticated;
- autonomous campaigns can call models and commands without interaction, but
  are bounded by user-configured budgets and stop conditions.

Joe does not request or store provider passwords. Provider credentials remain
under the control of their respective CLIs.

Please report behavior such as:

- command execution outside the configured project scope;
- disclosure or bypass of the local authentication token;
- cross-project access to files or conversation data;
- a third-party page acting on a local Joe instance;
- unauthenticated disclosure of machine-specific information.

## Known limitations

- Loopback servers accept only loopback `Host` authorities, reducing DNS
  rebinding exposure. An explicit `--allow-remote` bind accepts forwarded host
  names so it remains compatible with reverse proxies.
- `/api/status` is intentionally public so the UI can detect and pair with the
  server. It exposes product version, provider catalog, modes, and whether
  authentication is required; workspace paths and profiles remain protected.
- `--allow-remote` serves plain HTTP. Joe terminates no TLS, and the session
  cookie carries no `Secure` attribute, so on a non-loopback bind the pairing
  token and the cookie travel unencrypted and anyone on the path can replay
  them. Prefer an SSH port-forward to the default loopback bind, or put a
  TLS-terminating reverse proxy in front.
