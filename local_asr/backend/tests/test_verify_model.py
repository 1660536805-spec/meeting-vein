import json
import subprocess
import sys
from pathlib import Path


def test_fake_model_verification_is_machine_readable():
    project_root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [sys.executable, "scripts/verify_model.py", "--fake"],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {
        "ok": True,
        "state": "ready",
        "device": "fake",
    }
