# Changelog

All notable changes to Joe are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.3.3] — 2026-09-23

### Security

- Full access no longer rides on the confirmation setting. A project set to
  run unattended stopped requiring approval *for everything*, so an
  `execution_mode` passed in a request body was enough to obtain
  `danger-full-access` with no approval and no `maintainer` profile — an
  `operator` could do it. Not wanting to confirm ordinary work is not granting
  unrestricted access; the two now have separate locks. Access beyond the
  project always requires a durable approval, validated and consumed once,
  whatever the project's setting.

### Changed

- A project runs unattended by default. Confirming every request defeated the
  REVIEW and CONSENSUS workflows, where several providers run in sequence with
  nobody at the screen. The scope is unchanged: `manual` and `auto` both grant
  writing **inside the project** and nothing beyond — only the confirmation
  disappears. `read_only` remains available per project.
- The run-decision and Web project-dialog fallbacks now use the same `auto`
  default as project storage, including projects created or edited in the UI.

## [1.3.2] — 2026-09-23

### Security

- Loopback servers now reject non-loopback `Host` headers, and every HTTP
  response carries content-type, framing, referrer, and content-security
  protections. Explicit remote binds remain compatible with reverse proxies.
- DOCX extraction rejects DTD and custom-entity declarations before parsing.
- CI now runs Bandit in addition to Ruff and the platform test matrix.
- GitHub Actions, including the trusted PyPI publisher, are pinned to audited
  commit SHAs while Dependabot remains responsible for update proposals.
- CI declares least-privilege token permissions. The workflow carried no
  `permissions` block, so its `GITHUB_TOKEN` inherited whatever the repository
  grants by default — write, on many repositories. No CI job needs it.
- The security policy states what `--allow-remote` costs. Joe terminates no TLS
  and the session cookie carries no `Secure` attribute, so on a non-loopback
  bind the pairing token and the cookie travel unencrypted and can be replayed
  by anyone on the path. The loopback default was documented; the price of
  leaving it was not.

### Changed

- The README describes the current two-agent REVIEW workflow: the examiner
  reopens the changed files and applies justified fixes directly.
- The README opens with the providers Joe leads with. Its first two paragraphs
  still listed Gemini CLI, retired for consumer accounts, and left out
  Antigravity — while the installation table below had it right.
- The test suite no longer depends on how fast the machine's provider CLIs
  answer. Discovery runs once per session instead of falling on whichever test
  first crossed a request path, where it could outlast the client timeout: a
  privilege-escalation test failed on a socket timeout roughly one run in
  seven, while the refusal it checks was correct and immediate.
- Obsolete screenshots from a real Joe workspace were removed before the
  repository becomes public. Future media must use synthetic project data and
  opaque redaction rather than blur.

## [1.3.1] — 2026-09-23

### Changed

- REVIEW mode is a second pass over the code, not a second opinion about a
  report. The reviewer received the implementer's own write-up as the thing to
  judge, was launched read-only, and was told not to rerun anything but to
  "evaluate the candidate's recorded evidence" — so it assessed what the first
  agent said it had done. Any correction then went back to that same first
  agent, from a prose description of its mistake rather than from the code.

  The examiner now runs with the same rights as the author, is given the list
  of files the run touched, and fixes bugs and unmet conditions itself. A
  feature that works halfway is the usual outcome of a first pass, and that is
  what this pass exists to catch. The separate correction round disappears:
  three provider calls become two, and the final report comes from whoever
  last touched the code. A read-only request still gets an opinion, never a
  second pass — nothing was modified, so there is nothing to correct.

  The examination stage no longer runs on a deliberately lighter model either:
  capping it made sense while it only read, not now that it writes.

  In Git workspaces, Joe now verifies the repository delta made during the
  examination instead of trusting the examiner's verdict marker. The provider
  that materially produced the final state is also recorded as the final
  provider for conversation continuity and subsequent routing.

### Added

- Windows now keeps Joe Web alive in a native detached process without tmux or
  administrator rights. `joe stop`, `joe restart`, `joe kill` and the new
  `joe logs` command use the persisted process state under
  `%LOCALAPPDATA%\\Joe\\run`.

### Fixed

- `joe restart` authenticates its status probe, so it restores the running
  server's project and profile instead of silently falling back to the current
  terminal directory.

## [1.3.0] — 2026-09-23

### Added

- Antigravity CLI joins the supported providers, as `agy`. It replaces Gemini
  CLI, retired for consumer accounts in June 2026. Its four access levels map
  to real CLI modes — `--mode plan`, `--mode accept-edits`,
  `--dangerously-skip-permissions`, `--sandbox` — and its activity stream is
  parsed from a real capture, so tool calls appear in the panel like any other
  provider's. When a permission cannot be granted in a non-interactive call,
  the CLI soft-denies and returns an empty answer; Joe names the missing
  permission instead of showing a blank bubble.

### Changed

- Gemini is no longer a default. Its CLI stopped serving consumer accounts on
  18 June 2026, superseded by Antigravity CLI, yet it was the first fallback of
  three providers and the preferred consensus arbiter — defaults that named the
  provider most likely to fail. An enterprise licence still works, so Gemini
  stays available, last in line, and both the panel and `joe doctor` say what
  happened.

### Fixed

- The Agent menu follows a language change. Its "Automatic" entry is built in
  JavaScript and carries no translation marker, so the document-wide
  translation never reached it and nothing rebuilt that menu.

- Antigravity's models are ranked rather than picked by position. Its
  catalogue publishes an identifier and a label, no description and no cost, so
  nothing was classified and the requested tier fell back to a place in the
  list: "standard" served a 3.6 generation while "strong" served a 3.8. The
  tier table is now keyed by provider as well as by model, because the same
  identifier does not mean the same thing everywhere — `claude-sonnet-4-6` is a
  previous generation in Claude Code's catalogue and the most recent Sonnet
  Antigravity offers, and one table gave the first ranking to the second.

- A large-context request reaches the CLI that still serves it. The rule that
  picks a provider for that work named Gemini outright, so Antigravity was
  never chosen for it, and with Gemini absent the fallback led to Codex instead
  of to its replacement. Naming Gemini, or forcing it, still runs it.

- A machine with a single provider CLI works. The router picked a provider from
  the request alone — codex for anything code-shaped — whether or not it was
  installed, so the interface announced an agent the machine did not have. The
  fallback hid that for one-step runs, but REVIEW and CONSENSUS failed outright
  on "All providers failed", because their second stage found nobody. Those
  modes now degrade to a single-agent answer, and say so.

## [1.2.1] — 2026-09-22

### Fixed

- Answers follow the language of the current request instead of inheriting the
  interface language. Explicit requests such as “answer in French” still take
  precedence, while short ambiguous technical prompts use the interface as a
  fallback.
- The first-run AI CLI setup opens independently from conversations, tasks and
  automation loading. Its renewed browser marker also shows it once to users
  whose earlier local state incorrectly skipped the onboarding.

## [1.2.0] — 2026-09-22

### Added

- A per-project setting allows the MCP tools already configured in a CLI. An
  MCP tool always asks for explicit permission, even to read, and a
  non-interactive call cannot grant it — so it was refused at every access
  level below full. The setting is off by default: such a tool acts outside the
  project, and widening that stays an explicit decision.
- The welcome screen cannot be dismissed before its diagnostic finishes,
  Escape included. It is the only page that says which CLIs are missing and how
  to install them, and it does not come back on its own.
- A missing CLI is guided step by step: what it is best at, the prerequisite
  when the install command needs a newer Node than the machine has, the command
  to run, the one to sign in with, and what to do next.
- Provider CLIs are now discoverable: `joe doctor` and a permanent **Manage AI
  CLIs** panel list what is detected, print the install command and official
  page for what is missing, and let a detected CLI be switched off.

### Changed

- The user guide and the HTTP/SSE API contract are written in English, like the
  rest of the public surface. The README linked to them as "User guide" and
  "API contract" while both were still French.
- Each workflow stage now gets the tier it deserves. A cross-review reads a
  proposal and criticises it, which is lighter than producing one. Only one
  stage received a model at all — the chosen provider's proposal — while every
  other ran on its CLI's own default, which Joe does not control.
- A shrinking provider quota steps the tier down instead of spending the last
  slice of the top model. The remaining headroom was already read to pick a
  provider, never to pick how large a model to ask for. A provider that
  publishes no quota is not presumed to be tight.
- The routing tier now decides which model runs. It was resolved by position in
  the provider's list, so `standard` returned the middle entry — on the Claude
  catalog, the most expensive model of the six, dearer than the one returned
  for `strong`. Tiers are read from what providers publish about their own
  models, with a declared catalog for what a description cannot settle, and an
  unclassified range still routes exactly as before.
- A model billed per token stays out of automatic routing, whatever its tier.
  Spending outside the subscription should follow a decision, not the workflow
  picked for a request. It remains selectable by hand.
- A review or a consensus no longer forces the flagship model and maximum
  effort on every stage. Anything other than FAST was declared complex.
- The markers that flag a demanding request exist in English too. They were
  French only, so the same need was routed differently depending on the
  language it was written in.
- Joe only routes to provider CLIs it can actually run. The router previously
  received every declared provider, so a first request on a machine with one
  CLI installed could be sent to a missing one, fail, and fall back.
- `joe doctor` answers in English, like the rest of the public surface.

### Fixed

- A Claude run no longer risks losing its prompt. `--allowedTools` accepts
  several values, so with no flag after it the CLI took the prompt for one more
  tool name and the run failed on "Input must be provided". Only the flags that
  happened to follow were hiding it.
- An empty provider answer now says why. A command that drives an interactive
  session returns neither an answer nor an error through a non-interactive
  call, so the conversation showed an empty bubble that looked like a failure.
  Nothing is refused upfront: custom commands do work, and rejecting `/review`
  or `/init` on the strength of their name would have broken them.
- Cursor is detected again. Its CLI is now installed as `agent`, and Joe looked
  only for `cursor-agent`, so no current installation was ever found. Joe tries
  both, and verifies that a binary found under the generic name really is the
  Cursor CLI before adopting it.

## [1.1.2] — 2026-09-21

### Fixed

- Removed screenshot embeds that could not render while the source repository
  remained private.

## [1.1.1] — 2026-09-21

### Added

- Durable autonomous recovery across quota windows and server restarts.
- English and French response-language propagation across FAST, REVIEW, plan,
  and CONSENSUS workflows.
- Explicit TestPyPI and PyPI trusted-publishing workflow.

### Changed

- Public documentation and VS Code Marketplace metadata are now written in
  English.
- Provider activity and workflow labels are translated by the interface rather
  than emitted as French display strings by the backend.

## [1.0.0] — 2026-09-03

Première version publiable. Le dépôt ne contient plus que l'orchestrateur, et
chaque comportement propre à un fournisseur se déclare au registre.

### Ajouté

- **Cursor** rejoint Codex, Claude, Gemini et Copilot. La CLI attendue est
  `cursor-agent`, sans fenêtre : `cursor` lance l'éditeur et ne convient pas.
  Le jeu d'options se limite à ce que la documentation confirme, faute d'avoir
  pu exécuter cette CLI.
- **Lecture et suppression des skills**, par projet ou communs, depuis
  l'interface. Tout skill listé entre dans le contexte partagé des
  fournisseurs : il fallait pouvoir vérifier ce qu'il demande, et le retirer.
- **Autorisation durable de l'accès complet.** La confirmation proposait
  seulement « une fois » ; « Toujours autoriser » bascule le projet en accès
  automatique, réversible dans ses réglages.
- **Campagnes autonomes décrites au formulaire** : titre, objectif, commande,
  contexte et métrique suivie.

### Modifié

- **Catalogue Claude complet et à jour.** Il tenait trois alias, or un alias
  suit le défaut de la CLI, qui reste une version en arrière : l'interface
  proposait Opus 4.8 alors qu'Opus 5 était disponible. Six modèles y figurent
  désormais par identifiant complet.
- **L'arbitre du consensus se déduit du disponible** au lieu d'être nommé dans
  le code. Un tiers est préféré ; s'il ne reste que les deux proposants, l'un
  d'eux arbitre plutôt que de faire échouer le consensus.
- **La bascule pour quota vaut pour tout fournisseur** ayant un pair déclaré,
  et non plus pour deux noms écrits en dur.

### Corrigé

- **Les questions de fin de réponse redeviennent des boutons.** Le parser
  exigeait un balisage strict ; les modèles rendent aussi le JSON nu, qui
  s'affichait alors tel quel.
- **Écoute sur la loopback IPv6.** `::1` passait la validation puis échouait au
  bind : un transfert de port résolvant `localhost` en IPv6 — le cas courant
  sous VS Code Remote — ne trouvait personne à l'écoute.
- **Une demande longue ne déclenche plus la sonde de santé.** Un simple mot
  suffisait à remplacer une spécification entière par un test arithmétique.
- **Un accès en écriture peut réellement lancer des commandes.** L'auto-
  acceptation ne couvrait que l'édition de fichiers : les tests que le modèle
  venait d'écrire restaient refusés.

### Retiré

- **La campagne de recherche DaT Parkinson**, qui était écrite en dur dans le
  produit : quatre fichiers à la racine, et un bouton envoyant toujours le même
  objectif, les mêmes URL et jusqu'au modèle de GPU d'une machine précise.

### Limites connues

- Le quota restant n'est lu que chez Codex, Claude et Gemini. Ailleurs, un
  quota épuisé reste détecté au moment du run et la demande bascule sur un
  autre fournisseur.
- Gemini et Copilot n'exposent aucune liste de modèles et n'en proposent donc
  qu'un, « auto », qui laisse la CLI choisir. Le modèle voulu peut être imposé
  dans les réglages du projet.
- Le contexte est plafonné en caractères, pas en jetons, et le même est envoyé
  à tous les fournisseurs sans tenir compte de leurs fenêtres respectives. La
  troncature est silencieuse.
