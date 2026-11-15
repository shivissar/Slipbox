"""
importer.py — Bring Markdown notes from another folder into Slipbox (5.3).

Settings → Import notes. Slipbox reads only files whose names start with
a 14-digit ID, and links only in the form [[ID]], so Markdown from another
app (Obsidian, Zettlr, Bear, Notion, plain files) can't simply be dropped
into the notes folder. The import makes a Slipbox note from each file:

- copies only; the files in the chosen folder are never changed;
- each note gets an ID (the one in its file name if it has a free 14-digit
  one, else one made from the date the note was written), a Slipbox file
  name, a title (front matter, else its first heading, else the file
  name), its tags plus "imported", its dates, and the kind chosen;
- [[Title]], [[file name]], [[folder/file name]], [[name|words]],
  [[name#heading]] and Markdown links to .md files between the imported
  files become Slipbox links; ![[name]] becomes an embed. Links that can't
  be matched stay as they are and are listed;
- subfolders are included, hidden folders (.obsidian, .git, .trash) not;
- pictures and other attachments are not copied: references to them stay
  as text and are counted.

scan() works everything out without writing; run() writes the notes.
"""

# Copyright (c) 2026 Shiv Issar
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import os
import posixpath
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote

import frontmatter

from . import db
from . import history
from . import notes
from . import vault

SUFFIXES = (".md", ".markdown")
MAX_FILES = 5000
MAX_BYTES = 5 * 1024 * 1024
SKIP_DIRS = {"node_modules", "__MACOSX"}
TAG = "imported"
_ID_FMT = "%Y%m%d%H%M%S"
# Fields the import sets itself; anything else in a file's front matter is
# kept as it is.
_TAKEN_OVER = set(notes.OWN_FIELDS) | {"tag"}

_NUM_PREFIX_RE = re.compile(r"^(\d{12,14})(?!\d)")
_CODE_RE = re.compile(r"(```.*?```|~~~.*?~~~|`[^`\n]*`)", re.DOTALL)
_WIKI_RE = re.compile(r"(!?)\[\[([^\[\]\n|]+)(?:\|([^\[\]\n]*))?\]\]")
_MD_LINK_RE = re.compile(r'(!?)\[([^\]\n]*)\]\(\s*<?([^)\s>]+)>?(?:\s+"[^"\n]*")?\s*\)')
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_EXT_RE = re.compile(r"\.([A-Za-z0-9]{1,6})$")


class ImportProblem(Exception):
    """The import can't start; the message is shown to the person."""


@dataclass
class Item:
    src: Path
    rel: str
    title: str
    body: str
    tags: list
    created: datetime
    modified: datetime
    type: str
    refs: list
    extra: dict
    number: str = ""        # a 12-14 digit ID at the start of the file name
    aliases: list = field(default_factory=list)
    id: str = ""
    filename: str = ""


@dataclass
class Plan:
    folder: Path
    kind: str
    found: int = 0
    items: list = field(default_factory=list)
    skipped: list = field(default_factory=list)       # (file, reason)
    unmatched: list = field(default_factory=list)     # (file, link as written)
    converted: int = 0
    attachments: int = 0
    attachment_examples: list = field(default_factory=list)
    clashes: list = field(default_factory=list)       # titles already in Slipbox
    yaml_problems: list = field(default_factory=list)  # files whose front matter was kept as text
    threads_dropped: int = 0


def _key(text):
    return unicodedata.normalize("NFC", text or "").strip().casefold()


def resolve_folder(raw):
    """The folder to import from, checked, or ImportProblem."""
    raw = (raw or "").strip()
    if not raw:
        raise ImportProblem("Enter the folder to import from.")
    folder = Path(raw).expanduser()
    if not folder.is_absolute():
        raise ImportProblem("Enter the folder’s full path, starting with / or ~ (for example ~/Documents/Notes).")
    if not folder.is_dir():
        raise ImportProblem("Slipbox can’t find a folder at %s." % raw)
    folder = folder.resolve()
    if folder == notes.NOTES_DIR.resolve():
        raise ImportProblem("That is your notes folder: its notes are already in Slipbox. Choose the folder you want to bring in.")
    return folder


def _files(folder):
    notes_dir = notes.NOTES_DIR.resolve()
    out = []
    for dirpath, dirnames, filenames in os.walk(folder, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS)
        here = Path(dirpath)
        if here.resolve() == notes_dir:
            continue  # Slipbox's own notes, where the folder chosen contains it
        for name in sorted(filenames):
            if name.startswith(".") or not name.lower().endswith(SUFFIXES):
                continue
            out.append(here / name)
            if len(out) > MAX_FILES:
                raise ImportProblem(
                    "That folder holds more than %s Markdown files, counting its subfolders. "
                    "Choose a smaller folder, or import it a part at a time." % format(MAX_FILES, ","))
    return out


def _when(value):
    """A front-matter date as a local datetime, or None."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    elif isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    try:
        return dt.astimezone()  # a time without a zone is taken as local
    except (OverflowError, OSError, ValueError):
        return None


def _file_times(path):
    st = path.stat()
    born = getattr(st, "st_birthtime", None) or st.st_mtime
    return datetime.fromtimestamp(born).astimezone(), datetime.fromtimestamp(st.st_mtime).astimezone()


def _tags(meta):
    raw = []
    for key in ("tags", "tag"):
        v = meta.get(key)
        if v is None:
            continue
        if isinstance(v, (list, tuple)):
            raw += [str(x) for x in v if x is not None]
        else:
            s = str(v)
            raw += s.split(",") if "," in s else s.split()
    out, seen = [], set()
    for t in raw:
        t = re.sub(r"[\s/]+", "-", t.strip().lstrip("#").strip()).strip("-")
        if t and t.casefold() not in seen:
            seen.add(t.casefold())
            out.append(t)
    if TAG not in seen:
        out.append(TAG)
    return out


def _list(value):
    if isinstance(value, (list, tuple)):
        return [str(x) for x in value if x is not None and str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _title_from_name(stem):
    t = _NUM_PREFIX_RE.sub("", stem).strip(" _-") or stem
    return t.strip() or "Untitled"


def _leading_h1(body):
    """The text of a '# heading' that is the first line with anything on it."""
    for line in body.split("\n"):
        if line.strip():
            return re.sub(r"\s+", " ", line[2:]).strip() if line.startswith("# ") else ""
    return ""


def _drop_title_heading(body, title):
    """A first line '# Title' repeats the title Slipbox shows above the
    note, so it is left out."""
    lines = body.split("\n")
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].startswith("# ") and re.sub(r"\s+", " ", lines[i][2:]).strip() == title:
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        return "\n".join(lines[j:])
    return body


def _read(path, rel, plan):
    try:
        if path.stat().st_size > MAX_BYTES:
            plan.skipped.append((rel, "larger than 5 MB"))
            return None
        raw = path.read_bytes()
    except OSError as e:
        plan.skipped.append((rel, "couldn’t be read (%s)" % (e.strerror or type(e).__name__)))
        return None
    try:
        text = raw.decode("utf-8-sig").replace("\r\n", "\n")
    except UnicodeDecodeError:
        plan.skipped.append((rel, "not UTF-8 text"))
        return None
    if not text.strip():
        plan.skipped.append((rel, "empty"))
        return None
    if vault.looks_locked(text):
        plan.skipped.append((rel, "a locked Slipbox note: remove its lock in the Slipbox it came from, then import it"))
        return None
    try:
        post = frontmatter.loads(text)
        meta, body = dict(post.metadata or {}), post.content
    except Exception:
        meta, body = {}, text
        plan.yaml_problems.append(rel)

    created, modified = _file_times(path)
    for key in ("created", "date", "created_at"):
        dt = _when(meta.get(key))
        if dt:
            created = dt
            break
    for key in ("modified", "updated", "last_modified"):
        dt = _when(meta.get(key))
        if dt:
            modified = dt
            break
    now = datetime.now().astimezone()
    created, modified = min(created, now), min(modified, now)

    title = meta.get("title")
    title = re.sub(r"\s+", " ", str(title)).strip() if title not in (None, "") else ""
    title = title or _leading_h1(body) or _title_from_name(path.stem)
    body = _drop_title_heading(body, title)

    if meta.get("threads"):
        plan.threads_dropped += 1
    m = _NUM_PREFIX_RE.match(path.name)
    number = m.group(1) if m else ""
    fm_id = str(meta.get("id") or "")
    if not number and re.fullmatch(r"\d{14}", fm_id):
        number = fm_id
    note_type = meta.get("type") if meta.get("type") in ("zettel", "manuscript") else "zettel"
    return Item(
        src=path, rel=rel, title=title[:200], body=body, tags=_tags(meta),
        created=created, modified=modified, type=note_type,
        refs=_list(meta.get("refs")),
        extra={k: v for k, v in meta.items() if k not in _TAKEN_OVER},
        number=number, aliases=_list(meta.get("aliases")),
    )


def _taken_ids():
    taken = set(db.all_known_ids())
    for p in notes.NOTES_DIR.glob("*.md"):
        m = notes.ID_RE.match(p.name)
        if m:
            taken.add(m.group(1))
    trash = notes.NOTES_DIR / ".trash"
    if trash.is_dir():
        for p in trash.iterdir():
            m = notes.ID_RE.match(p.name)
            if m:
                taken.add(m.group(1))
    taken.update(history.ids_with_history())  # a deleted note's history
    return taken


def _assign_ids(items):
    """A file whose name starts with a free 14-digit ID keeps it, so [[ID]]
    links among the files still work; the others get an ID from the date
    they were written, moved on a second at a time until it's free."""
    taken = _taken_ids()
    for it in items:
        if len(it.number) == 14 and it.number not in taken:
            try:
                datetime.strptime(it.number, _ID_FMT)
            except ValueError:
                continue
            it.id = it.number
            taken.add(it.id)
    for it in sorted((i for i in items if not i.id), key=lambda i: (i.created, i.rel)):
        t = it.created.replace(tzinfo=None)
        nid = t.strftime(_ID_FMT)
        while nid in taken:
            t += timedelta(seconds=1)
            nid = t.strftime(_ID_FMT)
        it.id = nid
        taken.add(nid)
    for it in items:
        it.filename = notes.make_filename(it.id, it.title)


class _Links:
    """Finds which imported note a link points to."""

    def __init__(self, items):
        self.by_path, self.by_name, self.by_title, self.by_alias, self.by_number = {}, {}, {}, {}, {}
        for it in items:
            stem = posixpath.splitext(it.rel)[0]
            self.by_path[_key(stem)] = it
            self.by_name.setdefault(_key(posixpath.basename(stem)), []).append(it)
            self.by_title.setdefault(_key(it.title), []).append(it)
            for a in it.aliases:
                self.by_alias.setdefault(_key(a), []).append(it)
            if it.number:
                self.by_number.setdefault(it.number, []).append(it)

    @staticmethod
    def _one(hits):
        if not hits:
            return None
        return hits[0] if len(hits) == 1 else "ambiguous"

    def find(self, target, here):
        """An Item, "existing" (already a link to a note in Slipbox),
        "ambiguous", or None."""
        t = re.split(r"[#^]", target.strip(), maxsplit=1)[0].strip()
        if not t:
            return None
        low = t.lower()
        for ext in SUFFIXES:
            if low.endswith(ext):
                t = t[: -len(ext)]
                break
        if re.fullmatch(r"\d{12,14}", t):
            hit = self._one(self.by_number.get(t))
            if hit:
                return hit
            if len(t) == 14 and db.get_note_meta(t):
                return "existing"
        k = _key(t)
        if "/" in k:
            hit = self.by_path.get(k.lstrip("/")) or self.by_path.get(
                _key(posixpath.normpath(posixpath.join(here, t))))
            if hit:
                return hit
            k = k.rsplit("/", 1)[1]
        for table in (self.by_name, self.by_title, self.by_alias):
            hit = self._one(table.get(k))
            if hit:
                return hit
        return None


def _is_attachment(target):
    m = _EXT_RE.search(re.split(r"[#^|]", target.strip(), maxsplit=1)[0].strip())
    return bool(m) and ("." + m.group(1).lower()) not in SUFFIXES


def _link_text(nid, title, words):
    words = (words or "").replace("]", "").strip()
    if not words or _key(words) == _key(title):
        return "[[%s]]" % nid
    return "[[%s|%s]]" % (nid, words)


def _convert(item, links, plan):
    here = posixpath.dirname(item.rel)

    def note_attachment(text):
        plan.attachments += 1
        if len(plan.attachment_examples) < 5:
            plan.attachment_examples.append((item.rel, text))

    def wiki(m):
        bang, target, words = m.group(1), m.group(2), m.group(3)
        hit = links.find(target, here)
        if hit == "existing":
            return m.group(0)
        if hit is None or hit == "ambiguous":
            if _is_attachment(target):
                note_attachment(m.group(0))
            else:
                plan.unmatched.append((item.rel, m.group(0)))
            return m.group(0)
        plan.converted += 1
        if bang:
            return "![[%s]]" % hit.id
        if not words:
            # The words shown are the link's own: its name without any
            # folder, and a heading as "Name › Heading".
            name, _, heading = target.strip().partition("#")
            name = name.split("^", 1)[0].strip()
            if re.fullmatch(r"\d{12,14}", name):
                name = ""  # an ID: show the note's title
            words = posixpath.basename(name)
            heading = heading.split("^", 1)[0].strip()
            if heading and words:
                words += " › " + heading
        return _link_text(hit.id, hit.title, words)

    def md(m):
        bang, words, target = m.group(1), m.group(2), m.group(3)
        if _SCHEME_RE.match(target) or target.startswith("#"):
            return m.group(0)  # a web link, an e-mail address, a heading here
        path = unquote(target).split("#", 1)[0]
        if bang or (_EXT_RE.search(path) and not path.lower().endswith(SUFFIXES)):
            note_attachment(m.group(0))
            return m.group(0)
        if not path.lower().endswith(SUFFIXES):
            return m.group(0)
        stem = posixpath.splitext(posixpath.normpath(posixpath.join(here, path)))[0]
        hit = links.by_path.get(_key(stem.lstrip("/")))
        if hit is None:
            plan.unmatched.append((item.rel, m.group(0)))
            return m.group(0)
        plan.converted += 1
        return _link_text(hit.id, hit.title, words)

    parts = _CODE_RE.split(item.body)
    for i in range(0, len(parts), 2):  # odd parts are code: left alone
        parts[i] = _MD_LINK_RE.sub(md, _WIKI_RE.sub(wiki, parts[i]))
    item.body = "".join(parts)


def scan(raw_folder, kind):
    """Work out the import without writing anything. Returns a Plan, or
    raises ImportProblem."""
    folder = resolve_folder(raw_folder)
    plan = Plan(folder=folder, kind=notes.normalize_kind(kind))
    files = _files(folder)
    plan.found = len(files)
    for path in files:
        rel = path.relative_to(folder).as_posix()
        item = _read(path, rel, plan)
        if item:
            plan.items.append(item)
    _assign_ids(plan.items)
    links = _Links(plan.items)
    for item in plan.items:
        _convert(item, links, plan)
    existing = {_key(t) for t in db.all_titles()}
    plan.clashes = [it.title for it in plan.items if _key(it.title) in existing]
    return plan


def run(plan):
    """Write the planned notes. Returns (notes written, error message or "")."""
    written = []
    present = {m.group(1) for m in (notes.ID_RE.match(p.name) for p in notes.NOTES_DIR.glob("*.md")) if m}
    for it in plan.items:
        path = notes.NOTES_DIR / it.filename
        if path.exists() or it.id in present:
            plan.skipped.append((it.rel, "a note with the same ID appeared while importing"))
            continue
        post = frontmatter.Post(it.body.strip("\n") + "\n")
        post.metadata.update(it.extra)
        post.metadata.update(
            id=it.id, title=it.title,
            created=it.created.isoformat(timespec="seconds"),
            modified=it.modified.isoformat(timespec="seconds"),
            type=it.type, kind=plan.kind, tags=it.tags, threads=[], refs=it.refs,
        )
        try:
            notes._write_atomically(path, frontmatter.dumps(post))
            db.upsert_note(notes.parse_file(path))
        except Exception as e:
            return written, "Stopped at %s: %s" % (it.rel, e)
        written.append(it)
    return written, ""
