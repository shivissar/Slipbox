"""
watcher.py — Keeps the SQLite index in sync with the notes/ directory.

Runs a watchdog observer in a background thread. On startup, performs a
full reconciliation: any .md file on disk is upserted, any DB row whose
file no longer exists is deleted.
"""

# Copyright (c) 2026 Shiv Issar
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from . import db
from . import notes


# Bump when upsert_note starts indexing something new, so the next start
# re-reads every note instead of trusting modification times.
INDEX_VERSION = "5.2"

# Note files Verweis couldn't read, by filename -> reason (5.2: these
# used to vanish from the list with only a console message).
PROBLEMS = {}


def full_resync(force=False):
    """Reconcile filesystem ↔ database. Idempotent. Files whose modification
    time matches the index are not re-read (5.2), unless `force` or the
    index was built by an older version."""
    db.init_db()
    trusted = not force and db.get_embed_meta("index_version") == INDEX_VERSION
    known = db.indexed_mtimes() if trusted else {}
    on_disk_ids = set()
    problems = {}
    for path in notes.iter_note_files():
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        hit = known.get(path.name)
        if hit and hit[1] == mtime:
            on_disk_ids.add(hit[0])
            continue
        try:
            note = notes.parse_file(path)
            db.upsert_note(note)
            on_disk_ids.add(note.id)
        except Exception as e:
            print(f"[watcher] skipped {notes.log_name(path)}: {type(e).__name__}")
            problems[path.name] = _reason(e)
    for stale_id in db.all_known_ids() - on_disk_ids:
        db.delete_note(stale_id)
    PROBLEMS.clear()
    PROBLEMS.update(problems)
    db.set_embed_meta("index_version", INDEX_VERSION)


def _reason(e):
    problem = getattr(e, "problem", None)  # a YAML error in the front matter
    if problem:
        hint = ""
        if any(s in problem for s in ("expected ','", "end of stream", "found unexpected end")):
            hint = " (often a bracket or quotation mark that isn’t closed)"
        elif "mapping values are not allowed" in problem:
            hint = " (often a colon in a title that needs quotation marks around it)"
        return f"the front matter can’t be read: {problem}{hint}"[:240]
    text = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
    return text[:200]


class NotesHandler(FileSystemEventHandler):
    def _handle(self, path_str):
        path = Path(path_str)
        if path.suffix != ".md":
            return
        # Only care about files directly in NOTES_DIR, not subdirs like .trash/
        try:
            if path.parent.resolve() != notes.NOTES_DIR.resolve():
                return
        except (OSError, RuntimeError):
            return
        try:
            if path.exists():
                try:
                    note = notes.parse_file(path)
                except Exception as e:
                    PROBLEMS[path.name] = _reason(e)
                    raise
                db.upsert_note(note)
                PROBLEMS.pop(path.name, None)
            else:
                PROBLEMS.pop(path.name, None)
                # Deleted: try to derive ID from filename
                stem = path.name.split("-", 1)[0]
                if stem.isdigit():
                    db.delete_note(stem)
        except Exception as e:
            print(f"[watcher] error on {notes.log_name(path)}: {type(e).__name__}")

    def on_created(self, event):
        if not event.is_directory:
            self._handle(event.src_path)

    def on_modified(self, event):
        if not event.is_directory:
            self._handle(event.src_path)

    def on_deleted(self, event):
        if not event.is_directory:
            self._handle(event.src_path)

    def on_moved(self, event):
        if not event.is_directory:
            self._handle(event.src_path)
            self._handle(event.dest_path)


def start_watcher():
    full_resync()
    observer = Observer()
    observer.schedule(NotesHandler(), str(notes.NOTES_DIR), recursive=False)
    observer.daemon = True
    observer.start()
    return observer
