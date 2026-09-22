# Changelog

All notable changes to Joe are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Provider CLIs are now discoverable: `joe doctor` and a permanent **Manage AI
  CLIs** panel list what is detected, print the install command and official
  page for what is missing, and let a detected CLI be switched off.

### Changed

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
