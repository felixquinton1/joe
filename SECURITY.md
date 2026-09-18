# Politique de sécurité

## Signaler une vulnérabilité

Merci de **ne pas ouvrir d'issue publique** pour une faille de sécurité.

Utilisez l'onglet *Security* du dépôt GitHub, « Report a vulnerability », qui
ouvre un canal privé. Décrivez le défaut, la version concernée (`joe --version`)
et, si possible, la manière de le reproduire.

Comptez une première réponse sous une semaine. Ce projet est maintenu sur du
temps disponible : ce délai est un usage, pas un engagement contractuel.

## Versions suivies

Seule la dernière version publiée reçoit des correctifs.

## Ce que Joe fait, et ce que cela implique

Joe est un orchestrateur local. Le comprendre évite de signaler comme faille un
comportement voulu, et aide à reconnaître ce qui en est vraiment une.

- **Joe lance les CLI de fournisseurs déjà installées et authentifiées** sur la
  machine. Il ne demande, ne stocke ni ne transmet leurs identifiants. Les
  jetons de ces outils restent là où eux les rangent.
- **Joe leur accorde le niveau d'accès choisi pour le projet**, jusqu'à
  l'exécution de commandes et la modification de fichiers dans la racine du
  projet et ses racines additionnelles. C'est la fonction même de l'outil : un
  agent qui modifie des fichiers avec l'accès accordé n'est pas une faille.
- **L'interface web n'écoute que sur la boucle locale**, sauf `--allow-remote`
  explicite, et l'authentification reste obligatoire dans ce cas.
- **Les campagnes autonomes enchaînent des appels de modèles et des commandes
  sans intervention.** Elles sont expérimentales et bornées par des budgets que
  l'utilisateur fixe.

Sont en revanche des vulnérabilités, et nous voulons les connaître :

- une commande exécutée hors du périmètre accordé au projet ;
- un moyen d'obtenir le jeton d'authentification, ou de s'en passer ;
- un accès aux données d'un projet depuis un autre ;
- une page web tierce capable d'agir sur l'instance locale de l'utilisateur ;
- la divulgation, par une route non authentifiée, d'informations sur la machine.

## Limites connues

Ces points sont documentés plutôt que corrigés, et ne sont pas à signaler :

- **L'en-tête `Host` n'est pas validé.** Le serveur reste donc atteignable par
  un nom qui résout vers la boucle locale. Les routes sensibles exigent un
  jeton, que le cookie `SameSite=Strict` ne divulgue pas à une autre origine.
- **`/api/status` répond sans authentification.** L'interface en a besoin pour
  se rendre et signaler un serveur périmé. Elle n'expose que la version, les
  fournisseurs déclarés et les modes ; le chemin du projet et le profil ne sont
  joints qu'à un appelant identifié.
