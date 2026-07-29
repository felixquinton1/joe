# Joe HTTP/SSE API — MVP contract

> Statut : validé. Contrat client introduit avec `api_version: "1.0"`.

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

Joe 1.0 ne fournit ni authentification HTTP, ni isolation entre plusieurs
utilisateurs du même compte système. Un bind non local reste refusé sans
option explicite.

## Matrice des endpoints MVP

| Méthode | Endpoint | Succès | Erreurs utiles | Usage client |
|---|---|---:|---:|---|
| GET | `/api/status` | 200 | — | identité, compatibilité, projet, fournisseurs |
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

Les endpoints de revue Git (`reject`) restent utilisés par le client Web mais
ne font pas partie du premier client VS Code en lecture seule.

## Schémas minimaux

### Statut

`GET /api/status` renvoie au minimum :

```json
{
  "version": "0.21.16",
  "api_version": "1.0",
  "project": "/chemin/du/projet",
  "providers": ["codex", "claude", "gemini", "copilot"],
  "provider_catalog": [{"id": "codex", "label": "Codex"}],
  "modes": ["fast", "review", "consensus"]
}
```

Le client vérifie la version majeure de `api_version` avant toute mutation.

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
