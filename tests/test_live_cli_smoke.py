import json
import os
import subprocess

import pytest


@pytest.mark.skipif(
    os.environ.get("JOE_LIVE_SMOKE") != "1",
    reason="set JOE_LIVE_SMOKE=1 to contact installed provider CLIs",
)
def test_installed_provider_clis_answer_doctor_live(tmp_path):
    completed = subprocess.run(
        [
            "joe",
            "doctor",
            "--live",
            "--json",
            "-C",
            str(tmp_path),
        ],
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    report = json.loads(completed.stdout)
    assert all(
        provider["installed"] and provider["live"]["ok"]
        for provider in report["providers"]
    )
