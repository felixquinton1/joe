import os
import stat

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
def warm_provider_catalogue():
    """Interroger les CLI du poste une fois, avant le premier test.

    `provider_capabilities` lance un sous-processus par fournisseur installe :
    environ trois secondes a froid sur une machine complete. Ce cout tombait
    sur le premier test a traverser un chemin de requete, dont le client
    attend cinq secondes — et sous charge la limite etait franchie. Les tests
    viraient au rouge selon la machine et non selon le code, dont un test
    d'escalade de privileges qui echouait sur un timeout de socket alors que
    le refus, lui, etait correct.

    La chauffe se fait ici, hors de tout delai de client. Le contenu reste
    celui du poste : on supprime la latence, pas la realite.
    """
    from joe.capabilities import provider_capabilities

    provider_capabilities()


@pytest.fixture(autouse=True)
def isolated_joe_data_home(tmp_path, monkeypatch):
    clear_cooldowns()
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "joe-test-data"))
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
