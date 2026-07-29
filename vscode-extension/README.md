# Joe for VS Code

Interface VS Code de Joe pour consulter les conversations et lancer une demande.

## Prérequis

- ouvrir le projet avec VS Code ou Remote-SSH ;
- installer l’extension sur l’hôte du workspace ;
- lancer Joe sur ce même hôte avec `joe` ;
- conserver l’adresse par défaut `http://127.0.0.1:8765`.

La vue Joe affiche la version du serveur, le projet et les conversations.
Cliquer sur une conversation la sélectionne. Les actions de la barre de vue
permettent de créer une conversation, d’envoyer une demande, de suivre sa
réponse dans le canal de sortie `Joe` et de demander son annulation. Les
réglages de fournisseur, mode et permissions enregistrés dans la conversation
restent l’autorité : l’extension ne les remplace pas dans son envoi.

Le
bouton de redémarrage relance uniquement le serveur tmux du port courant, après
confirmation, attend que l’API réponde réellement, ouvre Joe Web et refuse
l’opération lorsqu’un run est actif. En Remote-SSH, VS Code résout l’URL externe
et son éventuel port forwarding. Sans tmux, Joe demande un redémarrage manuel.
Une seule demande peut être suivie à la fois par fenêtre VS Code. Joe refuse
également côté serveur les doubles soumissions sur une même conversation.

Après un rechargement de fenêtre ou une reconnexion Remote-SSH, l’extension
retrouve le run mémorisé, réaffiche le texte déjà reçu et reprend le SSE après
le dernier événement. Si le flux a expiré côté serveur, elle recherche la
réponse finale dans l’historique de la conversation.

## Sécurité et aide

Les demandes partent en lecture seule par défaut. Le réglage
`Joe: Allow Workspace Writes` doit être activé explicitement pour conserver les
permissions d’écriture de la conversation. Le réglage
`Joe: Enable Maintenance Actions` révèle le redémarrage tmux. Ces options
réduisent les actions accidentelles, mais seule une future autorisation côté
serveur pourra constituer une frontière de sécurité multi-utilisateur.

Le survol des éléments fournit des explications courtes. La commande
`Joe: Ouvrir le guide` ouvre ce document. Le zoom natif de VS Code fonctionne
avec `Ctrl/Cmd + +`, `Ctrl/Cmd + -` et `Ctrl/Cmd + 0`, ou avec les commandes
Joe équivalentes dans la palette.

## Développement

```bash
npm ci
npm run check
npm test
npm run package
```

Le `.vsix` produit reste local et n’est pas commité.
