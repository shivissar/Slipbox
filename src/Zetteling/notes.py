"""
notes.py — The Note model and filesystem operations.

Notes are plain Markdown files with YAML frontmatter, stored in NOTES_DIR.
Filename convention: YYYYMMDDHHMM-short-slug.md
The digit prefix is the canonical ID.
"""

# Copyright (c) 2026 Shiv Issar
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import frontmatter

from . import settings
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
    return Note(
        id=note_id,
        filename=path.name,
        title=title,
        body=post.content,
        created=str(fm.get("created", "")),
        modified=str(fm.get("modified", "")),
        type=fm.get("type", "zettel"),
        kind=normalize_kind(fm.get("kind")),
        tags=list(fm.get("tags", []) or []),
        threads=list(fm.get("threads", []) or []),
        refs=list(fm.get("refs", []) or []),
        mtime=path.stat().st_mtime,
        outgoing_links=extract_wikilinks(post.content),
    )


def write_note(note):
    """Serialize a Note back to disk with updated frontmatter."""
    note.modified = now_iso()
    note.outgoing_links = extract_wikilinks(note.body)
    post = frontmatter.Post(
        note.body,
        id=note.id,
        title=note.title,
        created=note.created,
        modified=note.modified,
        type=note.type,
        kind=normalize_kind(note.kind),
        tags=note.tags,
        threads=note.threads,
        refs=note.refs,
    )
    note.path.write_text(frontmatter.dumps(post), encoding="utf-8")
    note.mtime = note.path.stat().st_mtime


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


def create_note(title, body="", tags=None, type_="zettel", kind=DEFAULT_KIND):
    note_id = now_id()
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
