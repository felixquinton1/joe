from types import SimpleNamespace

from joe.cli import _kill, _restart, _stop, parser


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
        "joe.cli._server_status",
        lambda url: {"profile": "viewer"},
    )
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
    assert calls[1][-2:] == ["--profile", "viewer"]


def test_restart_reuses_the_active_server_project_by_default(
    tmp_path, monkeypatch
):
    calls = []
    monkeypatch.setattr("joe.cli.shutil.which", lambda name: "/usr/bin/tmux")
    monkeypatch.setattr("joe.cli._active_runs", lambda url: [])
    monkeypatch.setattr(
        "joe.cli._server_status",
        lambda url: {"profile": "maintainer", "project": str(tmp_path)},
    )
    monkeypatch.setattr(
        "joe.cli.subprocess.run",
        lambda command, **kwargs: calls.append(command)
        or SimpleNamespace(returncode=0, stdout=""),
    )
    monkeypatch.setattr("joe.cli._web", lambda args: calls.append(args) or 0)

    assert _restart([]) == 0
    project_index = calls[1].index("-C") + 1
    assert calls[1][project_index] == str(tmp_path.resolve())


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


def test_stop_targets_only_the_requested_joe_port(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr("joe.cli.shutil.which", lambda command: "/usr/bin/tmux")
    monkeypatch.setattr(
        "joe.cli.subprocess.run",
        lambda command, **kwargs: (
            calls.append(command)
            or SimpleNamespace(returncode=0)
        ),
    )

    assert _stop(["--port", "9000"]) == 0
    assert calls == [["tmux", "kill-session", "-t", "joe-9000"]]
    assert "port 9000" in capsys.readouterr().out


def test_joe_relaunches_itself_not_another_installation(monkeypatch, tmp_path):
    """La session tmux doit exécuter le Joe invoqué, pas un homonyme du PATH.

    `shutil.which("joe")` renvoyait la première installation du PATH : sur une
    machine qui en porte plusieurs, le serveur tournait avec un autre code que
    celui lancé, et changeait de dossier de travail sans qu'on le voie.
    """
    from joe.cli import _self_command

    script = tmp_path / "joe"
    script.write_text("", encoding="utf-8")
    monkeypatch.setattr("sys.argv", [str(script), "web"])
    assert _self_command() == [str(script)]

    # Invoqué autrement (python -m), on repasse par le module, jamais par PATH.
    monkeypatch.setattr("sys.argv", ["-c"])
    command = _self_command()
    assert command[1:] == ["-m", "joe.cli"]
