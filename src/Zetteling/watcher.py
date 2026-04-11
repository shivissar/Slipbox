"""
watcher.py — Keeps the SQLite index in sync with the notes/ directory.

Runs a watchdog observer in a background thread. On startup, performs a
full reconciliation: any .md file on disk is upserted, any DB row whose
file no longer exists is deleted.
"""

# Copyright (c) 2026 Shiv Issar
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from . import db
from . import notes


def full_resync():
    """Reconcile filesystem ↔ database. Idempotent."""
    db.init_db()
    on_disk_ids = set()
    for path in notes.iter_note_files():
        try:
            note = notes.parse_file(path)
            db.upsert_note(note)
            on_disk_ids.add(note.id)
        except Exception as e:
            print(f"[watcher] skipped {path.name}: {e}")
    for stale_id in db.all_known_ids() - on_disk_ids:
        db.delete_note(stale_id)


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
                note = notes.parse_file(path)
                db.upsert_note(note)
            else:
                # Deleted: try to derive ID from filename
                stem = path.name.split("-", 1)[0]
                if stem.isdigit():
                    db.delete_note(stem)
        except Exception as e:
            print(f"[watcher] error on {path.name}: {e}")

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
