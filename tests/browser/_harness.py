"""Shared set-up for the browser suites: a fresh copy of the fixture
library per run, Chrome or Playwright's Chromium, pandoc, and a folder
for screenshots. Paths come from this file's location, so the suites run
from any checkout."""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixture")
SRC_DIR = os.path.abspath(os.path.join(HERE, "..", "..", "src"))
SHOTS = os.environ.get("ZETTEL_TEST_SHOTS") or os.path.join(HERE, "screenshots")
os.makedirs(SHOTS, exist_ok=True)

# The repository's bundled pandoc is the macOS binary; on other systems
# use the one on PATH (or set PANDOC=/path/to/pandoc).
PANDOC = os.environ.get("PANDOC") or shutil.which("pandoc")
if not PANDOC and sys.platform == "darwin":
    _bundled = os.path.join(SRC_DIR, "Verweis", "resources", "bin", "pandoc")
    PANDOC = _bundled if os.path.exists(_bundled) else None


def prepare(name):
    """Copy the fixture library to a new temporary folder; return its data
    folder. settings.json is pointed at the copy's bibliography and an
    exports folder inside it, so nothing is written outside."""
    root = tempfile.mkdtemp(prefix=name + "-")
    data = os.path.join(root, "data")
    shutil.copytree(FIXTURE, data)
    path = os.path.join(data, "settings.json")
    with open(path) as f:
        s = json.load(f)
    s["bib_path"] = os.path.join(data, "library.bib")
    s["export_dir"] = os.path.join(data, "exports")
    with open(path, "w") as f:
        json.dump(s, f, indent=2)
    return data


def launch(p):
    """Chrome if CHROME points at it (or /usr/bin/google-chrome exists),
    else Playwright's own Chromium (`playwright install chromium`)."""
    exe = os.environ.get("CHROME") or ("/usr/bin/google-chrome" if os.path.exists("/usr/bin/google-chrome") else None)
    kw = {"args": ["--no-sandbox"]}
    if exe:
        kw["executable_path"] = exe
    return p.chromium.launch(**kw)
