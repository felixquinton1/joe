from types import SimpleNamespace

from joe.cli import _kill, _restart, parser


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


def test_restart_refuses_when_a_run_is_active(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("joe.cli.shutil.which", lambda name: "/usr/bin/tmux")
    monkeypatch.setattr("joe.cli._active_runs", lambda url: [{"run_id": "one"}])

    assert _restart(["-C", str(tmp_path)]) == 3
    assert "tâche est encore active" in capsys.readouterr().err


def test_restart_recreates_only_selected_tmux_session(
    tmp_path, monkeypatch
):
    calls = []
    monkeypatch.setattr("joe.cli.shutil.which", lambda name: "/usr/bin/tmux")
    monkeypatch.setattr("joe.cli._active_runs", lambda url: [])
    monkeypatch.setattr(
        "joe.cli.subprocess.run",
        lambda command, **kwargs: calls.append(command)
        or SimpleNamespace(returncode=0, stdout=""),
    )
    monkeypatch.setattr("joe.cli._web", lambda args: calls.append(args) or 0)

    assert _restart(["-C", str(tmp_path), "--port", "9000"]) == 0
    assert calls[0] == ["tmux", "kill-session", "-t", "joe-9000"]
    assert "--port" in calls[1]
    assert "9000" in calls[1]


def test_restart_requires_force_when_server_is_unreachable(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr("joe.cli.shutil.which", lambda name: "/usr/bin/tmux")
    monkeypatch.setattr("joe.cli._active_runs", lambda url: None)

    assert _restart(["-C", str(tmp_path)]) == 3
    assert "utilise --force" in capsys.readouterr().err


def test_forced_restart_recovers_an_unreachable_server(
    tmp_path, monkeypatch
):
    calls = []
    monkeypatch.setattr("joe.cli.shutil.which", lambda name: "/usr/bin/tmux")
    monkeypatch.setattr("joe.cli._active_runs", lambda url: None)
    monkeypatch.setattr(
        "joe.cli.subprocess.run",
        lambda command, **kwargs: calls.append(command)
        or SimpleNamespace(returncode=0, stdout=""),
    )
    monkeypatch.setattr("joe.cli._web", lambda args: calls.append(args) or 0)

    assert _restart(["-C", str(tmp_path), "--force"]) == 0
    assert calls[0] == ["tmux", "kill-session", "-t", "joe-8765"]


def test_help_explains_the_full_cli_and_web_interfaces():
    help_text = parser().format_help()

    assert "joe cli" in help_text
    assert "joe web" in help_text
    assert "doctor" in help_text
