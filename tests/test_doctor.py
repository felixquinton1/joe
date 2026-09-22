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
    assert "Install Claude:" in text
    assert "curl -fsSL https://claude.ai/install.sh | bash" in text
    assert "https://code.claude.com/docs" in text
    assert "npm install -g @openai/codex" in text


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
