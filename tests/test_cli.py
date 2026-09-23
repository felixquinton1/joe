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
    assert "stopped 2 tmux sessions" in capsys.readouterr().out


def test_kill_succeeds_when_no_joe_session_exists(monkeypatch, capsys):
    monkeypatch.setattr("joe.cli.shutil.which", lambda name: "/usr/bin/tmux")
    monkeypatch.setattr(
        "joe.cli.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=""),
    )

    assert _kill([]) == 0
    assert "no active tmux session" in capsys.readouterr().out


def test_restart_refuses_when_a_run_is_active(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("joe.cli.shutil.which", lambda name: "/usr/bin/tmux")
    monkeypatch.setattr("joe.cli._active_runs", lambda url: [{"run_id": "one"}])

    assert _restart(["-C", str(tmp_path)]) == 3
    assert "a task is still active" in capsys.readouterr().err


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
    assert "use --force" in capsys.readouterr().err


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


def test_web_uses_the_native_windows_background_manager(tmp_path, monkeypatch):
    from joe import background
    from joe.cli import _web

    calls = []
    monkeypatch.setattr(background, "is_windows", lambda: True)
    monkeypatch.setattr(
        "joe.cli._windows_web",
        lambda args, url: calls.append((args, url)) or 0,
    )

    assert _web(["-C", str(tmp_path), "--no-browser"]) == 0
    assert calls[0][1] == "http://127.0.0.1:8765"


def test_windows_background_launches_the_same_joe_in_foreground(
    tmp_path, monkeypatch, capsys
):
    from joe import background
    from joe.cli import _windows_web

    calls = []
    state = background.BackgroundState(
        pid=4321,
        port=8765,
        project=str(tmp_path),
        host="127.0.0.1",
        profile="maintainer",
        command=[],
        log=str(tmp_path / "server.log"),
        started_at="2026-09-23T00:00:00+00:00",
    )
    process = SimpleNamespace(poll=lambda: None, returncode=None)
    monkeypatch.setattr(background, "load_state", lambda port: None)
    monkeypatch.setattr(
        background,
        "start_windows_background",
        lambda command, **kwargs: calls.append((command, kwargs))
        or (state, process),
    )
    monkeypatch.setattr("joe.cli._wait_for_server", lambda url: True)
    monkeypatch.setattr("joe.cli._server_status", lambda url: None)
    monkeypatch.setattr("joe.cli._self_command", lambda: ["joe.exe"])
    args = SimpleNamespace(
        project=tmp_path,
        host="127.0.0.1",
        port=8765,
        no_browser=True,
        profile="maintainer",
        allow_remote=False,
    )

    assert _windows_web(args, "http://127.0.0.1:8765") == 0
    command = calls[0][0]
    assert command[:2] == ["joe.exe", "web"]
    assert "--foreground" in command
    assert "--no-browser" in command
    assert "running in the background" in capsys.readouterr().out


def test_stop_uses_the_windows_background_state(monkeypatch, capsys):
    from joe import background

    calls = []
    monkeypatch.setattr(background, "is_windows", lambda: True)
    monkeypatch.setattr(
        background,
        "stop_windows_background",
        lambda port: calls.append(port) or True,
    )

    assert _stop(["--port", "9000"]) == 0
    assert calls == [9000]
    assert "port 9000" in capsys.readouterr().out


def test_restart_uses_the_windows_background_state(tmp_path, monkeypatch):
    from joe import background

    calls = []
    monkeypatch.setattr(background, "is_windows", lambda: True)
    monkeypatch.setattr(
        background,
        "stop_windows_background",
        lambda port: calls.append(("stop", port)) or True,
    )
    monkeypatch.setattr("joe.cli._active_runs", lambda url: [])
    monkeypatch.setattr(
        "joe.cli._server_status",
        lambda url: {"profile": "viewer", "project": str(tmp_path)},
    )
    monkeypatch.setattr(
        "joe.cli._web",
        lambda args: calls.append(("web", args)) or 0,
    )

    assert _restart(["--port", "9000"]) == 0
    assert calls[0] == ("stop", 9000)
    assert calls[1][0] == "web"
    assert calls[1][1][-2:] == ["--profile", "viewer"]


def test_logs_prints_the_tail_of_the_background_log(tmp_path, monkeypatch, capsys):
    from joe import background
    from joe.cli import _logs

    target = tmp_path / "server-8765.log"
    target.write_text("one\ntwo\nthree\n", encoding="utf-8")
    monkeypatch.setattr(background, "log_path", lambda port: target)

    assert _logs(["--tail", "2"]) == 0
    assert capsys.readouterr().out == "two\nthree\n"


def test_server_status_authenticates_to_recover_the_active_project(
    tmp_path, monkeypatch
):
    from joe import auth
    from joe.cli import _server_status

    token_path = tmp_path / "auth-token"
    token_path.write_text("secret", encoding="utf-8")
    monkeypatch.setattr(auth, "auth_token_path", lambda: token_path)
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"project": "C:/work/project", "profile": "viewer"}'

    def fake_urlopen(request, timeout):
        seen["request"] = request
        seen["timeout"] = timeout
        return Response()

    monkeypatch.setattr("joe.cli.urllib.request.urlopen", fake_urlopen)

    assert _server_status("http://127.0.0.1:8765") == {
        "project": "C:/work/project",
        "profile": "viewer",
    }
    assert seen["request"].get_header("Authorization") == "Bearer secret"
    assert seen["timeout"] == 2
