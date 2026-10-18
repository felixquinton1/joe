"""The default suite must never spend a developer's provider quota."""
import os
import shutil
import subprocess

import pytest


@pytest.mark.skipif(os.environ.get("JOE_LIVE_SMOKE") == "1", reason="explicit live suite")
def test_host_provider_discovery_and_execution_are_disabled():
    assert shutil.which("codex") is None
    assert shutil.which("claude") is None
    with pytest.raises(FileNotFoundError, match="Authenticated provider CLIs are disabled"):
        subprocess.run(["codex", "exec", "this command must never run"], check=False)
