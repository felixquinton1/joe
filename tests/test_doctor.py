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
        "joe.doctor.windows_aware_executable",
        lambda executable: f"/usr/bin/{executable}",
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
        "joe.doctor.windows_aware_executable", lambda executable: None
    )

    report = doctor_report(
        Path(tmp_path),
        providers={"claude": provider},
    )

    assert report["providers"][0]["installed"] is False
    assert "--live" in format_doctor(report)
