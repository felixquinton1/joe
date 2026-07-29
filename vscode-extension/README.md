# Joe for VS Code

L’extension est un lanceur léger pour Joe Web.

## Prérequis

- ouvrir le projet avec VS Code ou Remote-SSH ;
- installer l’extension sur l’hôte du workspace ;
- installer la commande `joe` sur ce même hôte ;
- conserver l’adresse par défaut `http://127.0.0.1:8765`.

## Boutons

- `▶` démarre Joe pour le workspace courant ;
- `🌐` ouvre Joe Web avec le port forwarding Remote-SSH automatique ;
- `↻` actualise l’état ;
- `⟳` redémarre l’instance du port courant ;
- `■` arrête l’instance du port courant ;
- `?` ouvre ce guide.

Les conversations, prompts, modèles, permissions et résultats restent dans Joe
Web. Il n’existe donc pas deux interfaces de conversation à maintenir.

Le démarrage, le redémarrage et l’arrêt automatiques utilisent actuellement
`tmux`. Sans tmux, lance `joe web` dans un terminal puis utilise le bouton
« Ouvrir l’interface Web ».

## Développement

```bash
npm ci
npm run check
npm test
npm run package
```

Le `.vsix` produit reste local et n’est pas commité.
