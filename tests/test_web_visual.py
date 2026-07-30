import shutil
import struct
import subprocess
import threading
from pathlib import Path

import pytest

from joe.auth import LocalAuth
from joe.web import Handler, JoeServer, RunManager


@pytest.mark.parametrize("size", ["1440,900", "390,844"])
def test_web_shell_renders_in_headless_chromium(tmp_path, size):
    chromium = (
        shutil.which("chromium")
        or shutil.which("chromium-browser")
        or shutil.which("google-chrome")
    )
    if not chromium:
        pytest.skip("Chromium is not installed")
    server = JoeServer(("127.0.0.1", 0), Handler)
    server.auth = LocalAuth()
    server.manager = RunManager(tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    screenshot = tmp_path / f"joe-{size.replace(',', 'x')}.png"
    try:
        result = subprocess.run(
            [
                chromium,
                "--headless",
                "--no-sandbox",
                "--disable-gpu",
                "--hide-scrollbars",
                f"--window-size={size}",
                f"--screenshot={screenshot}",
                f"http://127.0.0.1:{server.server_port}/",
            ],
            capture_output=True,
            timeout=25,
            check=False,
        )
        stderr = result.stderr.decode(errors="replace")
        if result.returncode != 0 and (
            "snap-update-ns" in stderr or "outside of /home" in stderr
        ):
            # Cause exacte : les navigateurs empaquetés en snap refusent un
            # répertoire personnel hors /home. Rejouer ce smoke exige un compte
            # dont le home est sous /home, ou un Chromium non-snap.
            pytest.skip(
                "Chromium est un snap et refuse un home hors /home "
                f"({Path.home()}) : smoke visuel non exécutable sur ce compte"
            )
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        data = screenshot.read_bytes()
        assert data.startswith(b"\x89PNG\r\n\x1a\n")
        width, height = struct.unpack(">II", data[16:24])
        assert width > 300
        assert height > 700
        assert len(data) > 10_000
    finally:
        server.shutdown()
        thread.join(timeout=2)
