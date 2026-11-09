# Browser tests

Seven Playwright suites that drive Slipbox's pages in Chromium against a
copy of a small fixture library (`fixture/`: nine notes, a BibTeX file and
settings). Each run copies the fixture to a temporary folder, so nothing
outside it is touched.

| Suite | Checks |
| --- | --- |
| `suite_back.py` | Back button and esc on Help, Export and Open beside; recovery-key file |
| `suite_links.py` | Outside links open in the browser, never in the app window |
| `suite_update.py` | The update check and its page |
| `suite_tour.py` | The welcome tour |
| `suite_autosave.py` | Autosave, the last save on leaving, saving when the app quits |
| `suite_locked.py` | Locked notes: set-up, lock, open/closed, inline unlock, kind sweep, idle lock |
| `suite_research.py` | History, backlinks, templates, coding, manuscripts, search filters |

## Running

```sh
pip install playwright && playwright install chromium
python tests/browser/run_all.py          # all suites, about four minutes
python tests/browser/run_all.py tour     # one suite
```

Set `CHROME=/path/to/chrome` to use an installed Chrome instead of
Playwright's Chromium, and `PANDOC=/path/to/pandoc` if pandoc isn't on your
PATH (on a Mac the bundled one is used). Screenshots go to
`tests/browser/screenshots/`, which git ignores.

These don't run under `pytest tests/` (they start a server and a browser at
import); the unit tests there are quick and need neither.
