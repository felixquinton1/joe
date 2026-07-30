import pytest

from joe.provider_health import clear_cooldowns


@pytest.fixture(autouse=True)
def isolated_joe_data_home(tmp_path, monkeypatch):
    clear_cooldowns()
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "joe-test-data"))
    monkeypatch.setenv("JOE_DISABLE_LLM_ROUTER", "1")
    yield
    clear_cooldowns()
