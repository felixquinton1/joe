# Public media

Release screenshots belong in this directory. Keep them free of usernames,
absolute paths, company names, private repository names, tokens, and provider
account information.

Current interface screenshots, all 1600×915 and framed identically:

- `joe-overview.png` — the Web interface at rest;
- `joe-consensus-running.png` — a consensus with its proposals complete and its
  cross-reviews still running;
- `joe-consensus.png` — the same run once the arbiter has published its
  synthesis.

`joe-social-preview.png` is a 1280×640 crop of the overview prepared for
GitHub's social preview. Upload it from **Settings → General → Social preview**;
GitHub does not expose this upload through its public API.

Still missing: a tasks or Git-diff review, and the VS Code launcher.

Use PNG for screenshots. Crop empty browser chrome when practical, keep the
same theme and window size across images, and verify every visible conversation
is synthetic before committing.

When a real capture shows an absolute path — the provider activity panel prints
the paths an agent touched — blur that line rather than dropping the panel. The
tool names carry the information; the paths do not.

The README does not embed them today, and that is deliberate. A relative path
does not render on PyPI, which serves the same file as the project
description, while an absolute `raw.githubusercontent.com` link returns 404
while the repository is private — the README showed broken images either way.
These files stay here as release media. Embed them only once the repository is
public, and check both renderings before doing so.
