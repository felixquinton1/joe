import pytest

from joe.provider_health import clear_cooldowns


@pytest.fixture(autouse=True)
def isolated_joe_data_home(tmp_path, monkeypatch):
    clear_cooldowns()
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "joe-test-data"))
    monkeypatch.setenv("JOE_DISABLE_LLM_ROUTER", "1")
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
