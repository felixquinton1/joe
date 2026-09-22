from pathlib import Path

from joe.doctor import doctor_report, format_doctor
from joe.models import ProviderResult


class DoctorProvider:
    def __init__(self, name: str, ok: bool = True):
        self.name = name
        self.executable = name
        self.ok = ok

    def run(self, *args, **kwargs):
        return ProviderResult(
            self.name,
            [self.name],
            "OK" if self.ok else "",
            "" if self.ok else "quota",
            0 if self.ok else 1,
            0.1,
            error_kind=None if self.ok else "quota",
        )


def test_doctor_reports_storage_and_live_provider_health(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "joe.doctor.usage_status",
        lambda force=False: [],
    )
    monkeypatch.setattr(
        "joe.doctor.provider_audit",
        lambda: [{"provider": "codex", "current_version": "1.2.3"}],
    )
    monkeypatch.setattr(
        "joe.doctor.resolve_executable",
        lambda name, **kwargs: f"/usr/bin/{name}",
    )

    report = doctor_report(
        tmp_path,
        live=True,
        providers={"codex": DoctorProvider("codex")},
    )

    assert report["storage"]["writable"] is True
    assert report["providers"][0]["live"]["ok"] is True
    assert report["providers"][0]["version"] == "1.2.3"
    assert "live OK" in format_doctor(report)


def test_doctor_does_not_consume_quota_without_live(tmp_path, monkeypatch):
    provider = DoctorProvider("claude")
    provider.run = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("provider must not run")
    )
    monkeypatch.setattr("joe.doctor.usage_status", lambda force=False: [])
    monkeypatch.setattr(
        "joe.doctor.provider_audit",
        lambda: [{"provider": "claude", "current_version": None}],
    )
    monkeypatch.setattr(
        "joe.doctor.resolve_executable", lambda name, **kwargs: None
    )

    report = doctor_report(
        Path(tmp_path),
        providers={"claude": provider},
    )

    assert report["providers"][0]["installed"] is False
    assert "--live" in format_doctor(report)


def test_doctor_tells_a_newcomer_how_to_install_what_is_missing(tmp_path, monkeypatch):
    """C'est la première commande du README après l'installation.

    Elle répondait en français à qui lit une documentation anglaise, et
    constatait l'absence d'une CLI sans jamais dire comment l'obtenir.
    """
    from joe.doctor import format_doctor

    monkeypatch.setattr("joe.doctor.usage_status", lambda force=False: [])
    monkeypatch.setattr("joe.doctor.provider_audit", lambda: [])
    monkeypatch.setattr("joe.doctor.resolve_executable", lambda name, **kwargs: None)

    text = format_doctor(doctor_report(tmp_path))

    assert "Storage: OK" in text
    assert "not found" in text
    assert "installé" not in text and "Stockage" not in text
    # Chaque absence est suivie de quoi faire, commande et page officielle.
    # La commande est celle de la plateforme : l'affirmer en dur ferait passer
    # le test pour un echec la ou le produit a justement raison.
    import os

    from joe.provider_registry import get_provider_names, install_hint

    assert "Install Claude:" in text
    for name in get_provider_names():
        hint = install_hint(name, windows=os.name == "nt")
        assert hint["command"] in text, name
        assert hint["homepage"] in text, name


def test_doctor_names_a_binary_it_could_not_confirm(tmp_path, monkeypatch):
    """Dire « absent » alors qu'un candidat existe cache la seule piste utile."""
    from joe.doctor import format_doctor

    monkeypatch.setattr("joe.doctor.usage_status", lambda force=False: [])
    monkeypatch.setattr("joe.doctor.provider_audit", lambda: [])
    monkeypatch.setattr(
        "joe.doctor.resolve_executable",
        lambda name, **kwargs: (
            "/venv/bin/agent" if kwargs.get("unconfirmed") and name == "cursor-agent" else None
        ),
    )

    report = doctor_report(tmp_path)
    cursor = next(item for item in report["providers"] if item["provider"] == "cursor-agent")

    assert cursor["installed"] is False
    assert cursor["unconfirmed"] == "/venv/bin/agent"
    assert "unconfirmed · found /venv/bin/agent" in format_doctor(report)


def test_a_detected_cli_still_says_it_needs_a_sign_in(tmp_path, monkeypatch):
    """Trouver le binaire ne dit rien du compte.

    Une CLI installee mais jamais connectee se presente comme utilisable, se
    laisse choisir, puis echoue au premier run avec une erreur que rien ne
    rattache a sa cause.
    """
    monkeypatch.setattr("joe.doctor.usage_status", lambda force=False: [])
    monkeypatch.setattr("joe.doctor.provider_audit", lambda: [])
    monkeypatch.setattr(
        "joe.doctor.resolve_executable", lambda name, **kwargs: f"/usr/bin/{name}"
    )
    monkeypatch.setattr("joe.doctor.recent_failure", lambda name: None)

    report = doctor_report(tmp_path)
    cursor = next(
        item for item in report["providers"] if item["provider"] == "cursor-agent"
    )

    assert cursor["installed"] is True
    assert cursor["auth_failed"] is False
    # La commande de connexion accompagne aussi une CLI presente.
    assert cursor["install"]["sign_in"] == "agent login"


def test_a_refused_sign_in_is_reported_where_the_choice_is_made(tmp_path, monkeypatch):
    """Joe ne peut pas deviner l'etat du compte, mais il retient un refus."""
    monkeypatch.setattr("joe.doctor.usage_status", lambda force=False: [])
    monkeypatch.setattr("joe.doctor.provider_audit", lambda: [])
    monkeypatch.setattr(
        "joe.doctor.resolve_executable", lambda name, **kwargs: f"/usr/bin/{name}"
    )
    monkeypatch.setattr(
        "joe.doctor.recent_failure",
        lambda name: ("authentication", 120) if name == "claude" else None,
    )

    report = doctor_report(tmp_path)
    states = {item["provider"]: item["auth_failed"] for item in report["providers"]}

    assert states["claude"] is True
    assert states["codex"] is False


def test_an_npm_install_warns_when_node_is_too_old(tmp_path, monkeypatch):
    """`npm` echoue sur un message qui ne nomme jamais la cause.

    L'utilisateur colle la commande, lit une erreur obscure, et rien ne lui dit
    que son Node est trop ancien.
    """
    from joe.doctor import _node_warning, format_doctor

    vieux = {"present": True, "version": "20.19.5", "major": 20}
    assert _node_warning("22", vieux) == "Node 20.19.5 present, requires 22+"
    assert _node_warning("20", vieux) == ""
    # Sans exigence declaree, aucun avertissement.
    assert _node_warning("", vieux) == ""
    assert _node_warning("22", {"present": False}) == "node absent, requires Node 22+"

    monkeypatch.setattr("joe.doctor.usage_status", lambda force=False: [])
    monkeypatch.setattr("joe.doctor.provider_audit", lambda: [])
    monkeypatch.setattr("joe.doctor.resolve_executable", lambda name, **kwargs: None)
    monkeypatch.setattr("joe.doctor.node_runtime", lambda: vieux)

    texte = format_doctor(doctor_report(tmp_path))
    assert "requires 22+" in texte
    # Claude s'installe par un script, sans Node : rien ne doit l'encombrer.
    claude = texte.split("Install Claude:")[1].split("Install ")[0]
    assert "requires" not in claude


def test_an_installed_cli_is_never_nagged_about_node(tmp_path, monkeypatch):
    """Codex tourne ici sous Node 20 alors que son paquet en demande 22.

    L'exigence porte sur l'installation, pas sur l'execution.
    """
    monkeypatch.setattr("joe.doctor.usage_status", lambda force=False: [])
    monkeypatch.setattr("joe.doctor.provider_audit", lambda: [])
    monkeypatch.setattr(
        "joe.doctor.resolve_executable", lambda name, **kwargs: f"/usr/bin/{name}"
    )
    monkeypatch.setattr(
        "joe.doctor.node_runtime",
        lambda: {"present": True, "version": "20.19.5", "major": 20},
    )

    report = doctor_report(tmp_path)
    assert all(item["install"]["node_warning"] == "" for item in report["providers"])
