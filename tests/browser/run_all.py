"""Run every browser suite, one process each, and summarise.

    python tests/browser/run_all.py            # all suites
    python tests/browser/run_all.py back tour  # some of them

Needs: pip install playwright && playwright install chromium (or CHROME=
/path/to/chrome), and pandoc on PATH for the export checks. The locked
suite waits 75 s for its idle-lock check."""
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SUITES = ["back", "links", "update", "tour", "autosave", "locked", "research"]


def main(names):
    failed = 0
    for name in names or SUITES:
        t = time.time()
        r = subprocess.run([sys.executable, os.path.join(HERE, f"suite_{name}.py")],
                           capture_output=True, text=True, timeout=600)
        out = r.stdout + r.stderr
        m = re.search(r"(\d+)/(\d+) passed", out)
        fails = [line for line in out.splitlines() if line.startswith("FAIL")]
        ok = r.returncode == 0 and m and m.group(1) == m.group(2)
        failed += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {name:9} {m.group(0) if m else 'no result'}  ({time.time() - t:.0f} s)")
        for line in fails:
            print("     " + line)
        if not m:
            print("\n".join("     " + line for line in out.strip().splitlines()[-15:]))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
