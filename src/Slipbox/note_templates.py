"""
note_templates.py — Templates for new notes (5.2).

Templates are Markdown files in <notes folder>/.templates/, so they travel
with your notes. The frontmatter holds the template's name, a description,
and the new note's kind, tags and title; the body becomes the new note's
text. {{date}}, {{time}} and {{weekday}} are filled in when you use it.
The four built-in templates are copied in the first time templates are
used, and can be edited, deleted or restored from Settings.
"""

# Copyright (c) 2026 Shiv Issar
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import os
import re
import shutil
from datetime import datetime
from pathlib import Path

import frontmatter

DEFAULTS_DIR = Path(__file__).parent / "resources" / "templates"
KINDS = ("research", "journal", "teaching", "life")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}$")


def templates_dir():
    from . import notes
    return Path(notes.NOTES_DIR) / ".templates"


def ensure_defaults():
    """Create the templates folder with the built-ins the first time.
    Returns False if it can't be created (5.2: a read-only notes folder
    used to make every page that lists templates fail)."""
    d = templates_dir()
    if d.is_dir():
        return True
    try:
        d.mkdir(parents=True, exist_ok=True)
        for src in sorted(DEFAULTS_DIR.glob("*.md")):
            shutil.copyfile(src, d / src.name)
        return True
    except OSError as e:
        print("[templates] couldn't create the templates folder (%s); using the built-in ones" % e.strerror)
        return d.is_dir()


def _source_dir():
    """Where templates are read from: the notes folder's .templates, or the
    built-ins inside the app when that folder can't be made."""
    return templates_dir() if ensure_defaults() else DEFAULTS_DIR


def writable():
    return ensure_defaults()


def restore_defaults():
    """Copy back any built-in template that is missing. Returns how many."""
    d = templates_dir()
    d.mkdir(parents=True, exist_ok=True)
    n = 0
    for src in sorted(DEFAULTS_DIR.glob("*.md")):
        if not (d / src.name).exists():
            shutil.copyfile(src, d / src.name)
            n += 1
    return n


def slugify(name):
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:60]
    return s or "template"


def _path(slug):
    if not _SLUG_RE.match(slug or ""):
        return None
    return templates_dir() / f"{slug}.md"


def _load(path):
    post = frontmatter.load(path)
    fm = post.metadata or {}
    kind = str(fm.get("kind", "research")).lower()
    tags = fm.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    return {
        "slug": path.stem,
        "name": str(fm.get("template") or path.stem.replace("-", " ").capitalize()),
        "description": str(fm.get("description") or ""),
        "kind": kind if kind in KINDS else "research",
        "tags": [str(t) for t in tags],
        "title": str(fm.get("title") or ""),
        "body": post.content,
    }


def list_templates():
    out = []
    for p in sorted(_source_dir().glob("*.md")):
        try:
            out.append(_load(p))
        except Exception as e:  # a hand-edited file with broken frontmatter
            print("[templates] skipping %s: %s" % (p.name, e))
    return sorted(out, key=lambda t: t["name"].lower())


def get(slug):
    if not _SLUG_RE.match(slug or ""):
        return None
    p = _source_dir() / f"{slug}.md"
    if not p.exists():
        return None
    try:
        return _load(p)
    except Exception as e:  # broken frontmatter in a hand-edited file
        print("[templates] can't read %s: %s" % (p.name, e))
        return None


def fill(text, now=None):
    now = now or datetime.now()
    return (text.replace("{{date}}", now.strftime("%Y-%m-%d"))
                .replace("{{time}}", now.strftime("%H:%M"))
                .replace("{{weekday}}", now.strftime("%A")))


def render(slug, now=None):
    """The new-note fields a template produces, placeholders filled in."""
    t = get(slug)
    if not t:
        return None
    return {
        "slug": t["slug"],
        "name": t["name"],
        "title": fill(t["title"], now),
        "body": fill(t["body"], now),
        "kind": t["kind"],
        "tags": t["tags"],
    }


def save(slug, name, description, kind, tags, title, body):
    """Create (slug None) or update a template. Returns its slug."""
    ensure_defaults()
    name = (name or "").strip() or "Untitled template"
    if not slug:
        base = slugify(name)
        slug, i = base, 2
        while (templates_dir() / f"{slug}.md").exists():
            slug, i = f"{base}-{i}", i + 1
    p = _path(slug)
    if p is None:
        raise ValueError("Bad template name")
    post = frontmatter.Post(
        (body or "").replace("\r\n", "\n"),
        template=name,
        description=(description or "").strip(),
        kind=kind if kind in KINDS else "research",
        tags=[t.strip() for t in (tags or "").split(",") if t.strip()],
        title=(title or "").strip(),
    )
    tmp = p.with_suffix(".tmp")
    tmp.write_text(frontmatter.dumps(post) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return slug


def delete(slug):
    p = _path(slug)
    if p and p.exists():
        p.unlink()
        return True
    return False
