"""The Mac app shell (app.py) on Toga's dummy backend: the window shows a
loading page while the server starts, a start-up failure shows an error
page instead of crashing, and quitting asks the page to save first (5.2)."""
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("toga_dummy")
PROBE = Path(__file__).with_name("app_shell_probe.py")


@pytest.mark.parametrize("mode", ["normal", "broken"])
def test_shell(mode):
    r = subprocess.run([sys.executable, str(PROBE), mode], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and f"OK {mode}" in r.stdout, r.stdout[-2000:] + r.stderr[-3000:]
