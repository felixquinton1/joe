# Joe Web — Validation de reconnexion

> Statut : terminé pour le contrat HTTP local de Joe 0.21.12. Les essais
> Remote-SSH et mise en veille physique restent dans la phase VS Code.

## 1. Objectif

Vérifier qu'une perte du client Web ne perd pas le run serveur et qu'une
reconnexion ne rejoue pas les événements déjà reçus.

## 2. Sortie attendue

Un flux SSE avec identifiants monotones, reprise par curseur et reconstruction
possible depuis le run actif ou la conversation persistée.

## 3. Données utilisées

Des conversations et runs temporaires créés par `tests/test_web.py`, sans
lecture ni modification des conversations utilisateur.

## 4. Méthode

Les scénarios utilisent un vrai `ThreadingHTTPServer` lié à un port local
éphémère et des connexions `http.client` distinctes. Le fournisseur est simulé
afin de contrôler précisément la coupure et l'achèvement.

## 5. Déroulé expérimental

1. Ouvrir le flux d'un run actif et recevoir l'événement 1.
2. Fermer réellement la connexion HTTP cliente.
3. Terminer le run côté serveur avec l'événement 2.
4. Ouvrir une nouvelle connexion avec le curseur 1.
5. Vérifier que seul l'événement 2 est renvoyé.
6. Vérifier séparément que `/api/runs/active` permet un rattachement après
   rechargement et qu'un run terminé reste disponible pendant sa rétention.

## 6. Conditions de validation

| Gate | Mesure | Condition d'acceptation |
|---|---|---|
| Survie | Run serveur | La fermeture du premier client ne supprime pas le run |
| Reprise | Identifiants SSE | La seconde connexion commence à l'identifiant 2 |
| Unicité | Corps rejoué | L'identifiant 1 est absent de la reprise |
| Achèvement | Résultat | L'événement final est disponible après reconnexion |
| Régression | Suite | Suite Python et contrôle syntaxique JavaScript verts |

## 7. Artefacts attendus

- test d'intégration SSE dans `tests/test_web.py` ;
- logique de curseur dans `web_server.py` et `app.js` ;
- ce rapport de validation.

## 8. Risques et garde-fous

Le test valide le protocole et une vraie coupure de socket locale, pas une mise
en veille de poste ni une rupture Remote-SSH. Ces essais nécessitent le futur
client VS Code et restent explicitement hors de cette validation.

## 9. Décisions ouvertes

Aucune pour Joe Web. La version indépendante de l'API reste le prochain gate
avant de créer le squelette VS Code.
