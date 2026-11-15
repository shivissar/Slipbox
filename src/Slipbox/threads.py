"""
threads.py — Putting existing notes into threads, taking them out, and
moving them along (5.3).

A thread is a named sequence of notes. Each member note's front matter
holds an entry {name, prev, next} for every thread it is in, so the
sequence is a chain of links from note to note. Before 5.3 a note could
join a thread only when it was created, at the end.

Every change here works out the thread's new order, then rewrites the
prev/next of each member whose entry differs, so a chain that was broken
(by a note deleted in Finder, say) is mended as a side effect. Locked
notes can be moved too: only their front matter changes, and their
encrypted text is written back as it was.
"""

# Copyright (c) 2026 Shiv Issar
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import re

from . import db
from . import notes

NAME_MAX = 80


class ThreadError(Exception):
    """A change that can't be made; the message is shown to the person."""


def clean_name(raw):
    """A thread name as typed, tidied, or ThreadError."""
    name = re.sub(r"\s+", " ", raw or "").strip()
    if not name:
        raise ThreadError("Give the new thread a name.")
    if "/" in name:
        raise ThreadError("A thread name can’t contain a slash (/).")
    if len(name) > NAME_MAX:
        raise ThreadError("Keep the thread’s name under %d characters." % NAME_MAX)
    return name


def order(name):
    """The thread's note IDs, first to last, leaving out any whose file is
    gone (the next change then joins up the notes either side)."""
    out = []
    for c in db.thread_chain(name):
        meta = db.get_note_meta(c["id"])
        if meta and (notes.NOTES_DIR / meta["filename"]).exists():
            out.append(c["id"])
    return out


def _load(note_id):
    meta = db.get_note_meta(note_id)
    path = notes.NOTES_DIR / meta["filename"] if meta else None
    if path is None or not path.exists():
        raise ThreadError("A note in this thread can’t be found. Reload the page and try again.")
    return notes.parse_file(path)


def _id(value):
    return str(value) if value else None


def _write(name, new_order, removed=()):
    """Give every note in new_order the right neighbours in thread `name`,
    and take the thread out of the notes in `removed`. Returns the IDs of
    the notes rewritten."""
    changed = []
    for i, nid in enumerate(new_order):
        prev_id = new_order[i - 1] if i else None
        next_id = new_order[i + 1] if i + 1 < len(new_order) else None
        note = _load(nid)
        entries = [t for t in note.threads if isinstance(t, dict) and t.get("name") == name]
        if not entries:
            note.threads.append({"name": name, "prev": prev_id, "next": next_id})
        elif len(entries) == 1 and _id(entries[0].get("prev")) == prev_id and _id(entries[0].get("next")) == next_id:
            continue
        else:
            # Keep one entry for the thread (a hand-edited file may have two).
            first = entries[0]
            note.threads = [t for t in note.threads if t is first or t not in entries]
            first["prev"], first["next"] = prev_id, next_id
        notes.write_note(note)
        db.upsert_note(note)
        changed.append(nid)
    for nid in removed:
        note = _load(nid)
        kept = [t for t in note.threads if not (isinstance(t, dict) and t.get("name") == name)]
        if len(kept) != len(note.threads):
            note.threads = kept
            notes.write_note(note)
            db.upsert_note(note)
            changed.append(nid)
    return changed


def add(note_id, name, after=None):
    """Put a note into thread `name`: after the note `after`, at the start
    if after is "start", else at the end. A name no thread has yet starts
    a new thread with this note."""
    if not db.get_note_meta(note_id):
        raise ThreadError("That note can’t be found.")
    current = order(name)
    if note_id in current:
        raise ThreadError("This note is already in “%s”." % name)
    if after == "start":
        at = 0
    elif after and after in current:
        at = current.index(after) + 1
    else:
        at = len(current)
    _write(name, current[:at] + [note_id] + current[at:])
    return at + 1, len(current) + 1


def remove(note_id, name):
    """Take a note out of a thread; the notes either side are joined up.
    Returns how many notes are left in the thread."""
    current = order(name)
    if note_id not in current:
        raise ThreadError("This note isn’t in “%s”." % name)
    rest = [i for i in current if i != note_id]
    _write(name, rest, removed=[note_id])
    return len(rest)


def move(note_id, name, step):
    """Move a note one place earlier (step -1) or later (+1)."""
    current = order(name)
    if note_id not in current:
        raise ThreadError("This note isn’t in “%s”." % name)
    i = current.index(note_id)
    j = i + (1 if step > 0 else -1)
    if not 0 <= j < len(current):
        return i + 1
    current[i], current[j] = current[j], current[i]
    _write(name, current)
    return j + 1
