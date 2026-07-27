import pytest


@pytest.fixture(autouse=True)
def isolated_joe_data_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "joe-test-data"))
