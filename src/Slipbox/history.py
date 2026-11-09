"""
history.py — Saved versions of notes (5.2).

Before a note's file is overwritten, a copy of the file as it stood is kept
in <app data>/history/<note id>/, at most once every INTERVAL seconds while
you edit, and always before a restore, lock or bulk change. A locked note's
file is encrypted on disk, so its versions are copies of encrypted files;
when a note is locked, its earlier plain-text versions are encrypted too.
"""

# Copyright (c) 2026 Shiv Issar
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import difflib
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import frontmatter

INTERVAL = 600        # seconds between automatic versions while editing
MAX_VERSIONS = 200    # per note; the oldest go first

REASONS = {
    "edit": "Saved while editing",
    "restore": "Before a restore",
    "lock": "Before locking",
    "unlock": "Before removing the lock",
    "link": "Before adding a link",
    "rename-code": "Before renaming a code",
    "outline": "Before reordering",
    "external": "Before an outside change",
}
_NAME_RE = re.compile(r"^(\d{14})-v(\d{8}T\d{9})-([a-z-]+)\.md$")


def history_dir():
    from . import db
    return Path(db.DB_PATH).parent / "history"


def _note_dir(note_id):
    return history_dir() / note_id


def _versions(note_id):
    d = _note_dir(note_id)
    if not d.is_dir():
        return []
    return sorted((p for p in d.iterdir() if _NAME_RE.match(p.name)), key=lambda p: p.name)


def snapshot(path, reason="edit", force=False):
    """Keep a copy of the note file at `path` as it is now. Returns the new
    version's path, or None if nothing was saved."""
    path = Path(path)
    if not path.exists():
        return None
    m = re.match(r"^(\d{14})", path.name)
    if not m:
        return None
    note_id = m.group(1)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    existing = _versions(note_id)
    if existing:
        last = existing[-1]
        if not force and time.time() - last.stat().st_mtime < INTERVAL:
            return None
        try:
            if last.read_text(encoding="utf-8") == text:
                return None
        except OSError:
            pass
    d = _note_dir(note_id)
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")[:-3]
    reason = reason if reason in REASONS else "edit"
    dest = d / f"{note_id}-v{stamp}-{reason}.md"
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, dest)
    for old in _versions(note_id)[:-MAX_VERSIONS]:
        try:
            old.unlink()
        except OSError:
            pass
    return dest


def list_versions(note_id):
    from . import vault
    out = []
    for p in reversed(_versions(note_id)):
        m = _NAME_RE.match(p.name)
        when = datetime.strptime(m.group(2), "%Y%m%dT%H%M%S%f").replace(tzinfo=timezone.utc).astimezone()
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        out.append({
            "name": p.name,
            "when": when,
            "reason": REASONS.get(m.group(3), m.group(3)),
            "locked": vault.looks_locked(text),
            "chars": len(text),
        })
    return out


def version_path(note_id, name):
    """Resolve a version name safely (no path tricks)."""
    if not _NAME_RE.match(name or "") or not name.startswith(note_id + "-"):
        return None
    p = _note_dir(note_id) / name
    return p if p.exists() else None


def read_version(note_id, name):
    from . import notes
    p = version_path(note_id, name)
    return notes.parse_file(p) if p else None


def diff_lines(old_text, new_text, context=2):
    """A line diff from old to new, as (kind, text) pairs for display.
    kind is one of: 'ctx', 'add', 'del', 'gap'."""
    old = (old_text or "").splitlines()
    new = (new_text or "").splitlines()
    out = []
    for group in difflib.SequenceMatcher(None, old, new).get_grouped_opcodes(context):
        if out:
            out.append(("gap", "…"))
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                out.extend(("ctx", line) for line in old[i1:i2])
            else:
                out.extend(("del", line) for line in old[i1:i2])
                out.extend(("add", line) for line in new[j1:j2])
    return out


def encrypt_versions(note_id):
    """After a note is locked: encrypt the body of its plain-text versions
    (locked notes are open at this point). Returns how many were changed."""
    from . import vault
    count = 0
    for p in _versions(note_id):
        try:
            text = p.read_text(encoding="utf-8")
            if vault.looks_locked(text):
                continue
            post = frontmatter.loads(text)
            post.content = vault.encrypt_body(note_id, post.content)
            post.metadata["locked"] = True
            tmp = p.with_suffix(".tmp")
            tmp.write_text(frontmatter.dumps(post), encoding="utf-8")
            os.replace(tmp, p)
            count += 1
        except (OSError, ValueError) as e:
            print("[history] couldn't encrypt %s: %s" % (p.name, e))
    return count


def plain_versions(note_id):
    """How many of a note's versions are stored in plain text."""
    from . import vault
    n = 0
    for p in _versions(note_id):
        try:
            if not vault.looks_locked(p.read_text(encoding="utf-8")):
                n += 1
        except OSError:
            pass
    return n


# ---------- Housekeeping (5.2) ---------- #
# Versions used to accumulate for ever, including for notes long deleted.
# Nothing is removed automatically: Settings → Trash and history offers to
# empty the trash (and the history of the notes in it) and to remove the
# history of notes that are no longer anywhere in the notes folder.

def ids_with_history():
    d = history_dir()
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir() and re.fullmatch(r"\d{14}", p.name))


def usage(note_ids):
    """(number of versions, bytes) kept for these notes."""
    count = size = 0
    for nid in note_ids:
        for p in _versions(nid):
            count += 1
            try:
                size += p.stat().st_size
            except OSError:
                pass
    return count, size


def delete_history(note_id):
    import shutil
    d = _note_dir(note_id)
    if d.is_dir() and re.fullmatch(r"\d{14}", note_id):
        shutil.rmtree(d, ignore_errors=True)
