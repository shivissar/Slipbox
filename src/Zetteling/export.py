"""
export.py — Pandoc-based export pipeline.

Takes a collection of notes (a single note, a thread chain, or a tag
group), concatenates their bodies into a single Markdown document, and
shells out to pandoc to convert to the requested format. If pandoc is
not installed, the feature is disabled gracefully — same pattern as
bib.py with bibtexparser.
"""

# Copyright (c) 2026 Shiv Issar
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import shutil
import stat
import re
import subprocess
import tempfile
from pathlib import Path

from . import db
from . import notes
from . import settings


def _find_pandoc():
    bundled = Path(__file__).parent / "resources" / "bin" / "pandoc"
    if bundled.exists():
        try:
            mode = bundled.stat().st_mode
            bundled.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        except OSError:
            pass
        return str(bundled)
    return shutil.which("pandoc")


PANDOC_PATH = _find_pandoc()
PANDOC_AVAILABLE = PANDOC_PATH is not None

# Formats we expose. The keys are pandoc's --to/--write values.
SUPPORTED_FORMATS = {
    "docx":     {"label": "Word (.docx)",       "ext": "docx", "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    "html":     {"label": "HTML",               "ext": "html", "mime": "text/html"},
    "latex":    {"label": "LaTeX (.tex)",       "ext": "tex",  "mime": "application/x-tex"},
    "markdown": {"label": "Markdown (.md)",     "ext": "md",   "mime": "text/markdown"},
    "plain":    {"label": "Plain text (.txt)",  "ext": "txt",  "mime": "text/plain"},
    "epub":     {"label": "EPUB",               "ext": "epub", "mime": "application/epub+zip"},
    "odt":      {"label": "OpenDocument (.odt)","ext": "odt",  "mime": "application/vnd.oasis.opendocument.text"},
}


def _strip_frontmatter(body):
    """Pandoc treats YAML front matter as document metadata, which can
    cause surprising title-page behavior. We strip it before passing to
    pandoc and let pandoc use the title from the first H1 heading."""
    lines = body.splitlines()
    if not lines or lines[0].strip() != "---":
        return body
    for i, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            return "\n".join(lines[i + 1:]).lstrip()
    return body


def _note_to_markdown(note, include_title=True):
    """Convert a Note to a Markdown chunk for inclusion in an export."""
    body = _strip_frontmatter(note.body)
    if include_title and not body.lstrip().startswith("# "):
        return f"# {note.title}\n\n{body}"
    return body


def _load_note(note_id):
    meta = db.get_note_meta(note_id)
    if not meta:
        return None
    path = notes.NOTES_DIR / meta["filename"]
    if not path.exists():
        return None
    return notes.parse_file(path)


def collect_notes_for_source(source_type, source_id):
    """Returns a list of Notes for a given source.
    source_type ∈ {'note', 'thread', 'tag'}, source_id is the id/name."""
    if source_type == "note":
        n = _load_note(source_id)
        return [n] if n else []
    if source_type == "thread":
        chain = db.thread_chain(source_id)
        out = []
        for entry in chain:
            n = _load_note(entry["id"])
            if n:
                out.append(n)
        return out
    if source_type == "tag":
        out = []
        for entry in db.notes_by_tag(source_id):
            n = _load_note(entry["id"])
            if n:
                out.append(n)
        return out
    return []


_EMBED_RE = re.compile(r"^[ \t]*!\[\[(\d{14})(?:\|([^\]\n]*))?\]\][ \t]*$", re.M)
_WIKILINK_RE = re.compile(r"\[\[(\d{14})(?:\|([^\]]+))?\]\]")


def _expand_embeds(body, embed_titles=False, depth=0, seen=None):
    """Replace ![[id]] lines with the embedded note's text (5.2), up to
    three levels deep, skipping cycles."""
    seen = set(seen or ())

    def repl(m):
        nid = m.group(1)
        if depth >= 3 or nid in seen:
            return ""
        n = _load_note(nid)
        if n is None or n.unreadable:
            return ""
        inner = _expand_embeds(_strip_frontmatter(n.body), embed_titles, depth + 1, seen | {nid})
        return (f"## {n.title}\n\n{inner}" if embed_titles else inner).strip() + "\n"
    return _EMBED_RE.sub(repl, body or "")


def embeds_unreadable(note, depth=0, seen=None):
    """True if a note embeds (at any depth an export would reach) a locked
    note that can't be read right now."""
    seen = set(seen or ()) | {note.id}
    if depth >= 3:
        return False
    for m in _EMBED_RE.finditer(note.body or ""):
        if m.group(1) in seen:
            continue
        n = _load_note(m.group(1))
        if n is None:
            continue
        if n.unreadable or embeds_unreadable(n, depth + 1, seen):
            return True
    return False


def text_for_ai(body, depth=0, seen=None):
    """A note body as an AI provider should see it (5.2): embedded notes
    written out under their titles, three levels deep, except locked ones,
    which are never sent; [[links]] as the linked note's title."""
    seen = set(seen or ())

    def repl(m):
        nid = m.group(1)
        if depth >= 3 or nid in seen:
            return ""
        n = _load_note(nid)
        if n is None:
            return "[A note that no longer exists was embedded here.]\n"
        if n.locked:
            return "[A locked note is embedded here. Its text is not shared.]\n"
        inner = text_for_ai(_strip_frontmatter(n.body), depth + 1, seen | {nid})
        return f"## {n.title}\n\n{inner}".strip() + "\n"
    expanded = _EMBED_RE.sub(repl, body or "")
    return _links_to_text(expanded) if depth == 0 else expanded


def locked_included(notes_list):
    """Locked notes whose text an export of notes_list would contain: the
    notes themselves and any locked note embedded in them (as far down as
    _expand_embeds goes). 5.2: the warning used to count only the former."""
    found, seen = {}, set()

    def visit(note, depth):
        if note.id in seen:
            return
        seen.add(note.id)
        if note.locked:
            found[note.id] = note.title
        if depth >= 3:
            return
        for m in _EMBED_RE.finditer(note.body or ""):
            n = _load_note(m.group(1))
            if n is not None and not n.unreadable:
                visit(n, depth + 1)
    for n in notes_list:
        visit(n, 0)
    return list(found.values())


def _links_to_text(md):
    """[[id|text]] -> text; [[id]] -> the linked note's title (5.2: these
    used to reach pandoc as raw brackets)."""
    def repl(m):
        if m.group(2):
            return m.group(2)
        meta = db.get_note_meta(m.group(1))
        return meta["title"] if meta else m.group(1)
    return _WIKILINK_RE.sub(repl, md)


def build_combined_markdown(notes_list, title=None, embed_titles=False, single=False):
    """Concatenate a list of Notes into a single Markdown document with
    an optional top-level title. Embedded notes are expanded and links
    become plain text. A single note (a manuscript, say) isn't given an
    extra heading: its title is the document title."""
    parts = []
    if title:
        parts.append(f"% {title}\n")
    for n in notes_list:
        body = _expand_embeds(_strip_frontmatter(n.body), embed_titles, seen={n.id})
        if not single and not body.lstrip().startswith("# "):
            body = f"# {n.title}\n\n{body}"
        parts.append(body)
        parts.append("")  # blank line between notes
    return _links_to_text("\n".join(parts))


def run_pandoc(markdown_text, fmt, citeproc=False):
    """Run pandoc on the given markdown text. Returns (output_bytes, error_str)."""
    if not PANDOC_AVAILABLE:
        return None, "pandoc is not installed on this system"
    if fmt not in SUPPORTED_FORMATS:
        return None, f"unsupported format: {fmt}"

    ext = SUPPORTED_FORMATS[fmt]["ext"]
    with tempfile.TemporaryDirectory() as tmpdir:
        in_path = Path(tmpdir) / "input.md"
        out_path = Path(tmpdir) / f"output.{ext}"
        in_path.write_text(markdown_text, encoding="utf-8")

        cmd = [PANDOC_PATH, str(in_path), "-o", str(out_path), "--from=markdown", f"--to={fmt}"]
        cmd += ["--standalone"]

        if citeproc:
            bib_path = settings.get_bib_path()
            if bib_path and Path(bib_path).exists():
                cmd += ["--citeproc", f"--bibliography={bib_path}"]

        try:
            result = subprocess.run(
                cmd, capture_output=True, timeout=30, check=False,
            )
        except subprocess.TimeoutExpired:
            return None, "pandoc timed out after 30 seconds"
        except Exception as e:
            return None, f"failed to run pandoc: {e}"

        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")
            return None, f"pandoc exited with code {result.returncode}: {stderr.strip()}"

        if not out_path.exists():
            return None, "pandoc produced no output file"

        return out_path.read_bytes(), None
