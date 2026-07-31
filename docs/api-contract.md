# Joe HTTP/SSE API — MVP contract

> Statut : validé. Contrat client courant : `api_version: "1.2"`.

## Portée et compatibilité

Ce contrat couvre l’interface nécessaire au client Web et au futur client
VS Code. Il ne constitue pas une API distante ou multi-utilisateur.

- Transport : HTTP/1.1 sur `127.0.0.1:8765` par défaut.
- Corps structurés : JSON UTF-8.
- Événements : Server-Sent Events (SSE).
- Taille maximale d’un corps JSON : 1 Mio.
- `version` identifie le paquet Joe.
- `api_version` identifie ce contrat indépendamment du paquet.
- Un client accepte les ajouts de champs avec la même version majeure.
- Une suppression, un renommage ou un changement de sémantique exige une
  nouvelle version majeure d’API.

Joe 1.1 authentifie l’API locale et impose un profil de capacités. Un bind non
local reste refusé sans option explicite.

Joe 1.2 resserre deux points, sans ajout de route :

- `full_access_approved` dans le corps de `POST /api/runs` **n’autorise plus
  rien**. Un accès complet exige un `approval_id` durable au statut `approved`,
  consommé une seule fois. Le champ est ignoré s’il est encore envoyé : un
  client qui s’en servait reçoit désormais `428` avec l’`approval_id` à faire
  valider. Aucun client 1.x publié ne dépendait de ce champ.
- Le téléchargement et la suppression d’une pièce jointe sont cloisonnés par
  projet et attendent `?project={id}`. Un identifiant seul ne suffit plus à
  sortir un fichier de son projet : la réponse est `404` en cas de
  discordance.

## Matrice des endpoints MVP

| Méthode | Endpoint | Succès | Erreurs utiles | Usage client |
|---|---|---:|---:|---|
| GET | `/api/status` | 200 | — | identité, compatibilité, projet, fournisseurs |
| POST | `/api/pair` | 200 | 401 | échange du fragment contre un cookie |
| POST | `/api/auth/rotate` | 200 | 401, 403 | révocation et rotation du secret |
| GET | `/api/capabilities` | 200 | — | modèles, efforts et modes d’exécution |
| GET | `/api/usage` | 200 | — | quotas mis en cache |
| GET | `/api/usage?force=1` | 200 | — | actualisation explicite des quotas |
| GET | `/api/projects` | 200 | — | liste des projets |
| GET | `/api/projects/{id}` | 200 | 404 | détail d’un projet |
| POST | `/api/projects` | 201 | 400, 413 | création d’un projet |
| PATCH | `/api/projects/{id}` | 200 | 400, 404, 411, 413 | modification d’un projet |
| GET | `/api/conversations` | 200 | — | liste des conversations |
| GET | `/api/conversations/{id}` | 200 | 404 | historique et réglages |
| POST | `/api/conversations` | 201 | 400, 413 | création d’une conversation |
| PATCH | `/api/conversations/{id}` | 200 | 400, 404, 411, 413 | réglages et renommage |
| DELETE | `/api/conversations/{id}` | 200 | 404, 409 | suppression |
| POST | `/api/runs` | 202 | 400, 409, 411, 413 | lancement d’un run |
| GET | `/api/runs/active` | 200 | — | réconciliation après reconnexion |
| GET | `/api/events/{run_id}` | 200 | 404 | flux SSE d’un run en mémoire |
| POST | `/api/runs/{run_id}/cancel` | 202 | 404 | annulation |
| GET | `/api/history` | 200 | — | liste des journaux persistants |
| GET | `/api/history/{run_id}` | 200 | 404 | journal persistant complet |
| GET | `/api/projects/{id}/skills` | 200 | 404 | skills du projet (avec indicateur `active` et `scope`) |
| POST | `/api/projects/{id}/skills/import` | 201 | 400, 404 | import d’un skill partagé par tous les fournisseurs |
| POST | `/api/projects/{id}/skills/promote` | 201 | 400, 404 | promotion d’un skill du projet en skill commun |
| GET | `/api/skills/global` | 200 | — | skills communs à tous les projets |

Les endpoints de revue Git (`reject`) restent utilisés par le client Web mais
ne font pas partie du premier client VS Code en lecture seule.

### Tâches durables

| Méthode | Route | Rôle | Résultat |
|---|---|---|---|
| `GET` | `/api/tasks` | viewer | Tâches récentes |
| `GET` | `/api/tasks/{id}/diff` | viewer | Diff du worktree |
| `POST` | `/api/tasks/{id}/integrate` | maintainer | Intégration asynchrone acceptée (`202`) |
| `DELETE` | `/api/tasks/{id}` | maintainer | Tâche et worktree supprimés |

## Schémas minimaux

### Statut

`GET /api/status` renvoie au minimum :

```json
{
  "version": "0.x.y",
  "api_version": "1.2",
  "project": "/chemin/du/projet",
  "providers": ["codex", "claude", "gemini", "copilot"],
  "provider_catalog": [{"id": "codex", "label": "Codex"}],
  "modes": ["fast", "review", "consensus"],
  "network_control_providers": ["codex"]
}
```

Le client vérifie la version majeure de `api_version` avant toute mutation.

`network_control_providers` énumère les fournisseurs dont la commande varie
réellement selon le réglage « Accès Web » du projet ou de la conversation. Les
autres CLI n’exposent aucun commutateur d’egress : le réglage ne les contraint
pas, et un client doit le dire à l’utilisateur plutôt que de présenter une
garantie globale.

## Authentification et rôles

`GET /api/status` et les fichiers statiques restent publics pour le diagnostic
et l’ouverture de Joe Web, mais ne distribuent aucun secret. La CLI ouvre une
URL dont le fragment contient le jeton; la page l’échange une fois via
`POST /api/pair` contre un cookie `HttpOnly`, `SameSite=Strict` valable 30
jours, puis efface le fragment de l’historique. Les autres endpoints exigent ce cookie ou
`Authorization: Bearer <jeton>`.

Le jeton est généré dans le répertoire de données utilisateur avec des
permissions `0600`; il n’est jamais placé dans le projet ou dans Git.

| Profil | Capacités |
|---|---|
| `viewer` | quotas en cache, conversations, historique, événements, **lecture** des projets |
| `operator` | capacités `viewer`, conversations et runs ordinaires |
| `maintainer` | capacités `operator`, mutation des projets, rejet Git et accès projet complet |

Une requête non authentifiée reçoit `401`; un jeton valide mais insuffisant
reçoit `403`. La confirmation ponctuelle `428` d’un accès complet reste requise
pour un `maintainer`.

`GET /api/projects` est accessible dès `viewer` : Joe Web en a besoin pour se
rendre, et le restreindre rendait l’interface inutilisable sous
`--profile viewer` ou `--profile operator`. Toute **mutation** de projet reste
réservée à `maintainer`.

L’actualisation active des quotas (`GET /api/usage?force=1`) exige
`maintainer`, parce qu’elle lance un sous-processus fournisseur.

Un `viewer` peut écrire le seul champ `unread_completion` d’une conversation :
acquitter un état lu n’est pas une mutation de contenu. Tout autre champ d’un
`PATCH /api/conversations/{id}` exige `operator` et répond `403` sinon.

`POST /api/auth/rotate`, réservé à `maintainer`, remplace atomiquement le secret
du serveur et révoque immédiatement cookies et Bearers antérieurs.

### Lancement

`POST /api/runs` accepte :

```json
{
  "request": "Demande utilisateur",
  "conversation_id": "identifiant",
  "agent": "codex",
  "mode": "review",
  "model": "gpt-5.6-sol",
  "effort": "high",
  "execution_mode": "workspace-write"
}
```

Seuls `request` et `conversation_id` sont obligatoires. Les valeurs absentes
utilisent le routage et les réglages de conversation. Le succès renvoie
`{"run_id": "…"}` avec HTTP 202.

Le mode historique `danger-full-access` demande une confirmation HTTP 428, mais
ne désactive pas le sandbox du fournisseur. Une fois confirmé, il donne un
accès complet limité à la racine du projet de la conversation et aux racines
additionnelles explicitement configurées pour ce même projet.

La confirmation passe obligatoirement par une approbation durable. Le `428`
renvoie `{"approval": "full-access", "approval_id": "…"}` ; le client fait
valider cette approbation (`PATCH /api/approvals/{id}` avec
`{"decision": "approved"}`, réservé à `maintainer`), puis relance le même
`request` en joignant `approval_id`. L’approbation est alors consommée et ne
peut pas être rejouée : un second lancement identique produit un nouveau `428`.
Aucun champ du corps de la requête ne peut se substituer à ce cycle.

Deux lancements simultanés pour une même conversation produisent exactement un
HTTP 202 et un HTTP 409. Le rejet ne persiste aucun second message utilisateur.

## Erreurs

Les erreurs de validation JSON renvoient un objet `{"error": "…"}` :

- 400 : JSON invalide, valeur ou identifiant invalide ;
- 409 : conversation occupée ou opération incompatible avec l’état courant ;
- 411 : en-tête `Content-Length` absent quand un corps est requis ;
- 413 : corps supérieur à 1 Mio.

Certains endpoints historiques renvoient encore `null` ou un booléen à 404.
Le client doit donc décider d’abord avec le code HTTP et ne jamais déduire un
succès de la seule forme du corps. Une uniformisation future des corps 404 sera
additive dans la version 1.x si les champs existants sont conservés.

## Contrat SSE

`GET /api/events/{run_id}` renvoie `text/event-stream`.

Chaque événement contient :

```text
id: 2
data: {"event_id":2,"at":1785310000.0,"type":"complete",...}
```

Garanties :

- `event_id` commence à 1 et croît de façon monotone pour un run ;
- l’identifiant SSE et `data.event_id` sont identiques ;
- `Last-Event-ID: N` reprend strictement après `N` ;
- `?after=N` fournit la même sémantique pour les clients ne contrôlant pas
  l’en-tête SSE ;
- les keepalives sont des commentaires `: keepalive` et ne modifient pas le
  curseur ;
- le flux se ferme après le dernier événement d’un run terminé ;
- un run absent de la mémoire renvoie 404.

## Réconciliation

Après une coupure, le client suit cet ordre :

1. appeler `/api/runs/active` ;
2. si le run est actif, reprendre le SSE après le dernier `event_id` reçu ;
3. si le run est terminé mais encore en mémoire, reprendre le SSE de la même
   manière jusqu’à fermeture ;
4. s’il n’est plus en mémoire, reconstruire l’affichage depuis la conversation
   puis `/api/history/{run_id}` ;
5. ne jamais relancer automatiquement une mutation pour recréer un flux perdu.

## Preuves de contrat

Les tests dédiés dans `tests/test_api_contract.py` verrouillent :

- identité et version indépendante ;
- erreurs JSON et taille maximale ;
- réservation atomique avec HTTP 409 ;
- identité des curseurs `Last-Event-ID` et `after`.

Les tests fonctionnels plus détaillés restent dans `tests/test_web.py`.

## Automatisations (API 1.3)

- `GET /api/automations` liste les plans séquentiels persistants ;
- `POST /api/automations` crée un plan avec `conversation_id`, `steps`,
  `scheduled_for`, `mode`, `execution_mode`, `max_retries` et
  `auto_integrate` ;
- `POST /api/automations/{id}/cancel` annule le plan et son run actif.

Les mutations exigent le profil `maintainer`. `execution_mode` est limité à
`read-only` et `workspace-write` : un plan autonome ne peut pas approuver son
propre accès complet.
