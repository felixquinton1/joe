# Public media

Release screenshots belong in this directory. Keep them free of usernames,
absolute paths, company names, private repository names, tokens, and provider
account information.

No screenshot is currently committed. The previous set came from a real
instance, showed obsolete version 1.1.1, and was removed before publication.
Create future release media from a synthetic demo project only.

Use PNG for screenshots. Crop empty browser chrome when practical, keep the
same theme and window size across images, and verify every visible conversation
is synthetic before committing.

Do not use blur to hide sensitive text: it can preserve recoverable pixels.
Replace the area with an opaque fill or, preferably, use synthetic data from
the start.

The README does not embed them today, and that is deliberate. A relative path
does not render on PyPI, which serves the same file as the project
description, while an absolute `raw.githubusercontent.com` link returns 404
while the repository is private — the README showed broken images either way.
Embed new media only once the repository is public, and check both renderings
before doing so.
