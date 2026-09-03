# Guide d’utilisation de Joe

Joe relie une interface locale aux CLI Codex, Claude Code, Gemini, Copilot et
Cursor déjà installées et authentifiées sur la machine. Il ne demande ni ne
stocke leurs mots de passe.

Pour Cursor, la CLI attendue est `cursor-agent`, sans fenêtre : la commande
`cursor` lance l’éditeur et ne convient pas. Joe la pilote sur ses options
documentées et n’en lit pas le quota restant ; un quota épuisé reste détecté
au moment du run, et la demande bascule sur un autre fournisseur.

Le sélecteur de modèles n’est rempli que pour les CLI qui publient leur
catalogue : Codex l’expose, celui de Claude est tenu à jour dans Joe. Gemini et
Copilot n’offrent aucune liste et ne proposent donc que « auto », qui laisse la
CLI choisir ; le modèle voulu s’impose dans les réglages du projet.

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

### Routage hybride

Les commandes locales certaines restent déterministes et n’appellent aucun
modèle. Pour une demande naturelle suffisamment ambiguë, Joe peut demander à
un modèle léger déjà disponible de retourner une classification JSON bornée :
intention, complexité, workflow, fournisseur, niveau de modèle et effort.

Le classificateur est choisi selon les quotas connus, la disponibilité, les
échecs récents et sa latence observée. Il dispose de six secondes, ne tente
pas plusieurs fournisseurs en chaîne et revient au routeur local en cas
d’échec. Les niveaux `light`, `standard`, `strong` et `long-context` sont
ensuite traduits vers le catalogue réellement publié par chaque CLI.

La classification ne peut ni accorder une permission, ni diminuer une
intention d’écriture détectée localement, ni imposer un consensus pour une
tâche simple. Un agent, un modèle, un effort ou un workflow choisi
explicitement par l’utilisateur reste prioritaire. Le mécanisme peut être
désactivé avec `JOE_DISABLE_LLM_ROUTER=1`.

### Interface Web

Joe Web démarre par défaut avec le profil privé `maintainer`. Une instance
volontairement limitée se lance avec `joe web --profile viewer` ou
`joe web --profile operator`. Le navigateur local est authentifié
automatiquement lorsqu’il est ouvert par `joe` ou l’extension VS Code. Une URL
saisie directement ne reçoit jamais le secret : relance `joe` pour appairer un
nouveau navigateur. Le secret reste hors du projet et ne doit pas être copié
dans un dépôt.

Sous `--profile viewer` ou `--profile operator`, l’interface reste utilisable :
la lecture des projets est autorisée, mais les mutations de projet et
l’actualisation active des quotas répondent `403` avec un message explicite.

Le réglage « Accès Web », par projet ou par conversation, ne contraint que les
fournisseurs qui exposent un commutateur réseau — aujourd’hui Codex seul. Les
CLI Claude, Gemini et Copilot n’offrent aucune option équivalente : le décocher
ne leur retire pas l’accès réseau. L’interface affiche cette portée réelle sous
la case, à partir du champ `network_control_providers` de `GET /api/status`.

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

## Tâches et worktrees

Chaque run crée une tâche durable visible dans le panneau de suivi. Elle
conserve son état, son fournisseur, son modèle et le résumé de ses
modifications sans dupliquer la conversation.

Le panneau montre aussi le pipeline compact « Demande → Réalisation →
Validation → Diff → Livraison ». Une conversation affiche « En cours » pendant
le run, puis « Terminée » jusqu’à sa prochaine ouverture.

### Reprise automatique après un quota

L’option projet « Reprendre automatiquement après un reset de quota » est
activée par défaut. Si aucun fournisseur adapté ne dispose d’une réserve
suffisante et qu’une heure de reset est connue, la tâche passe en « En attente
du quota ». Joe conserve le prompt, les pièces jointes, le worktree et le nombre
de tentatives, puis réactualise les quotas à l’heure prévue.

Si la nouvelle fenêtre reste insuffisante, la tâche attend la suivante. Si un
quota est atteint au milieu d’un long travail, les changements déjà présents
dans le worktree sont conservés et la demande reprend en tenant compte de cet
état. Une attente peut être annulée avec le bouton d’interruption habituel.

Joe continue de choisir un autre fournisseur lorsqu’il peut terminer la tâche
sans sacrifier le workflow demandé. Une sélection explicite d’agent ou de mode
reste prioritaire et est tentée immédiatement.

Les autorisations en attente restent visibles dans ce même panneau après un
rechargement. Elles peuvent être acceptées ou refusées plus tard ; une
autorisation acceptée ne vaut que pour la demande enregistrée.

Lorsqu’un projet active « Isoler les modifications dans un worktree Git »,
Joe crée une branche `joe/<id>` et travaille hors du checkout principal. À la
fin du run :

- **Voir le diff** affiche les fichiers et le patch ;
- **Intégrer** committe les changements puis fusionne la branche, uniquement
  si le dépôt principal est propre ;
- **Supprimer** abandonne explicitement le worktree et sa branche.

Si la branche principale a avancé, Joe rebase d’abord la tâche. Un conflit
déclenche une résolution bornée par l’agent de la tâche, avec conservation des
deux objectifs et tests ciblés demandés. Si la résolution échoue ou laisse des
marqueurs, le rebase est annulé et le worktree reste disponible pour examen et
nouvelle tentative.

## Fichiers et recherche

Le bouton `+` ou un glisser-déposer sur le compositeur ajoute un fichier à la
bibliothèque du projet. Le menu **Outils** permet de le joindre à une demande,
de l’ouvrir ou de le supprimer. `@nom-du-fichier` joint également le fichier
correspondant. Joe affiche les pièces jointes réellement transmises avant
l’envoi.

La recherche de la colonne de gauche couvre les conversations, les tâches et
les noms de fichiers de tous les projets. Les résultats restent entièrement
locaux.

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

## Plans autonomes et quotas

Le bouton horloge du panneau d’activité ouvre les **Plans autonomes**. Il
permet de choisir une conversation, une heure de départ et une suite d’étapes
simples (une par ligne). Joe exécute une seule étape à la fois, conserve son
état après redémarrage et attend un reset de quota connu au lieu d’abandonner.

La section **IA réservée** est un réglage du projet. En mode automatique, Joe
équilibre les fournisseurs selon la tâche et les réserves connues. Si Claude,
Codex, Gemini ou Copilot est réservé, Joe en fait l’agent principal des
nouvelles demandes automatiques du projet et attend sa prochaine fenêtre
connue lorsqu’elle est insuffisante. Les workflows REVIEW et CONSENSUS peuvent
toujours appeler un autre agent pour la relecture. Un agent choisi explicitement
dans une conversation reste un choix ponctuel et prioritaire.

Un plan est volontairement borné à 24 étapes et 5 corrections par étape. Il
s’arrête sur conflit Git, permission manquante ou validation humaine requise.
L’accès complet n’est jamais accordé à un plan autonome. L’intégration des
worktrees peut être automatique, mais commit et push restent régis par les
réglages de livraison du projet.
