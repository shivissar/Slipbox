"""
bib.py — Watches a BibTeX file (typically a Better BibTeX auto-export from
Zotero) and keeps the bib_entries table in sync.

The bib path is resolved by settings.get_bib_path(), which checks the
ZETTEL_BIB_PATH env var first and then the persistent settings file.
The watcher can be stopped and restarted at runtime via
restart_bib_watcher() — used by the /settings page when the user
changes the path through the UI.
"""

# Copyright (c) 2026 Shiv Issar
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import threading
from pathlib import Path

try:
    import bibtexparser
    BIBTEXPARSER_AVAILABLE = True
except ImportError:
    bibtexparser = None
    BIBTEXPARSER_AVAILABLE = False

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from . import db
from . import settings

_observer = None
_observer_lock = threading.Lock()


def _author_short(entry):
    """Extract a short 'Last, First; Last, First' author string."""
    raw = entry.get("author") or entry.get("editor") or ""
    parts = [p.strip() for p in raw.split(" and ") if p.strip()]
    return "; ".join(parts) if parts else ""


def parse_bib_file(path):
    """Parse a .bib file into a list of dicts ready for replace_bib_entries."""
    if not BIBTEXPARSER_AVAILABLE:
        return []
    path = Path(path)
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        try:
            bib_db = bibtexparser.load(f)
        except Exception as e:
            print(f"[bib] parse error in {path}: {e}")
            return []
    out = []
    for entry in bib_db.entries:
        citekey = entry.get("ID", "").strip()
        if not citekey:
            continue
        out.append({
            "citekey": citekey,
            "title": (entry.get("title") or "").strip("{}"),
            "author": _author_short(entry),
            "year": (entry.get("year") or "").strip(),
            "raw": str(entry),
        })
    return out


def reload_bib():
    """Re-parse the configured bib file and replace the index. Returns count."""
    path = settings.get_bib_path()
    if not path:
        db.replace_bib_entries([])
        return 0
    entries = parse_bib_file(path)
    db.replace_bib_entries(entries)
    print(f"[bib] indexed {len(entries)} entries from {path}")
    return len(entries)


class BibHandler(FileSystemEventHandler):
    def __init__(self, target_path):
        self.target = Path(target_path).resolve()
        self._debounce = None

    def _trigger(self):
        if self._debounce:
            self._debounce.cancel()
        self._debounce = threading.Timer(0.5, reload_bib)
        self._debounce.daemon = True
        self._debounce.start()

    def on_modified(self, event):
        if not event.is_directory and Path(event.src_path).resolve() == self.target:
            self._trigger()

    def on_created(self, event):
        if not event.is_directory and Path(event.src_path).resolve() == self.target:
            self._trigger()


def stop_bib_watcher():
    """Stop the active observer if any. Safe to call multiple times."""
    global _observer
    with _observer_lock:
        if _observer is not None:
            try:
                _observer.stop()
                _observer.join(timeout=2)
            except Exception as e:
                print(f"[bib] error stopping watcher: {e}")
            _observer = None


def start_bib_watcher():
    """Start the bib watcher if a bib path is configured. Returns the observer or None."""
    global _observer
    if not BIBTEXPARSER_AVAILABLE:
        print("[bib] bibtexparser not installed; bibliography feature inactive")
        print("[bib] to enable: pip install bibtexparser")
        return None

    path_str = settings.get_bib_path()
    if not path_str:
        print("[bib] no bib path configured; bibliography feature inactive")
        return None

    path = Path(path_str)
    if not path.exists():
        print(f"[bib] WARNING: {path_str} does not exist; will pick it up if created")

    reload_bib()

    parent = path.parent if path.parent.exists() else Path.cwd()
    obs = Observer()
    obs.schedule(BibHandler(str(path)), str(parent), recursive=False)
    obs.daemon = True
    obs.start()

    with _observer_lock:
        _observer = obs
    return obs


def restart_bib_watcher():
    """Stop any running watcher and start a fresh one with current settings.
    Used when the user changes the bib path via the /settings page."""
    stop_bib_watcher()
    return start_bib_watcher()
