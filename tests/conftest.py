import pytest

from joe.provider_health import clear_cooldowns


@pytest.fixture(autouse=True)
def isolated_joe_data_home(tmp_path, monkeypatch):
    clear_cooldowns()
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "joe-test-data"))
    yield
    clear_cooldowns()
