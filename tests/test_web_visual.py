import shutil
import struct
import subprocess
import threading

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
        if result.returncode != 0 and (
            "snap-update-ns" in result.stderr.decode(errors="replace")
        ):
            pytest.skip("Chromium snap cannot enter its mount namespace")
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
