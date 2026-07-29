# Joe VS Code — Plan d'action

> Statut : en cours, phase 0 terminée, phase 1 validée sous Remote-SSH et
> premier parcours interactif de phase 2 implémenté.
> Le registre unique, la réservation
> atomique par conversation, le curseur SSE et le contrat d’API 1.0 sont
> livrés. Le smoke test local reste recommandé avant diffusion ; le prochain
> gate actif est la parité manuelle d’un run entre VS Code distant et Joe Web.

## 1. Objectif

Créer une extension VS Code utilisable avec Remote-SSH comme deuxième interface
de Joe, sans interrompre l'interface web actuelle et sans lier la durée de vie
des runs au processus d'extension VS Code.

La variable introduite dans cette phase est uniquement le client VS Code. Restent
fixes :

- le serveur Python Joe lancé de façon détachée sous `tmux` ;
- l'API HTTP liée par défaut à `127.0.0.1:8765` ;
- les conversations, runs et sauvegardes gérés par Joe ;
- l'interface web, qui reste disponible comme solution de repli ;
- le comportement des agents, du routage et des permissions.

## 2. Sortie attendue

Une extension privée installable en `.vsix`, exécutée côté serveur dans une
fenêtre Remote-SSH, capable de :

- détecter et vérifier le serveur Joe local ;
- lister, créer et sélectionner des conversations ;
- afficher l'historique d'une conversation ;
- envoyer une requête avec les réglages de la conversation ;
- suivre les événements d'un run et l'annuler ;
- retrouver les runs actifs après rechargement ou reconnexion de VS Code ;
- signaler clairement un serveur absent ou incompatible ;
- cohabiter avec l'interface web sans altérer ses données.

Le MVP ne remplace ni `tmux`, ni le serveur HTTP, ni l'interface web. Il ne
démarre pas de processus fournisseur comme enfant de l'extension.

## 3. Données utilisées

Le client consomme uniquement les contrats Joe existants :

- `GET /api/status` pour l'identité du serveur, sa version et son projet ;
- `GET /api/conversations` et `GET /api/conversations/{id}` ;
- `POST /api/conversations` et `PATCH /api/conversations/{id}` ;
- `POST /api/runs` pour démarrer une requête ;
- `GET /api/runs/active` pour réconcilier l'état après reconnexion ;
- `GET /api/events/{run_id}` pour le flux SSE ;
- `GET /api/history` et `GET /api/history/{run_id}` pour retrouver un run
  terminé qui n'est plus conservé en mémoire ;
- `POST /api/runs/{run_id}/cancel` pour l'annulation.

L'extension ne lit et n'écrit jamais directement
`.agentflow/conversations.json`, `.agentflow/pending_runs.json` ou les sauvegardes
Joe. Elle ne stocke localement que des préférences d'interface non sensibles,
notamment la dernière conversation sélectionnée.

Le développement utilise des conversations dédiées `VS Code dev` pour isoler
les essais. Le serveur refuse déjà avec HTTP 409 un second run simultané dans
une même conversation, quel que soit le client.

## 4. Méthode

### Architecture partagée

```text
VS Code local
    └── Remote-SSH
          └── Extension Joe (extensionKind: workspace)
                    │
                    │ HTTP/SSE sur 127.0.0.1:8765
                    ▼
              Joe Python dans tmux
                 ├── conversations et runs persistants
                 ├── processus fournisseurs
                 └── interface web existante
```

L'extension est un client TypeScript léger. Son code d'extension porte l'accès
HTTP, la validation des réponses et la reconnexion. Une vue native suffit pour
la phase 1 ; le choix d'une Webview doit être arrêté avant la phase 2, qui doit
restituer des événements riches. Toute Webview ne reçoit pas d'URL arbitraire,
communique avec l'extension par messages typés et interdit les scripts inline
par une Content Security Policy avec nonce.

L'adresse par défaut est fixe : `http://127.0.0.1:8765`. Une adresse
configurable est hors MVP ; si elle est ajoutée ensuite, seuls `localhost`,
`127.0.0.1` et les sockets locaux seront acceptés par défaut.
Le diagnostic distingue un serveur absent d'un serveur Joe lancé sur un autre
port via l'option existante `--port`, sans scanner automatiquement le réseau.

La compatibilité se fonde d'abord sur un contrat de capacités explicite. Avant
le premier `.vsix`, `/api/status` devra exposer une version d'API indépendante
de la version du paquet. L'extension refusera les versions majeures
incompatibles et tolérera les ajouts de champs.

## 5. Déroulé expérimental

### Phase 0 — Figer le contrat et le banc de test — terminée

1. Documenter les requêtes, réponses, erreurs et événements SSE utilisés dans
   une matrice exhaustive pour le MVP.
2. Ajouter une version d'API et, si nécessaire, un identifiant stable du projet
   dans `/api/status`, de façon additive pour le client web existant.
3. Contractualiser le curseur SSE déjà livré : identifiant monotone par
   événement, `Last-Event-ID` et paramètre `after` utilisé par le client Web.
4. Écrire des tests Python de contrat pour les endpoints, codes HTTP et types
   d'événements du MVP, dont la reprise SSE.
5. Définir les trois chemins de réconciliation :
   actif vers SSE ; terminé encore en mémoire vers SSE rejoué ; terminé hors
   mémoire vers la conversation et `/api/history/{run_id}`.

Gate : aucune extension n'est développée tant que le contrat minimal et les cas
de reconnexion ne sont pas testables. La livraison inclut la suite Python
complète et les vérifications JavaScript du client web existant.

### Phase 1 — Squelette VS Code en lecture seule — validé sous Remote-SSH

1. Créer `vscode-extension/` avec TypeScript, lint, tests et packaging `.vsix`.
2. Déclarer explicitement `extensionKind: ["workspace"]`.
3. Ajouter une commande de diagnostic et un indicateur de connexion.
4. Afficher le projet, la version du serveur et les conversations.
5. Vérifier que le navigateur et VS Code montrent les mêmes données.
6. Permettre le redémarrage confirmé d’un serveur tmux, avec refus lorsqu’un
   run est actif et forçage explicite seulement si l’API ne répond plus.

Gate : aucune écriture Joe n'est encore autorisée depuis l'extension.

Le smoke distant a été validé par l’utilisateur. Le scénario local reste non
testé et compare nécessairement l’extension locale à Joe Web local, sans
attendre les conversations de l’instance distante.

### Phase 2 — Conversation et run de bout en bout — en cours

1. Créer et sélectionner une conversation de développement.
2. Envoyer un prompt et restituer le flux SSE dans VS Code.
3. Afficher les erreurs HTTP et les fins de flux sans faux succès.
4. Ajouter l'annulation et empêcher les doubles soumissions involontaires.
5. Conserver les réglages de permission portés par la conversation.
6. Si une Webview est retenue, valider sa CSP, ses nonces, l'échappement du
   contenu et la validation de tous les messages avant d'afficher du Markdown.

Implémenté dans le client natif :

- sélection persistée et création d’une conversation ;
- envoi minimal conservant les réglages portés par la conversation ;
- lecture progressive du SSE dans le canal de sortie Joe ;
- erreurs HTTP explicites, dont le conflit 409 ;
- blocage d’une double soumission dans une même fenêtre et annulation ;
- tests TypeScript du payload, du flux SSE et de l’annulation.

Gate : les mêmes actions produisent le même historique depuis les deux
interfaces, sans accès direct aux fichiers de stockage.

### Phase 3 — Résilience Remote-SSH

1. Lancer un run long depuis VS Code.
2. Fermer VS Code puis interrompre la connexion SSH du client.
3. Vérifier côté serveur que `tmux`, Joe et le fournisseur continuent.
4. Rouvrir Remote-SSH, interroger `/api/runs/active` et rattacher le flux.
5. Tester séparément le rechargement de fenêtre, le redémarrage de l'extension,
   le redémarrage de Joe et un run déjà terminé.
6. Pour le redémarrage de Joe, utiliser d'abord une tâche en lecture seule :
   une entrée de `pending_runs.json` provoque une relance complète du prompt,
   et non une reprise à l'instruction interrompue. Documenter ce comportement
   avant tout essai avec une tâche de modification potentiellement non idempotente.

Gate : aucune déconnexion du client ne tue le run ; l'état affiché après retour
est `actif`, `terminé`, `annulé` ou `irrécupérable`, jamais bloqué indéfiniment.

### Phase 4 — VSIX privé et usage parallèle

1. Produire un `.vsix` reproductible avec notice d'installation Remote-SSH.
2. Faire une semaine d'usage quotidien sur des conversations séparées.
3. Conserver le lancement actuel de Joe et l'interface web.
4. Classer les écarts fonctionnels avant tout élargissement du périmètre.

La décision `HTTP local` versus `socket Unix` vient seulement après ce retour
d'usage. Un passage à `systemd --user` est une évolution indépendante.

## 6. Conditions de validation

| Gate | Mesure | Condition d'acceptation | Preuve |
|---|---|---|---|
| Compatibilité | Matrice API MVP | Chaque endpoint, code attendu et type SSE de la matrice possède un test | Tests Python |
| Lecture seule | Parité des listes | Même projet et mêmes conversations | Test d'intégration |
| Envoi | Run complet | Prompt, flux et réponse présents une seule fois | Test extension + serveur |
| Permissions | Réglages transmis | Aucun élargissement implicite | Test de payload |
| Cohabitation | Intégrité | Une action depuis chaque UI, historique cohérent | Scénario manuel consigné |
| Veille/déconnexion | Survie | PID serveur et run toujours actifs | Script/scénario Remote-SSH |
| Reconnexion en mémoire | Réconciliation SSE | Aucun événement manquant ou dupliqué après reprise par curseur | Test d'intégration |
| Reconnexion hors mémoire | Reconstruction | Conversation et historique affichés en moins de 10 s, sans prétendre rejouer le flux | Test d'intégration |
| Annulation | Arrêt | Processus fournisseur arrêté et tour inachevé retiré | Test existant + extension |
| Sécurité | Exposition | Aucun bind non local ajouté par l'extension | Revue de configuration |
| Régression | Suite Joe | Suite Python complète verte | CI |
| Packaging | Installation | `.vsix` installable côté Remote-SSH vierge | Rapport smoke test |

## 7. Artefacts attendus

- ce plan et le [contrat versionné de l’API cliente](api-contract.md) ;
- `vscode-extension/` avec sources TypeScript, tests et README ;
- tests de contrat Python ciblés dans `tests/` ;
- fixture ou faux serveur pour les tests rapides de l'extension ;
- `.vsix` privé non commité, généré par une commande documentée ;
- rapport de test de déconnexion/reconnexion ;
- journal des écarts entre Web et VS Code ;
- notes de retour arrière et de compatibilité par version.

## 8. Risques et garde-fous

| Risque | Garde-fou |
|---|---|
| VS Code coupe son extension host | Aucun run fournisseur enfant de l'extension |
| Deux interfaces lancent la même conversation | Réservation serveur atomique et HTTP 409 ; conversations distinctes pendant le pilote |
| SSE duplique/perd des événements | Identifiants/curseur et réconciliation avec l'état serveur |
| Serveur redémarré pendant une déconnexion | `pending_runs.json` reste autorité côté Joe |
| Dérive entre API et extension | Version d'API + tests de contrat |
| Exposition réseau accidentelle | Boucle locale par défaut, aucune ouverture de port automatique |
| Webview vulnérable | CSP stricte, messages typés, HTML échappé |
| Processus local hostile | Documenter que l'API sans authentification est accessible aux autres processus du compte serveur |
| Régression de Joe Web | Phases additives, suite complète et repli navigateur |
| Corruption des conversations | API uniquement, sauvegardes Joe inchangées |
| Extension trop ambitieuse | MVP limité aux conversations, runs, flux et annulation |

Le retour arrière consiste à désinstaller/désactiver l'extension : aucune
migration des conversations ou des runs ne doit être nécessaire. Joe Web et
`joe kill` continuent de fonctionner comme avant.

## 9. Décisions ouvertes

Ne bloquent que la phase indiquée :

| Décision | Échéance | Proposition actuelle |
|---|---|---|
| UI native VS Code ou Webview | Avant phase 2 | Vue native pour la phase 1, Webview minimale si le rendu riche l'exige |
| Forme exacte du curseur SSE | Phase 0 | Identifiant monotone par événement |
| Démarrage de Joe depuis l'extension | Après MVP | Diagnostic et instruction, pas d'auto-start initial |
| Port configurable | Après MVP | Non, `127.0.0.1:8765` fixe |
| HTTP ou socket Unix | Après phase 4 | Conserver HTTP tant qu'aucun problème concret |
| `tmux` ou `systemd --user` | Projet séparé | Conserver `tmux` pour le pilote |
| Publication Marketplace | Après pilote privé | Hors périmètre actuel |

## 10. Exécution

Prérequis déjà livrés :

1. registre statique unique, sélecteur Web alimenté par le serveur ;
2. un seul run atomiquement réservé par conversation ;
3. reprise SSE après coupure, avec tests HTTP de rechargement, achèvement
   pendant la coupure et reconnexion après achèvement.

Ordre de livraison :

1. Livré : contrat API 1.0, version d'API et tests Python ;
2. Livré : squelette extension en lecture seule ; smoke Remote-SSH validé,
   smoke local restant avant diffusion ;
3. Livré en code, smoke à faire : envoi, flux et annulation ;
4. PR/commit D : reconnexion et tests Remote-SSH ;
5. PR/commit E : documentation et packaging privé.

Chaque livraison doit conserver une suite Python verte, ajouter ses tests
TypeScript ciblés, incrémenter la version de Joe lorsqu'elle modifie le paquet,
et passer une revue Claude pour tout changement important avant commit et push.
