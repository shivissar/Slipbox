"""
notes.py — The Note model and filesystem operations.

Notes are plain Markdown files with YAML frontmatter, stored in NOTES_DIR.
Filename convention: YYYYMMDDHHMMSS-short-slug.md (14-digit timestamp)
The digit prefix is the canonical ID.
"""

# Copyright (c) 2026 Shiv Issar
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import os
import re
import stat
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import frontmatter

from . import settings
from . import vault
NOTES_DIR = Path(settings.get_notes_dir())
NOTES_DIR.mkdir(parents=True, exist_ok=True)

ID_RE = re.compile(r"^(\d{14})")
SLUG_RE = re.compile(r"[^a-z0-9]+")
WIKILINK_RE = re.compile(r"\[\[(\d{14})(?:\|([^\]]+))?\]\]")
CODE_BLOCK_RE = re.compile(r"```.*?```|`[^`\n]*`", re.DOTALL)


def extract_wikilinks(body):
    """Return the set of destination IDs referenced by [[id]] or [[id|alias]],
    ignoring matches inside fenced or inline code."""
    stripped = CODE_BLOCK_RE.sub("", body)
    return {m.group(1) for m in WIKILINK_RE.finditer(stripped)}


VALID_KINDS = ("research", "journal", "teaching", "life")
DEFAULT_KIND = "research"


def normalize_kind(k):
    """Coerce any kind value to a valid one, defaulting to research."""
    if not k:
        return DEFAULT_KIND
    k = str(k).strip().lower()
    return k if k in VALID_KINDS else DEFAULT_KIND


@dataclass
class Note:
    id: str
    filename: str
    title: str
    body: str
    created: str
    modified: str
    type: str = "zettel"
    kind: str = DEFAULT_KIND
    tags: list = field(default_factory=list)
    threads: list = field(default_factory=list)
    refs: list = field(default_factory=list)
    mtime: float = 0.0
    outgoing_links: set = field(default_factory=set)
    # 5.1 locked notes: the body is encrypted on disk (see vault.py).
    locked: bool = False
    cipher: str = ""          # the armoured body as read from disk
    unreadable: bool = False  # locked and not decrypted (closed or damaged)
    damaged: bool = False

    @property
    def path(self):
        return NOTES_DIR / self.filename


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def now_id():
    return datetime.now().strftime("%Y%m%d%H%M%S")


def slugify(text, maxlen=40):
    s = SLUG_RE.sub("-", text.lower()).strip("-")
    return s[:maxlen] or "untitled"


def make_filename(note_id, title):
    return f"{note_id}-{slugify(title)}.md"


def parse_file(path):
    """Read a markdown file from disk and return a Note."""
    path = Path(path)
    raw = path.read_text(encoding="utf-8")
    post = frontmatter.loads(raw)
    fm = post.metadata or {}

    # Derive ID from filename prefix; fall back to frontmatter id field.
    m = ID_RE.match(path.name)
    note_id = m.group(1) if m else str(fm.get("id", ""))
    if not note_id:
        raise ValueError(f"Cannot determine ID for {path.name}")

    title = fm.get("title") or _first_h1(post.content) or path.stem
    content = post.content
    locked = bool(fm.get("locked")) or vault.looks_locked(content)
    cipher, unreadable, damaged = "", False, False
    if locked:
        cipher = content
        content = ""
        if vault.is_unlocked():
            try:
                content = vault.decrypt_body(note_id, cipher)
            except vault.Damaged:
                unreadable = damaged = True
            except vault.VaultLocked:
                unreadable = True
        else:
            unreadable = True
    return Note(
        id=note_id,
        filename=path.name,
        title=title,
        body=content,
        created=str(fm.get("created", "")),
        modified=str(fm.get("modified", "")),
        type=fm.get("type", "zettel"),
        kind=normalize_kind(fm.get("kind")),
        tags=list(fm.get("tags", []) or []),
        threads=list(fm.get("threads", []) or []),
        refs=list(fm.get("refs", []) or []),
        mtime=path.stat().st_mtime,
        outgoing_links=extract_wikilinks(content),
        locked=locked,
        cipher=cipher,
        unreadable=unreadable,
        damaged=damaged,
    )


# 5.2: each note's modification time as Zetteling last wrote it, or as it
# was when the editor opened it. If the file has changed since (another app,
# a sync from another Mac), the next save keeps that outside version in the
# note's history first, whatever the 10-minute spacing of versions says;
# before, it could be overwritten without a copy.
_KNOWN_MTIME = {}


def log_name(path):
    """How a note file is named in console messages (5.2): its 14-digit ID,
    never the filename, which contains the title. In the Mac app these
    messages go to the system log, readable in Console.app."""
    stem = Path(path).name.split("-", 1)[0]
    return "note " + stem if stem.isdigit() and len(stem) == 14 else "a note file"


def remember(note):
    """Record the file's state when the editor opens it."""
    try:
        _KNOWN_MTIME[note.id] = note.path.stat().st_mtime
    except OSError:
        pass


def changed_outside(note):
    known = _KNOWN_MTIME.get(note.id)
    if known is None:
        return False
    try:
        return note.path.stat().st_mtime != known
    except OSError:
        return False


def write_note(note):
    """Serialize a Note back to disk with updated frontmatter.

    A locked note's body is encrypted before it touches the disk. If the
    note was read while locked notes were closed (unreadable), only its
    frontmatter changes and the encrypted body is written back untouched;
    a locked body is never written in plain text."""
    if note.locked:
        if note.unreadable:
            if not note.cipher:
                raise vault.VaultLocked("Locked notes are closed.")
            content = note.cipher
        else:
            content = vault.encrypt_body(note.id, note.body)  # raises if closed
            note.cipher = content
    else:
        content = note.body
    note.modified = now_iso()
    note.outgoing_links = set() if note.unreadable else extract_wikilinks(note.body)
    meta = {}
    if note.locked:
        meta["locked"] = True
    post = frontmatter.Post(
        content,
        id=note.id,
        title=note.title,
        created=note.created,
        modified=note.modified,
        type=note.type,
        kind=normalize_kind(note.kind),
        tags=note.tags,
        threads=note.threads,
        refs=note.refs,
        **meta,
    )
    note.kept_outside_version = False
    try:  # 5.2: keep the previous version (at most every 10 minutes)
        from . import history
        if changed_outside(note):
            note.kept_outside_version = history.snapshot(note.path, "external", force=True) is not None
        else:
            history.snapshot(note.path)
    except Exception as e:  # history must never block a save
        print("[history] snapshot failed: %s" % e)
    _write_atomically(note.path, frontmatter.dumps(post))
    note.mtime = note.path.stat().st_mtime
    _KNOWN_MTIME[note.id] = note.mtime


def _write_atomically(path, text):
    """Write the whole file or nothing (5.2). The text goes to a hidden
    temporary file beside the note, is flushed to disk, then replaces the
    note in one step, so a crash or a full disk can't leave half a note,
    and the file watcher never reads a half-written one."""
    path = Path(path)
    tmp = path.with_name(".%s.%d.tmp" % (path.name, os.getpid()))
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp, stat.S_IMODE(os.stat(path).st_mode))
        except OSError:
            pass  # a new note: keep the default permissions
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def soft_delete(note):
    """Move a note to NOTES_DIR/.trash/ so it disappears from the index
    but remains recoverable via Finder/mv. A timestamp suffix prevents
    collisions if the same filename is deleted twice."""
    from datetime import datetime
    trash = NOTES_DIR / ".trash"
    trash.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    dest = trash / f"{note.filename}.{stamp}.deleted"
    note.path.rename(dest)
    return dest


_TRASHED_RE = re.compile(r"^(\d{14})-.*\.md\.\d{14}\.deleted$")


def trashed():
    """[(path, note_id)] for every note deleted into .trash by Zetteling."""
    trash = NOTES_DIR / ".trash"
    if not trash.is_dir():
        return []
    out = []
    for p in sorted(trash.iterdir()):
        m = _TRASHED_RE.match(p.name)
        if m and p.is_file():
            out.append((p, m.group(1)))
    return out


def find_trashed(note_id):
    """The most recently trashed file for this note ID, or None."""
    hits = [p for p, nid in trashed() if nid == note_id]
    return max(hits, key=lambda p: p.name.rsplit(".", 2)[-2]) if hits else None


def restore_from_trash(note_id):
    """Move the most recent trashed copy of a note back into the notes
    folder under its old filename (5.2). Returns the restored path.
    Raises FileNotFoundError, or FileExistsError if that name is taken."""
    src = find_trashed(note_id)
    if src is None:
        raise FileNotFoundError(note_id)
    dest = NOTES_DIR / src.name.rsplit(".", 2)[0]
    if dest.exists() or any(NOTES_DIR.glob(f"{note_id}*.md")):
        raise FileExistsError(dest.name)
    src.rename(dest)
    return dest


def _unused_id(note_id):
    """Note IDs are 14-digit timestamps. Two notes made in the same second
    used to share one; move to the next free second instead (5.2)."""
    from datetime import datetime, timedelta
    while any(NOTES_DIR.glob(f"{note_id}*.md")):
        t = datetime.strptime(note_id, "%Y%m%d%H%M%S") + timedelta(seconds=1)
        note_id = t.strftime("%Y%m%d%H%M%S")
    return note_id


def create_note(title, body="", tags=None, type_="zettel", kind=DEFAULT_KIND, locked=False):
    note_id = _unused_id(now_id())
    # Avoid collision if two notes are made in the same minute
    base = make_filename(note_id, title)
    candidate = NOTES_DIR / base
    suffix = 1
    while candidate.exists():
        candidate = NOTES_DIR / f"{note_id}-{suffix}-{slugify(title)}.md"
        suffix += 1

    note = Note(
        id=note_id,
        filename=candidate.name,
        title=title,
        body=body,
        created=now_iso(),
        modified=now_iso(),
        type=type_,
        kind=normalize_kind(kind),
        tags=list(tags or []),
        locked=bool(locked),
    )
    write_note(note)
    return note


def iter_note_files():
    return sorted(NOTES_DIR.glob("*.md"))


def _first_h1(body):
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None
