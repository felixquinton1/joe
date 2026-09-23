from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from joe import background


def test_windows_runtime_uses_local_app_data(tmp_path, monkeypatch):
    monkeypatch.setattr(background, "is_windows", lambda: True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert background.runtime_dir() == tmp_path / "Joe" / "run"
    assert background.state_path(8765).name == "server-8765.json"
    assert background.log_path(8765).name == "server-8765.log"


def test_start_windows_background_records_pid_command_and_log(tmp_path, monkeypatch):
    calls = []

    class FakeProcess:
        pid = 4321

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return FakeProcess()

    monkeypatch.setattr(background, "runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(background.subprocess, "Popen", fake_popen)

    state, process = background.start_windows_background(
        ["joe.exe", "web", "--foreground"],
        port=9000,
        project=tmp_path,
        host="127.0.0.1",
        profile="maintainer",
    )

    assert process.pid == 4321
    assert state.pid == 4321
    assert state.command == ["joe.exe", "web", "--foreground"]
    assert Path(state.log) == tmp_path / "server-9000.log"
    assert calls[0][1]["creationflags"] == 0x208
    assert calls[0][1]["stdin"] is subprocess.DEVNULL
    saved = json.loads((tmp_path / "server-9000.json").read_text(encoding="utf-8"))
    assert saved["project"] == str(tmp_path.resolve())


def test_stop_windows_background_kills_the_process_tree(tmp_path, monkeypatch):
    state = background.BackgroundState(
        pid=4321,
        port=8765,
        project=str(tmp_path),
        host="127.0.0.1",
        profile="maintainer",
        command=["joe.exe"],
        log=str(tmp_path / "server-8765.log"),
        started_at="2026-09-23T00:00:00+00:00",
    )
    monkeypatch.setattr(background, "runtime_dir", lambda: tmp_path)
    background.save_state(state)
    monkeypatch.setattr(background, "process_is_running", lambda pid: True)
    calls = []
    monkeypatch.setattr(
        background.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command) or SimpleNamespace(returncode=0),
    )

    assert background.stop_windows_background(8765) is True
    assert calls == [["taskkill", "/PID", "4321", "/T", "/F"]]
    assert not (tmp_path / "server-8765.json").exists()


def test_managed_ports_ignores_unrelated_files(tmp_path, monkeypatch):
    monkeypatch.setattr(background, "runtime_dir", lambda: tmp_path)
    (tmp_path / "server-8765.json").write_text("{}", encoding="utf-8")
    (tmp_path / "server-9000.json").write_text("{}", encoding="utf-8")
    (tmp_path / "server-nope.json").write_text("{}", encoding="utf-8")
    (tmp_path / "other.json").write_text("{}", encoding="utf-8")


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows process lifecycle")
def test_native_windows_background_process_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr(background, "runtime_dir", lambda: tmp_path)
    state, _ = background.start_windows_background(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        port=9876,
        project=tmp_path,
        host="127.0.0.1",
        profile="viewer",
    )
    try:
        assert background.process_is_running(state.pid)
        assert background.stop_windows_background(9876)
        assert not background.state_path(9876).exists()
    finally:
        if background.process_is_running(state.pid):
            subprocess.run(
                ["taskkill", "/PID", str(state.pid), "/T", "/F"],
                check=False,
            )
    assert background.managed_ports() == [8765, 9000]
