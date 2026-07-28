from types import SimpleNamespace

from joe.cli import _kill


def test_kill_stops_only_numbered_joe_tmux_sessions(monkeypatch, capsys):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[1] == "list-sessions":
            return SimpleNamespace(
                returncode=0,
                stdout="joe-8765\nwork\njoe-debug\njoe-9000\n",
            )
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr("joe.cli.shutil.which", lambda name: "/usr/bin/tmux")
    monkeypatch.setattr("joe.cli.subprocess.run", fake_run)

    assert _kill([]) == 0

    assert calls[1:] == [
        ["tmux", "kill-session", "-t", "joe-8765"],
        ["tmux", "kill-session", "-t", "joe-9000"],
    ]
    assert "2 sessions tmux arrêtées" in capsys.readouterr().out


def test_kill_succeeds_when_no_joe_session_exists(monkeypatch, capsys):
    monkeypatch.setattr("joe.cli.shutil.which", lambda name: "/usr/bin/tmux")
    monkeypatch.setattr(
        "joe.cli.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=""),
    )

    assert _kill([]) == 0
    assert "aucune session" in capsys.readouterr().out
