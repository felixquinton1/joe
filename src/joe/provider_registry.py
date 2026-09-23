"""Declarative description of every provider Joe can drive.

Chaque comportement propre à un fournisseur était auparavant re-testé par nom
dans `providers.py` et réécrit littéralement dans cinq autres modules. Le signe
le plus net : `watchdog_seconds` existait comme donnée, mais son activation
restait un `if provider == "gemini"`. Ici, un comportement se déclare une fois
et se lit partout.

Ajouter un fournisseur = une entrée dans `PROVIDERS` et une fonction d'argv
dans `providers.py`. L'absence de l'une ou de l'autre échoue à l'import, pas au
premier run.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    label: str

    streams_json: bool = False
    """La CLI émet des événements JSON sur stdout plutôt que du texte brut.

    Remplace les listes blanches `{"codex", "claude", "gemini"}` recopiées dans
    `_activity`, `_collect_streams` et `_final_output` : un fournisseur absent
    de ces listes renvoyait silencieusement du stdout brut au lieu d'événements
    analysés.
    """

    watchdog_seconds: float | None = None
    """Délai d'inactivité toléré. `None` = pas de chien de garde.

    Unique commutateur : la présence d'une valeur suffit à armer le watchdog.
    """

    terminal_quota_markers: tuple[str, ...] = ()
    """Marqueurs prouvant un quota définitivement épuisé, même en sortie 0."""

    reviewer_peers: tuple[str, ...] = ()
    """Fournisseurs pouvant relire ce fournisseur, par ordre de préférence.

    La règle « choisis le complémentaire » était écrite cinq fois dans cinq
    modules, ce qui empêchait structurellement Gemini et Copilot d'être
    relecteurs.
    """

    fallbacks: tuple[str, ...] = ()
    """Ordre de repli quand ce fournisseur échoue."""

    minimum_version: str = ""
    """Version minimale connue pour fonctionner."""

    exposes_usage: bool = False
    """La CLI expose une mesure de quota exploitable."""

    arbitration_priority: int = 3
    """Préférence pour arbitrer un consensus. Plus bas = préféré.

    Le rôle était tenu par un nom écrit dans le workflow. Chez qui n'avait pas
    ce fournisseur, la synthèse partait quand même vers lui, échouait, puis se
    rabattait en annonçant une dégradation qui n'en était pas une.

    Arbitrer demande surtout de ne pas être déjà juge et partie : la priorité
    départage, elle n'exclut personne.
    """

    executables: tuple[str, ...] = ()
    """Noms cherchés sur le PATH, par ordre de préférence. Vide = `name`.

    Une CLI peut être renommée sans que le fournisseur change de nom chez Joe :
    ce nom-là est écrit dans les conversations et les réglages. Cursor l'a fait
    — `cursor-agent` est devenu `agent` — et tout ce qui cherchait le seul
    ancien nom a cessé de la voir.
    """

    identity_marker: str = ""
    """Mot attendu dans `--version` d'un exécutable au nom générique.

    Un alias comme `agent` n'appartient à personne : sur une machine ordinaire
    il peut désigner tout autre chose. Sans preuve d'identité, Joe adopterait
    ce binaire et enverrait ses runs à un inconnu. La vérification ne porte que
    sur les noms qui ne sont pas celui du fournisseur.
    """

    install_posix: str = ""
    """Commande d'installation documentée sur macOS et Linux."""

    install_windows: str = ""
    """Commande d'installation documentée sur Windows. Vide = celle de POSIX."""

    deprecated_since: str = ""
    """Date a laquelle la CLI a cesse de servir le grand public, si elle l'a fait.

    Ce n'est pas une suppression : Gemini CLI continue de repondre aux licences
    entreprise. Joe garde donc le fournisseur, cesse seulement de le preferer,
    et le dit — sinon un utilisateur grand public voit des echecs sans cause
    apparente.
    """

    successor: str = ""
    """Nom de la CLI qui la remplace, tel qu'on l'affiche."""

    successor_provider: str = ""
    """Identifiant du fournisseur qui la remplace, quand Joe le pilote aussi.

    `successor` s'adresse a un lecteur, celui-ci au routeur : une regle qui
    nomme un fournisseur pour ce qu'il sait faire doit atterrir sur la CLI qui
    le sert encore, pas sur celle qui portait ce role avant.
    """

    successor_url: str = ""
    """Ou se la procurer."""

    requires_node: str = ""
    """Version majeure de Node exigee par la commande d'installation npm.

    L'exigence porte sur l'installation, pas sur l'execution : Codex tourne ici
    sous Node 20 alors que son `npm install -g` en demande 22. C'est donc un
    avertissement attache a la commande, la ou il sert — sinon `npm` echoue sur
    un message qui ne nomme jamais la cause.
    """

    homepage: str = ""
    """Page officielle. C'est elle qui fait foi, pas la commande ci-dessus.

    Une commande d'installation vieillit ; Joe l'affiche pour épargner une
    recherche, jamais comme source de vérité. L'interface montre toujours les
    deux.
    """

    sign_in: str = ""
    """Comment s'authentifier une fois la CLI installée.

    Joe ne détient aucun identifiant : une CLI installée mais non connectée
    échoue au premier run, avec une erreur que rien ne rattache à la cause.
    """


PROVIDERS = (
    ProviderSpec(
        "codex",
        "Codex",
        streams_json=True,
        reviewer_peers=("claude", "cursor-agent"),
        fallbacks=("claude", "copilot", "cursor-agent", "antigravity", "gemini"),
        # 0.147.0 rejetait `--search`, que Joe passe en accès distant : la
        # borne annoncée décrivait donc une version où les runs échouaient.
        minimum_version="0.155.0",
        exposes_usage=True,
        install_posix="npm install -g @openai/codex",
        requires_node="22",
        install_windows="npm install -g @openai/codex",
        homepage="https://github.com/openai/codex",
        sign_in="codex",
    ),
    ProviderSpec(
        "claude",
        "Claude",
        streams_json=True,
        reviewer_peers=("codex", "cursor-agent"),
        fallbacks=("codex", "copilot", "cursor-agent", "antigravity", "gemini"),
        minimum_version="2.1.197",
        exposes_usage=True,
        # L'installation par npm est dépréciée en amont : l'installeur natif
        # n'a aucune dépendance et se met à jour seul.
        install_posix="curl -fsSL https://claude.ai/install.sh | bash",
        install_windows="irm https://claude.ai/install.ps1 | iex",
        homepage="https://code.claude.com/docs",
        sign_in="claude",
    ),
    ProviderSpec(
        "gemini",
        "Gemini",
        streams_json=True,
        watchdog_seconds=90,
        terminal_quota_markers=(
            "resource_exhausted",
            "exceeded your current quota",
            "status 429",
        ),
        reviewer_peers=("codex", "claude"),
        fallbacks=("codex", "claude", "copilot"),
        minimum_version="0.52.0",
        exposes_usage=True,
        # Etait l'arbitre prefere, parce que rarement juge et partie. Depuis le
        # 18 juin 2026 la CLI ne sert plus les comptes grand public : en faire
        # l'arbitre par defaut revenait a choisir celui qui echouera.
        arbitration_priority=5,
        deprecated_since="2026-06-18",
        successor="Antigravity CLI",
        successor_provider="antigravity",
        successor_url="https://antigravity.google",
        install_posix="npm install -g @google/gemini-cli",
        requires_node="20",
        install_windows="npm install -g @google/gemini-cli",
        homepage="https://github.com/google-gemini/gemini-cli",
        sign_in="gemini",
    ),
    ProviderSpec(
        "copilot",
        "Copilot",
        reviewer_peers=("codex", "claude"),
        fallbacks=("codex", "claude", "antigravity", "gemini"),
        minimum_version="1.0.75",
        install_posix="npm install -g @github/copilot",
        requires_node="22",
        install_windows="npm install -g @github/copilot",
        homepage="https://docs.github.com/en/copilot/get-started/cli-quickstart",
        sign_in="copilot, then /login",
    ),
    # Successeur de Gemini CLI, depreciee pour le grand public le 18 juin 2026.
    # Le nom du fournisseur est parlant, celui du binaire ne l'est pas : `agy`.
    ProviderSpec(
        "antigravity",
        "Antigravity",
        streams_json=True,
        executables=("agy",),
        reviewer_peers=("codex", "claude"),
        fallbacks=("claude", "codex", "copilot"),
        install_posix="curl -fsSL https://antigravity.google/cli/install.sh | bash",
        install_windows="irm https://antigravity.google/cli/install.ps1 | iex",
        homepage="https://antigravity.google/docs/cli/install/",
        # Aucune commande de connexion : la CLI ouvre un navigateur, ou affiche
        # une URL et attend un code quand elle detecte SSH.
        sign_in="agy",
    ),
    # Le nom porte le suffixe `-agent` parce qu'il a d'abord servi à trouver
    # l'exécutable : `cursor` est l'éditeur, la CLI est sans fenêtre. Il reste
    # le nom du fournisseur — il est écrit dans les conversations — mais
    # l'exécutable, lui, s'appelle `agent` sur une installation actuelle.
    # `cursor-agent` subsiste par compatibilité, donc on cherche les deux.
    ProviderSpec(
        "cursor-agent",
        "Cursor",
        reviewer_peers=("codex", "claude"),
        fallbacks=("claude", "codex", "antigravity", "gemini"),
        # `agent` est un nom générique qu'une autre CLI peut occuper : le
        # diagnostic vérifie l'identité du binaire trouvé sous ce nom-là.
        executables=("cursor-agent", "agent"),
        identity_marker="cursor",
        install_posix="curl https://cursor.com/install -fsS | bash",
        install_windows="irm 'https://cursor.com/install?win32=true' | iex",
        homepage="https://cursor.com/docs/cli/installation",
        sign_in="agent login",
    ),
)

_BY_NAME = {provider.name: provider for provider in PROVIDERS}


def get_provider_specs() -> tuple[ProviderSpec, ...]:
    return PROVIDERS


def get_provider_names() -> tuple[str, ...]:
    return tuple(provider.name for provider in PROVIDERS)


def get_provider_catalog(
    available: set[str] | None = None,
) -> tuple[dict[str, object], ...]:
    """Le catalogue, et ce que Joe peut réellement lancer.

    Sans cette marque, l'interface proposait un agent que le panneau des CLI
    déclarait absent dans la même fenêtre : deux affirmations contraires sur le
    même écran, et un choix qui ne pouvait pas aboutir.
    """
    return tuple(
        {
            "id": provider.name,
            "label": provider.label,
            "available": available is None or provider.name in available,
        }
        for provider in PROVIDERS
    )


def get_provider_spec(name: str) -> ProviderSpec:
    """Return one spec, or a neutral one for an unknown name.

    Un nom inconnu ne doit pas faire planter une lecture de comportement : il
    hérite des valeurs les plus prudentes (pas de JSON, pas de watchdog).
    """
    return _BY_NAME.get(name) or ProviderSpec(name, name.capitalize())


def provider_executables(name: str) -> tuple[str, ...]:
    """Noms à chercher sur le PATH pour ce fournisseur, par ordre."""
    spec = get_provider_spec(name)
    return spec.executables or (spec.name,)


def install_hint(name: str, *, windows: bool = False) -> dict[str, str]:
    """Tout ce qu'il faut pour installer et connecter une CLI absente."""
    spec = get_provider_spec(name)
    command = spec.install_windows if windows else spec.install_posix
    return {
        "command": command,
        "homepage": spec.homepage,
        "sign_in": spec.sign_in,
        "requires_node": spec.requires_node,
    }


def default_fallbacks() -> dict[str, list[str]]:
    return {
        provider.name: list(provider.fallbacks)
        for provider in PROVIDERS
        if provider.fallbacks
    }


def minimum_versions() -> dict[str, str]:
    """Every provider, including those whose working version is unknown.

    Filtrer les versions vides sortait le fournisseur de l'audit : il
    disparaissait du diagnostic au lieu d'y figurer comme non vérifié.
    """
    return {provider.name: provider.minimum_version for provider in PROVIDERS}


def usage_providers() -> tuple[str, ...]:
    return tuple(provider.name for provider in PROVIDERS if provider.exposes_usage)


def arbitration_order(
    proposers: tuple[str, ...] = (),
    eligible: tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    """Rank who should synthesise a consensus, best first.

    Un tiers est préférable — il n'a pas à départager sa propre proposition —
    mais l'exclusion n'est qu'une préférence : quand les proposants sont les
    seuls disponibles, l'un d'eux arbitre plutôt que de faire échouer le
    consensus. Ils passent simplement en fin de liste.
    """
    allowed = eligible if eligible is not None else get_provider_names()
    candidates = [name for name in allowed if name in _BY_NAME]

    def rank(name: str) -> tuple[int, int, str]:
        return (
            1 if name in proposers else 0,
            _BY_NAME[name].arbitration_priority,
            name,
        )

    return tuple(sorted(candidates, key=rank))


def counterpart(primary: str, eligible: tuple[str, ...] | None = None) -> str | None:
    """Return the preferred reviewer for `primary`, honouring the registry."""
    allowed = eligible if eligible is not None else get_provider_names()
    for peer in get_provider_spec(primary).reviewer_peers:
        if peer != primary and peer in allowed:
            return peer
    for name in allowed:
        if name != primary:
            return name
    return None
