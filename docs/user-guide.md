# Guide d’utilisation de Joe

Joe relie une interface locale aux CLI Codex, Claude Code, Gemini et Copilot
déjà installées et authentifiées sur la machine. Il ne demande ni ne stocke
leurs mots de passe.

## Installation

Joe nécessite Python 3.10 ou plus. `pipx` est recommandé, car il isole les
dépendances tout en rendant la commande `joe` disponible partout.

Depuis le dépôt GitHub :

```bash
pipx install git+https://github.com/felixquinton1/joe.git
```

Pour développer Joe :

```bash
git clone https://github.com/felixquinton1/joe.git
cd joe
python -m pip install -e .
```

Le paquet Python est indépendant de la plateforme. Linux, macOS et Windows
utilisent les mêmes commandes. `tmux` améliore le fonctionnement détaché sous
Linux et macOS ; sans `tmux`, notamment sous Windows, `joe web` reste au premier
plan et se ferme avec son terminal.

Vérifier l’installation :

```bash
joe --version
joe doctor
```

Les CLI des agents restent des prérequis séparés. `joe doctor --live` les
contacte réellement et peut consommer un petit quota ; la commande simple
`joe doctor` ne lance pas de demande.

## Trois façons d’utiliser Joe

### Terminal

Une demande unique :

```bash
joe "Analyse ce projet sans modifier les fichiers"
joe --agent claude --mode review "Vérifie ce changement"
printf "Résume ce dépôt" | joe -C /chemin/du/projet
```

Une session entièrement en ligne de commande :

```bash
joe cli
```

`joe chat` est un alias conservé. `/exit` quitte la session. `--dry-run`
affiche le routage choisi sans appeler d’agent.

### Interface Web

Joe Web démarre par défaut avec le profil privé `maintainer`. Une instance
volontairement limitée se lance avec `joe web --profile viewer` ou
`joe web --profile operator`. Le navigateur local est authentifié
automatiquement lorsqu’il est ouvert par `joe` ou l’extension VS Code. Une URL
saisie directement ne reçoit jamais le secret : relance `joe` pour appairer un
nouveau navigateur. Le secret reste hors du projet et ne doit pas être copié
dans un dépôt.

Pour réappairer un navigateur après suppression des cookies :

```bash
joe url
```

`joe url --print` affiche le lien sensible uniquement lorsqu’il faut le
transmettre manuellement à un navigateur sur la même machine. En cas de doute
sur une fuite locale, `joe auth rotate` renouvelle immédiatement le secret,
révoque les anciennes sessions et ouvre un nouvel appairage.

Le profil limite les actions acceptées par une instance donnée. Les instances
du même compte système partagent toutefois le même secret : `--profile viewer`
réduit les risques de fausse manœuvre, mais ne constitue pas une délégation à
un autre utilisateur.

Depuis le projet à traiter :

```bash
joe web
```

Joe écoute uniquement sur `127.0.0.1:8765` par défaut. Sous Linux ou macOS,
`joe` ouvre directement cette interface et utilise `tmux` lorsqu’il est
disponible. Pour garder le serveur dans le terminal :

```bash
joe web --foreground
```

### VS Code et Remote-SSH

Installer le fichier `.vsix` dans la fenêtre VS Code qui contient le workspace.
Sous Remote-SSH, il faut choisir « Install in SSH » : l’extension et
`127.0.0.1:8765` se trouvent alors sur l’hôte distant.

La vue Joe sert de lanceur pour l’interface Web : démarrer, ouvrir, actualiser,
redémarrer et arrêter l’instance du port courant. En Remote-SSH, « Ouvrir »
utilise automatiquement le port forwarding de VS Code. Les conversations,
prompts et résultats restent exclusivement dans Joe Web.

Le zoom natif de VS Code s’applique à la vue Joe :

- Windows/Linux : `Ctrl++`, `Ctrl+-`, `Ctrl+0` ;
- macOS : `Cmd++`, `Cmd+-`, `Cmd+0` ;
- palette : `Joe: Zoomer`, `Joe: Dézoomer`, `Joe: Réinitialiser le zoom`.

## Capacités et sécurité

L’extension publique adopte des valeurs prudentes :

- `Joe: Allow Workspace Writes` est désactivé : les demandes envoyées depuis
  VS Code imposent la lecture seule ;
- `Joe: Enable Maintenance Actions` est désactivé : le redémarrage du serveur
  est masqué et refusé ;
- les workspaces non approuvés ne peuvent ni créer une conversation ni envoyer
  une demande.

Ces réglages évitent les actions accidentelles, mais ne constituent pas une
autorisation forte : un utilisateur ayant accès au même compte système et à
l’API locale peut les contourner. Les profils publics réellement restreints
devront être appliqués par le serveur Joe.

Lorsqu’un projet ou une conversation demande `danger-full-access`, Joe suspend
le lancement et affiche une confirmation « Autoriser une fois ». Sans cette
confirmation, aucun run n’est créé et aucun message n’est ajouté à
l’historique. Après validation, Codex, Claude ou Gemini reçoit son mode complet
réel pour ce run seulement. Cette confirmation au niveau du run fonctionne dans
Joe Web et VS Code ; les demandes commande par commande propres à chaque CLI
restent un chantier ultérieur.

Joe ne doit pas être exposé directement sur Internet. Le bind non local est
refusé sans `--allow-remote`, et cette option est réservée à un environnement
réseau déjà protégé.

## Commandes utiles

```bash
joe --help
joe doctor [-C /projet]
joe web --foreground [-C /projet]
joe restart [-C /projet]
joe stop [--port 8765]
joe kill
joe sync
```

`restart` et `kill` automatisent uniquement les serveurs gérés par `tmux`.
Fermer une interface cliente n’efface pas les conversations.

## Dépannage rapide

- « Serveur inaccessible » : lancer `joe web` sur la même machine que
  l’extension.
- Remote-SSH affiche le mauvais projet : vérifier que l’extension est installée
  côté SSH et que Joe a été lancé sur cet hôte.
- Les conversations ne sont pas listées dans VS Code : c’est volontaire,
  utilise « Joe: Ouvrir l’interface Web ».
- Port occupé : choisir un autre `--port` et reporter la même URL dans
  `Joe: Server Url`.
- Agent absent : installer/authentifier sa CLI, puis relancer `joe doctor`.

Les conversations et journaux appartiennent au projet ciblé, jamais au paquet
Python ni à l’extension.
