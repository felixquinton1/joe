# Joe — Plan d’action produit, extension et distribution

> Statut : en cours. Les phases 0 à 6 sont livrées localement. Les smokes
> multiplateformes, le pilote et la publication PyPI/Marketplace restent des
> gates distincts.

## 1. Objectif

Faire de Joe un produit local simple à installer et à maintenir, utilisable
depuis le Web, VS Code/Remote-SSH ou entièrement en CLI sur Linux, macOS et
Windows.

La distribution publique ne doit pas donner implicitement toutes les capacités
de l’instance privée. L’interface reste légère, rapide, compréhensible sans
manuel long et cohérente avec les conventions sobres de Codex, Claude et Gemini.

Restent fixes :

- un seul moteur Python et un seul stockage de conversations ;
- les CLI fournisseurs déjà installées et authentifiées par l’utilisateur ;
- une API HTTP/SSE locale, sans télémétrie vers l’éditeur ;
- Joe Web comme interface complète et solution de repli ;
- aucune lecture directe des fichiers de conversation par l’extension.

## 2. Sortie attendue

Le résultat cible comprend :

- `joe-orchestrator`, paquet Python installable en une commande ;
- `joe`, CLI single-shot et interactive ;
- Joe Web, utilisable au premier plan sur toute plateforme ;
- une extension VS Code installable en VSIX puis via Marketplace ;
- reprise des runs après rechargement ou coupure Remote-SSH ;
- aide intégrée courte, infobulles discrètes et zoom accessible ;
- capacités sensibles absentes ou sûres par défaut dans l’interface publique ;
- dépôt GitHub public, CI multiplateforme et artefacts de release vérifiés.

## 3. Données utilisées

L’extension dépend du [contrat API 1.x](api-contract.md) :

- statut et capacités : `/api/status`, `/api/capabilities` ;
- conversations : `/api/conversations` et `/{id}` ;
- runs : `/api/runs`, `/api/runs/active`, `/api/events/{id}` et annulation ;
- reconstruction : conversation puis `/api/history/{run_id}`.

Elle persiste uniquement l’identifiant de conversation, le run suivi, le
dernier `event_id` et au plus 100 000 caractères de flux pour reconstruire son
affichage. Les sources du paquet excluent `.agentflow/`, les conversations,
sessions, journaux, secrets et VSIX générés.

Les validations utilisent des faux serveurs et conversations dédiées. Les
smokes réels utilisent une instance locale ou distante isolée et n’attendent
aucun partage de données entre ces instances.

## 4. Méthode

```text
CLI ───────────────┐
Joe Web ───────────┼── Joe Python ── stockage local ── CLI fournisseurs
VS Code/Remote-SSH ┘      │
                         └── API HTTP/SSE 127.0.0.1
```

La logique métier reste dans Python. L’extension TypeScript demeure un client
fin et utilise d’abord les composants natifs VS Code. Une Webview riche ne sera
introduite que si le pilote démontre que l’Output Channel ne suffit plus.

Les restrictions d’interface sont des garde-fous, pas une sécurité
multi-utilisateur. Une restriction publique forte doit être imposée par l’API
avec un profil de capacités non extensible par le client.

La portabilité est vérifiée à deux niveaux : installation du paquet sur les
trois OS en CI, puis smokes fonctionnels avec au moins un fournisseur réel.
`tmux` reste une amélioration Unix facultative ; le serveur au premier plan est
la base portable.

## 5. Déroulé expérimental

### Phase 0 — Contrat HTTP/SSE — terminée

API 1.x, réservation atomique 202/409, curseur monotone, reprise SSE et tests de
contrat sont livrés.

### Phase 1 — Squelette VS Code — terminée pour Remote-SSH

Connexion côté workspace, statut, conversations, actualisation et redémarrage
tmux confirmé sont livrés. Le smoke Remote-SSH a été validé par l’utilisateur ;
le smoke local indépendant reste recommandé avant diffusion.

### Phase 2 — Client de conversation — retirée après pilote

La sélection, l’envoi et le suivi natifs VS Code fonctionnaient mais dupliquaient
Joe Web et rendaient le parcours confus. L’extension devient volontairement un
lanceur : démarrer, ouvrir, actualiser, redémarrer et arrêter. Le Web reste
l’unique interface conversationnelle.

### Phase 3 — Résilience — portée par Joe Web

Les runs appartiennent au serveur et Joe Web reconstruit leur état après
reconnexion. Fermer VS Code ne ferme ni tmux ni le navigateur déjà ouvert.
L’extension n’a plus sa propre copie du flux ou de l’historique.

### Phase 4 — UX légère et capacités prudentes — implémentée

Infobulles courtes, guide intégré et commandes de zoom natif sont livrés. Les
demandes VS Code imposent la lecture seule et les actions de maintenance sont
masquées par défaut. Workspace Trust bloque les mutations de l’extension.

Un run nécessitant historiquement `danger-full-access` est refusé par l’API
avec HTTP 428 tant que l’utilisateur n’a pas choisi « Autoriser une fois ». Le
Web et VS Code relaient ce gate de façon accessible. Après confirmation, Joe
autorise les modifications dans la racine du sous-projet concerné et ses seules
racines additionnelles déclarées. Il ne transmet jamais aux
fournisseurs les modes globaux `danger-full-access`, `bypassPermissions` ou
`yolo`, qui permettraient de sortir de ce périmètre. Le nom historique reste
accepté dans les réglages pour compatibilité.

Après pilote seulement : décider si un panneau de chat riche apporte assez de
valeur pour justifier sa CSP, son protocole de messages et sa maintenance.

### Phase 5 — Paquet et dépôt publics — prêt pour audit de publication

Le paquet contient métadonnées, scripts CLI, dépendance Windows conditionnelle
et arrêt de processus portable. La CI installe Joe sur Ubuntu, macOS et Windows.
Le workflow de tag construit et vérifie wheel/sdist puis les joint à une release
GitHub. Le guide documente `pipx`, CLI, Web, VSIX et Remote-SSH.

Avant ouverture : suite complète, build isolé, contrôle des secrets suivis et
historiques, revue croisée, commit. Après ouverture : tag/release vérifié.
PyPI reste un canal supplémentaire après configuration de Trusted Publishing.
Le changement de visibilité GitHub et le choix de licence sont des opérations
explicites : ils ne sont pas déclenchés automatiquement par cette phase.

### Phase 6 — Autorisation serveur — terminée

Les profils `viewer`, `operator` et `maintainer` sont imposés par une matrice
centrale. Un secret local hors Git authentifie cookie Web et clients Bearer.
Les mutations de projets, le rejet Git et tout accès projet complet exigent
`maintainer`; une élévation demandée par le client reçoit `403`. L’instance
privée démarre en `maintainer`, sans élargir les racines autorisées.

### Phase 7 — Pilote et canaux publics — à faire

Utiliser Web, CLI et VSIX une semaine, consigner les écarts, corriger seulement
les irritants récurrents, puis publier sur PyPI et éventuellement Marketplace.

## 6. Conditions de validation

| Gate | Condition d’acceptation | Preuve |
|---|---|---|
| Contrat | Chaque endpoint et cas SSE client possède un test | tests Python/TS |
| Parité | Même tour et mêmes permissions dans Web et VS Code | smoke consigné |
| Reprise | Aucun événement manquant/dupliqué après reload/SSH | test + rapport |
| Expiration | Réponse retrouvée ou état irrécupérable explicite | test TS |
| Sécurité | Lecture seule par défaut et HTTP 428 avant tout accès complet | manifest + tests |
| CLI | single-shot, pipe et session `joe cli` documentés | tests + guide |
| Portabilité | installation et `joe --help` sur 3 OS | CI |
| Paquet | wheel/sdist construits et `twine check` réussi | release |
| Données | aucun état utilisateur ou secret dans Git | audit avant ouverture |
| Régression | suites Python, TypeScript et JavaScript vertes | CI locale/distante |

## 7. Artefacts attendus

- ce plan, le contrat API et le [guide utilisateur](user-guide.md) ;
- paquet Python `joe-orchestrator` et commandes `joe`/`agent` ;
- extension versionnée et VSIX non commité ;
- tests de reprise et de portabilité ;
- workflows CI et release ;
- rapport de reconnexion Remote-SSH ;
- journal de pilote et matrice de capacités serveur.

## 8. Risques et garde-fous

| Risque | Garde-fou |
|---|---|
| Déconnexion tue le travail | fournisseur enfant du serveur, bookmark client |
| Flux perdu ou dupliqué | `event_id`, curseur et historique |
| Écriture publique involontaire | override lecture seule par défaut |
| Réglage client contourné | autorisation et profil imposés côté serveur |
| API exposée | bind local, aucune ouverture automatique |
| Windows sans `tmux` | serveur foreground et arrêt de processus portable |
| UI trop lourde | natif VS Code avant Webview |
| Données privées dans Git | exclusions + audit du contenu et de l’historique |
| Publication cassée | builds vérifiés avant tag, release automatisée |

Le retour arrière de l’extension consiste à la désinstaller : aucune migration
des conversations n’est nécessaire. Une release Python reste réinstallable par
version.

## 9. Décisions ouvertes

| Décision | Échéance | Proposition |
|---|---|---|
| Licence publique | avant redistribution large | droits réservés jusque-là |
| Webview riche | après pilote | rester natif si les besoins restent couverts |
| Profils/capacités | décidé | viewer/operator/maintainer |
| Auth locale | décidé | secret local `0600`, cookie HttpOnly ou Bearer |
| PyPI | après release GitHub | Trusted Publishing sans token long terme |
| Marketplace | après pilote | seulement si maintenance acceptable |
| Service détaché Windows/macOS | après pilote | ne pas bloquer le CLI/foreground |

## 10. Exécution

```bash
python -m pip install -e . pytest build twine
pytest -q
python -m build
python -m twine check dist/*
cd vscode-extension
npm ci
npm run check
npm test
npm run package
```

Ordre restant : validations locales, revue Claude, commit/push, ouverture GitHub,
tag et contrôle de la release ; puis smokes manuels des phases 2 et 3.
