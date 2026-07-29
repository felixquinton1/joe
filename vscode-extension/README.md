# Joe for VS Code

Première interface VS Code de Joe, volontairement limitée à la lecture.

## Prérequis

- ouvrir le projet avec VS Code ou Remote-SSH ;
- installer l’extension sur l’hôte du workspace ;
- lancer Joe sur ce même hôte avec `joe` ;
- conserver l’adresse par défaut `http://127.0.0.1:8765`.

La vue Joe affiche la version du serveur, le projet et les conversations. Le
bouton de redémarrage relance uniquement le serveur tmux du port courant, après
confirmation, attend que l’API réponde réellement, ouvre Joe Web et refuse
l’opération lorsqu’un run est actif. En Remote-SSH, VS Code résout l’URL externe
et son éventuel port forwarding. Sans tmux, Joe demande un redémarrage manuel.
L’extension ne peut encore ni créer une
conversation, ni envoyer ou annuler un run.

## Développement

```bash
npm ci
npm run check
npm test
npm run package
```

Le `.vsix` produit reste local et n’est pas commité.
