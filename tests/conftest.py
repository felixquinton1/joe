import os
import stat
import shutil
import subprocess
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import OpenerDirector, Request

import pytest

from joe.provider_health import clear_cooldowns


def assert_mode(path, expected):
    """Assert POSIX permission bits, where they exist.

    Windows n'a pas de bits de permission : un fichier y hérite des ACL du
    profil utilisateur, et `chmod` n'y bascule que la lecture seule. Le mode
    lu vaut alors 0o666 quoi que Joe ait demandé, et l'assertion n'a pas de sens.
    """
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == expected


@pytest.fixture(scope="session", autouse=True)
def offline_provider_clis(tmp_path_factory):
    """Default tests use fixtures, never authenticated CLIs from the host.

    The existing live smoke test remains an explicit opt-in. Loopback HTTP
    and Python/JSONL subprocess fixtures remain available to ordinary tests.
    """
    if os.environ.get("JOE_LIVE_SMOKE") == "1":
        yield
        return
    from joe.provider_registry import get_provider_specs

    commands = {name for spec in get_provider_specs()
                for name in (spec.name, *spec.executables)}
    original_which, original_popen = shutil.which, subprocess.Popen
    original_open = OpenerDirector.open
    fixture_root = tmp_path_factory.getbasetemp().resolve()

    def fixture_which(command, *args, **kwargs):
        if Path(os.fsdecode(command)).name.removesuffix(".exe") in commands:
            return None
        return original_which(command, *args, **kwargs)

    def fixture_popen(args, *positional, **kwargs):
        executable = kwargs.get("executable") or (args[0] if isinstance(args, (list, tuple)) else args)
        path = Path(os.fsdecode(executable))
        if path.name.removesuffix(".exe") in commands and not path.resolve().is_relative_to(fixture_root):
            raise FileNotFoundError("Authenticated provider CLIs are disabled in the default test suite")
        return original_popen(args, *positional, **kwargs)

    def fixture_open(self, fullurl, *args, **kwargs):
        url = fullurl.full_url if isinstance(fullurl, Request) else fullurl
        if urlsplit(url).hostname not in {"127.0.0.1", "::1", "localhost"}:
            raise URLError("External HTTP is disabled in the default test suite")
        return original_open(self, fullurl, *args, **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(shutil, "which", fixture_which)
        patch.setattr(subprocess, "Popen", fixture_popen)
        patch.setattr(OpenerDirector, "open", fixture_open)
        yield


@pytest.fixture(scope="session", autouse=True)
def warm_provider_catalogue(offline_provider_clis):
    from joe.capabilities import provider_capabilities

    provider_capabilities()


@pytest.fixture(autouse=True)
def isolated_joe_data_home(tmp_path, monkeypatch):
    clear_cooldowns()
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "joe-test-data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "joe-test-config"))
    monkeypatch.setenv("JOE_DISABLE_LLM_ROUTER", "1")
    # Unit tests exercise scheduling deterministically. A daemon scheduler
    # surviving the test that created it must never launch a real provider.
    monkeypatch.setenv("JOE_DISABLE_BACKGROUND_WORKERS", "1")
    yield
    clear_cooldowns()


def build_test_server(tmp_path, *, role=None, ai_access="auto"):
    """Serveur de test partagé : le niveau d'accès est explicite, jamais implicite.

    Les trois fabriques recopiées avaient déjà divergé — l'une d'elles tournait
    en accès manuel pendant que les autres étaient en automatique, sans que ce
    soit visible nulle part.
    """
    import threading

    from joe.auth import LocalAuth
    from joe.web import Handler, JoeServer, RunManager

    server = JoeServer(("127.0.0.1", 0), Handler)
    server.manager = RunManager(tmp_path)
    server.auth = (
        LocalAuth("test-token", role, True) if role else LocalAuth()
    )
    server.auth_path = tmp_path / "auth-token"
    for project in server.manager.conversations.list_projects():
        server.manager.conversations.update_project(
            project["id"], {"ai_access": ai_access}
        )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread
