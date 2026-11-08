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

import re

# Kept for compatibility with flask_app.py; always True since the parser
# is now in-house with no external dependency.
BIBTEXPARSER_AVAILABLE = True

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from . import db
from . import settings

_observer = None
_observer_lock = threading.Lock()


_ENTRY_RE = re.compile(r'@(\w+)\s*\{\s*([^,\s]+)\s*,', re.IGNORECASE)


def _strip_braces(s):
    s = s.strip()
    while s.startswith('{') and s.endswith('}'):
        s = s[1:-1].strip()
    return s


def _split_fields(body):
    fields = {}
    i, n = 0, len(body)
    while i < n:
        m = re.match(r'\s*(\w+)\s*=\s*', body[i:])
        if not m:
            i += 1
            continue
        key = m.group(1).lower()
        i += m.end()
        if i >= n:
            break
        if body[i] == '{':
            depth, j = 1, i + 1
            while j < n and depth > 0:
                if body[j] == '{': depth += 1
                elif body[j] == '}': depth -= 1
                j += 1
            val = body[i+1:j-1]
            i = j
        elif body[i] == '"':
            j = i + 1
            while j < n and body[j] != '"':
                j += 1
            val = body[i+1:j]
            i = j + 1
        else:
            j = i
            while j < n and body[j] not in ',\n':
                j += 1
            val = body[i:j].strip()
            i = j
        fields[key] = val
        while i < n and body[i] in ', \t\n\r':
            i += 1
    return fields


def parse_bib_file(path):
    """Parse a .bib file into a list of dicts. Minimal in-house parser — handles
    the Better BibTeX export subset Verweis needs (citekey, title, author/editor,
    year). No external dependencies."""
    path = Path(path)
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        print(f"[bib] read error in {path}: {e}")
        return []
    out = []
    i = 0
    while i < len(text):
        m = _ENTRY_RE.search(text, i)
        if not m:
            break
        citekey = m.group(2).strip()
        depth, j = 1, m.end()
        while j < len(text) and depth > 0:
            if text[j] == '{': depth += 1
            elif text[j] == '}': depth -= 1
            j += 1
        body = text[m.end():j-1]
        fields = _split_fields(body)
        entry_type = m.group(1).lower()
        if citekey:
            author_raw = fields.get("author") or fields.get("editor") or ""
            parts = [p.strip() for p in author_raw.split(" and ") if p.strip()]
            out.append({
                "citekey": citekey,
                "title": _strip_braces(fields.get("title", "")),
                "author": "; ".join(parts),
                "year": _strip_braces(fields.get("year", "")),
                "raw": f"@{entry_type}{{{citekey}, ...}}",
            })
        i = j
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
