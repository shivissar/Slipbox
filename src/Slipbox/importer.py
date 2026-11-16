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
  [[name#heading]] and Markdown links to other imported files become
  Slipbox links; ![[name]] on a line of its own becomes an embed. Links
  that can't be matched stay as they are and are listed;
- subfolders are included, hidden folders (.obsidian, .git, .trash) not;
- pictures and other attachments are not copied: references to them stay
  as text and are counted;
- front-matter fields Slipbox doesn't use are kept as they are; a field
  whose name Slipbox uses for something else (id, type, kind) is kept
  under "source-id" and so on, so nothing a file held is dropped.

scan() works everything out without writing; run() writes the notes.
"""

# Copyright (c) 2026 Shiv Issar
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import errno
import os
import posixpath
import re
import stat
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urlparse

import frontmatter
import yaml

from . import db, history, notes, vault

SUFFIXES = (".md", ".markdown")
# One import. The Mac app's web view gives a page 60 seconds to answer, and
# 2,000 files take well under that on an ordinary Mac.
MAX_FILES = 2000
MAX_BYTES = 5 * 1024 * 1024
SKIP_DIRS = {"node_modules", "__MACOSX"}
TAG = "imported"
_ID_FMT = "%Y%m%d%H%M%S"
# Fields the import sets itself; anything else in a file's front matter is
# kept as it is.
_TAKEN_OVER = set(notes.OWN_FIELDS) | {"tag"}
# The library Slipbox reads note files with fails on a front-matter field
# with either of these names, so a note holding one could never be opened
# again. They are kept as source-content and source-handler.
_UNREADABLE = ("content", "handler")

_NUM_PREFIX_RE = re.compile(r"^(\d{12,14})(?!\d)")
_CODE_RE = re.compile(r"(```.*?```|~~~.*?~~~|`[^`\n]*`)", re.DOTALL)
_WIKI_RE = re.compile(r"(!?)\[\[([^\[\]\n|]+)(?:\|([^\[\]\n]*))?\]\]")
# [words](path), [words](<path with spaces>), with an optional "title".
_MD_LINK_RE = re.compile(
    r"(!?)\[([^\]\n]*)\]\(\s*(?:<([^<>\n]*)>|([^)\s]+))"
    r"(?:\s+(?:\"[^\"\n]*\"|'[^'\n]*'))?\s*\)")
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_EXT_RE = re.compile(r"\.([A-Za-z0-9]{1,6})$")
# What a [[link]] must end in to count as a picture or other file. A name
# that merely has a dot in it ([[Chapter 1.2]]) is a note.
_ATTACHMENT_EXTS = frozenset("""
    png jpg jpeg gif svg webp bmp tif tiff heic heif avif ico
    pdf doc docx rtf odt pages txt xls xlsx csv tsv ods numbers ppt pptx odp key
    mp3 wav m4a ogg oga flac aac aiff mp4 mov m4v mkv webm avi
    zip gz tar epub html htm json xml yaml yml bib tex canvas excalidraw base
""".split())
_FM_OPEN_RE = re.compile(r"-{3,}[ \t]*\n")
_FM_CLOSE_RE = re.compile(r"^-{3,}[ \t]*$", re.MULTILINE)
# A file in iCloud Drive that isn't on this Mac (macOS 11 to 13) is a
# hidden stub: ".Name.md.icloud".
_CLOUD_STUB_RE = re.compile(r"^\.(.+\.(?:md|markdown))\.icloud$", re.IGNORECASE)
# A tag written in the text: #idea, #field/site (not a heading, a link to
# a heading, or part of a web address).
_INLINE_TAG_RE = re.compile(r"(?<![\w/#&(\[])#[A-Za-z][\w/-]*")
_HEX_COLOUR_RE = re.compile(r"#(?:[0-9a-fA-F]{3}){1,2}")


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
    fm_id: object = None    # an "id" field in the file's front matter
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
    yaml_problems: list = field(default_factory=list)  # files whose opening block was kept as text
    threads_dropped: int = 0
    unreadable: list = field(default_factory=list)    # (subfolder, why) that couldn't be opened
    cloud_only: int = 0                               # files in iCloud, not on this Mac
    cloud_examples: list = field(default_factory=list)
    renamed: dict = field(default_factory=dict)       # field name -> files where it was kept as source-<name>
    inline_tags: int = 0                              # files with #tags written in their text
    table_links: int = 0                              # links in tables that lost their own words
    embeds_as_links: int = 0                          # ![[...]] that Slipbox can't embed

    @property
    def other_fields(self):
        """Names of the fields from other apps that the notes carry."""
        return sorted({k for it in self.items for k in it.extra}, key=str.casefold)


def _key(text):
    return unicodedata.normalize("NFC", text or "").strip().casefold()


def _cant_read(shown, err):
    """What to say when a folder can't be opened. macOS answers "Operation
    not permitted" when the person hasn't allowed the app to use a folder
    it guards (Desktop, Documents, Downloads, iCloud Drive, other disks)."""
    if err.errno == errno.EPERM:
        return ("macOS didn’t let Slipbox read %s. If macOS asked whether Slipbox may use that "
                "folder and the answer was Don’t Allow, turn it on in System Settings → Privacy & "
                "Security → Files and Folders → Slipbox, then check the folder again." % shown)
    if err.errno == errno.EACCES:
        return ("Slipbox doesn’t have permission to open %s. In Finder, select the folder, choose "
                "File → Get Info, and look under Sharing & Permissions." % shown)
    return "Slipbox couldn’t read %s (%s)." % (shown, err.strerror or type(err).__name__)


def _typed_path(raw):
    """The path as pasted, tidied: without quotation marks around it, as a
    plain path if it's a file:// address, and with Terminal's backslashes
    before spaces removed when that is what makes it exist."""
    raw = (raw or "").strip()
    if len(raw) >= 2 and raw[0] in "\"'“‘" and raw[-1] in "\"'”’":
        raw = raw[1:-1].strip()
    if raw.lower().startswith("file://"):
        raw = unquote(urlparse(raw).path)
    if "\\" in raw and not os.path.exists(os.path.expanduser(raw)):
        plain = re.sub(r"\\(.)", r"\1", raw)
        if os.path.exists(os.path.expanduser(plain)):
            raw = plain
    return raw


def resolve_folder(raw):
    """The folder to import from, checked, or ImportProblem."""
    raw = _typed_path(raw)
    if not raw:
        raise ImportProblem("Enter the folder to import from.")
    folder = Path(raw).expanduser()
    if not folder.is_absolute():
        raise ImportProblem("Enter the folder’s full path, starting with / or ~ (for example ~/Documents/Notes).")
    try:
        mode = os.stat(folder).st_mode
    except PermissionError as e:
        raise ImportProblem(_cant_read(raw, e))
    except OSError:
        raise ImportProblem("Slipbox can’t find a folder at %s." % raw)
    if not stat.S_ISDIR(mode):
        raise ImportProblem("%s is a file, not a folder. Enter the folder it is in: Slipbox shows "
                            "every Markdown file there before importing anything." % raw)
    folder = folder.resolve()
    if folder == notes.NOTES_DIR.resolve():
        raise ImportProblem("That is your notes folder: its notes are already in Slipbox. Choose the folder you want to bring in.")
    return folder


def _files(folder, plan):
    notes_dir = notes.NOTES_DIR.resolve()
    out = []

    def failed(err):
        where = Path(err.filename) if err.filename else folder
        if where == folder:
            raise ImportProblem(_cant_read(str(folder), err))
        try:
            shown = where.relative_to(folder).as_posix()
        except ValueError:
            shown = str(where)
        plan.unreadable.append((shown, err.errno))

    for dirpath, dirnames, filenames in os.walk(folder, onerror=failed, followlinks=False):
        here = Path(dirpath)
        if here.resolve() == notes_dir:
            dirnames[:] = []  # Slipbox's own notes, where the folder chosen contains it
            continue
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS)
        for name in sorted(filenames):
            stub = _CLOUD_STUB_RE.match(name)
            if stub:
                plan.cloud_only += 1
                if len(plan.cloud_examples) < 5:
                    plan.cloud_examples.append((here / stub.group(1)).relative_to(folder).as_posix())
                continue
            if name.startswith(".") or not name.lower().endswith(SUFFIXES):
                continue
            out.append(here / name)
            if len(out) > MAX_FILES:
                raise ImportProblem(
                    "That folder holds more than %s Markdown files, counting its subfolders. "
                    "Import it a part at a time: choose one of its subfolders, then the next." % format(MAX_FILES, ","))
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
    if dt.year < 1900:
        return None  # an unset date (Hugo writes 0001-01-01), not when the note was written
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


def _front_matter(text):
    """(fields, text below them, ok). The lines between --- at the top of a
    file are its front matter only if they read as a set of named fields.
    Otherwise (plain text or a heading between two rules, a broken quotation
    mark) nothing is taken out: the whole file is the note's text, and ok
    is False so the file can be listed."""
    start = _FM_OPEN_RE.match(text)
    if not start:
        return {}, text.strip(), True
    end = _FM_CLOSE_RE.search(text, start.end())
    if not end:
        return {}, text.strip(), True  # an opening rule, nothing more
    block = text[start.end():end.start()]
    if not block.strip():
        return {}, text[end.end():].strip(), True  # empty front matter
    try:
        fields = yaml.load(block, Loader=yaml.SafeLoader)
    except yaml.YAMLError:
        return {}, text.strip(), False
    except Exception:
        # Not a mistake in the YAML but a value that can't be: a date such
        # as 0000-00-00 or 30 February (ValueError), a number thousands of
        # digits long. Read the block again with every value taken as the
        # text it is written as, so the title and tags still come through.
        # If that fails too (brackets nested very deep: RecursionError),
        # the block stays in the note's text. Either way, one such file
        # must not stop the whole preview.
        try:
            fields = yaml.load(block, Loader=yaml.BaseLoader)
        except Exception:
            return {}, text.strip(), False
    if not isinstance(fields, dict) or not all(isinstance(k, str) for k in fields):
        return {}, text.strip(), False
    return fields, text[end.end():].strip(), True


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
    meta, body, ok = _front_matter(text)
    item, counts = _item(path, rel, plan.kind, meta, body)
    if ok and not _storable(item.extra):
        # Fields Slipbox couldn't read back from a note file: the opening
        # lines stay in the note's text instead, and the file is listed.
        ok = False
        item, counts = _item(path, rel, plan.kind, {}, text.strip())
    if not ok:
        plan.yaml_problems.append(rel)
    for name, n in counts["renamed"].items():
        plan.renamed[name] = plan.renamed.get(name, 0) + n
    plan.threads_dropped += counts["threads_dropped"]
    plan.inline_tags += counts["inline_tags"]
    return item


def _storable(extra):
    """Whether these fields, once in a note's front matter, can be read
    back by the reader Slipbox uses for its own notes. Nothing is written
    that Slipbox couldn't open again."""
    if not extra:
        return True
    try:
        post = frontmatter.Post("x")
        post.metadata.update(extra)
        frontmatter.loads(frontmatter.dumps(post))
        return True
    except Exception:
        return False


def _item(path, rel, kind, meta, body):
    """The note a file becomes, from its fields and its text. Returns the
    Item and what to count for the preview; changes nothing else."""
    counts = {"renamed": {}, "threads_dropped": 0, "inline_tags": 0}
    extra = {k: v for k, v in meta.items() if k not in _TAKEN_OVER and k not in _UNREADABLE}

    def keep(name):
        """A field whose name Slipbox uses itself, and whose value it isn't
        using: kept as source-<name>."""
        key = "source-" + name
        while key in extra or key in meta:
            key += "-imported"
        extra[key] = meta[name]
        counts["renamed"][name] = counts["renamed"].get(name, 0) + 1

    for name in _UNREADABLE:
        if name in meta:
            keep(name)

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
    for key in ("created", "modified"):
        if meta.get(key) not in (None, "") and _when(meta.get(key)) is None:
            keep(key)
    now = datetime.now().astimezone()
    created, modified = min(created, now), min(modified, now)

    title = meta.get("title")
    title = re.sub(r"\s+", " ", str(title)).strip() if title not in (None, "") else ""
    title = title or _leading_h1(body) or _title_from_name(path.stem)
    body = _drop_title_heading(body, title)

    if meta.get("threads"):
        counts["threads_dropped"] = 1
        keep("threads")
    m = _NUM_PREFIX_RE.match(path.name)
    number = m.group(1) if m else ""
    fm_id = str(meta.get("id") or "")
    if not number and re.fullmatch(r"\d{14}", fm_id):
        number = fm_id
    note_type = meta.get("type") if meta.get("type") in ("zettel", "manuscript") else "zettel"
    if meta.get("type") not in (None, "", "zettel", "manuscript"):
        keep("type")
    if meta.get("kind") not in (None, "") and str(meta["kind"]).strip().lower() != kind:
        keep("kind")
    if "locked" in meta:
        keep("locked")
    if meta.get("refs") is not None and not isinstance(meta["refs"], (list, tuple, str)):
        keep("refs")
    if any(not _HEX_COLOUR_RE.fullmatch(t) for part in _CODE_RE.split(body)[::2]
           for t in _INLINE_TAG_RE.findall(part)):
        counts["inline_tags"] = 1
    return Item(
        src=path, rel=rel, title=title[:200], body=body, tags=_tags(meta),
        created=created, modified=modified, type=note_type,
        refs=_list(meta.get("refs")), extra=extra,
        number=number, aliases=_list(meta.get("aliases")), fm_id=meta.get("id"),
    ), counts


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


def _stamp(t):
    return "%04d%02d%02d%02d%02d%02d" % (t.year, t.month, t.day, t.hour, t.minute, t.second)


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
    # In date order, so the search for a free second never goes back over
    # seconds already given out: one pass, however many files share a date.
    nxt = None
    for it in sorted((i for i in items if not i.id), key=lambda i: (i.created, i.rel)):
        t = it.created.replace(tzinfo=None, microsecond=0)
        if nxt is not None and t < nxt:
            t = nxt
        nid = _stamp(t)
        while nid in taken:
            t += timedelta(seconds=1)
            nid = _stamp(t)
        it.id = nid
        taken.add(nid)
        nxt = t + timedelta(seconds=1)
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

    def find_path(self, path, here):
        """The Item a Markdown link's path points to, or None: taken as
        relative to the file it is in, then from the top folder, then, for
        a bare file name, by that name when only one file has it."""
        p = path
        for ext in SUFFIXES:
            if p.lower().endswith(ext):
                p = p[: -len(ext)]
                break
        p = p.rstrip("/")
        if not p:
            return None
        relative = _key(posixpath.normpath(posixpath.join(here, p)).lstrip("/"))
        from_top = _key(posixpath.normpath(p.lstrip("/")))
        for k in (relative, from_top, relative + "/index", from_top + "/index"):
            hit = self.by_path.get(k)
            if hit:
                return hit
        # By file name alone only when the link is a bare file name. A path
        # that names folders and matches nothing points somewhere else,
        # perhaps outside the folder being imported, and a note that merely
        # shares the file name is not what it meant.
        if "/" in posixpath.normpath(p) or posixpath.normpath(p) in (".", ".."):
            return None
        hit = self._one(self.by_name.get(_key(p)))
        return hit if isinstance(hit, Item) else None


def _ext(path):
    m = _EXT_RE.search(path)
    return m.group(1).lower() if m else ""


def _is_attachment(target):
    return _ext(re.split(r"[#^|]", target.strip(), maxsplit=1)[0].strip()) in _ATTACHMENT_EXTS


def _link_text(nid, title, words):
    words = (words or "").replace("]", "").strip()
    if not words or _key(words) == _key(title):
        return "[[%s]]" % nid
    return "[[%s|%s]]" % (nid, words)


def _alone_on_line(text, start, end):
    a = text.rfind("\n", 0, start) + 1
    b = text.find("\n", end)
    return not text[a:start].strip() and not text[end:len(text) if b < 0 else b].strip()


def _convert(item, links, plan):
    here = posixpath.dirname(item.rel)

    def note_attachment(text):
        plan.attachments += 1
        if len(plan.attachment_examples) < 5:
            plan.attachment_examples.append((item.rel, text))

    def wiki(m):
        bang, target, words = m.group(1), m.group(2), m.group(3)
        # In a table the | is written \| so that it doesn't end the cell.
        in_table = target.endswith("\\")
        if in_table:
            target = target[:-1]
        if target.lstrip().startswith(("#", "^")):
            return m.group(0)  # a heading or block in this same note: not a link to another
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
        part = bool(re.search(r"[#^]", target))
        if bang:
            # Slipbox embeds a whole note, and only from a line of its own.
            if not part and _alone_on_line(m.string, m.start(), m.end()):
                return "![[%s]]" % hit.id
            plan.embeds_as_links += 1
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
        if in_table:
            # [[ID|words]] would end the table cell at the |, and Slipbox
            # has no \| form, so the link shows the note's title.
            if words.strip() and _key(words) != _key(hit.title):
                plan.table_links += 1
            return "[[%s]]" % hit.id
        return _link_text(hit.id, hit.title, words)

    def md(m):
        bang, words = m.group(1), m.group(2)
        target = (m.group(3) if m.group(3) is not None else m.group(4)).strip()
        if not target or _SCHEME_RE.match(target) or target.startswith(("#", "//")):
            return m.group(0)  # a web link, an e-mail address, a heading here
        path = unquote(target).split("#", 1)[0]
        if not path:
            return m.group(0)
        ext = _ext(path)
        is_note = path.lower().endswith(SUFFIXES)
        if bang or (not is_note and (ext in _ATTACHMENT_EXTS or re.search(r"[a-z]", ext))):
            note_attachment(m.group(0))
            return m.group(0)
        hit = links.find_path(path, here)
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
    files = _files(folder, plan)
    plan.found = len(files)
    for path in files:
        rel = path.relative_to(folder).as_posix()
        item = _read(path, rel, plan)
        if item:
            plan.items.append(item)
    _assign_ids(plan.items)
    for it in plan.items:
        # Another app's "id", unless it is the ID the note has here.
        if it.fm_id not in (None, "") and str(it.fm_id) != it.id:
            key = "source-id"
            while key in it.extra:
                key += "-imported"
            it.extra[key] = it.fm_id
            plan.renamed["id"] = plan.renamed.get("id", 0) + 1
    links = _Links(plan.items)
    for item in plan.items:
        _convert(item, links, plan)
    existing = {_key(t) for t in db.all_titles()}
    plan.clashes = [it.title for it in plan.items if _key(it.title) in existing]
    return plan


def run(plan, lock=False):
    """Write the planned notes. Returns (notes written, error message or "").
    With lock (the kind is one the person always locks, and locked notes
    are open), each note's text is encrypted before it reaches the disk.
    A file whose note Slipbox couldn't read back is skipped and listed,
    not written; a failure to write stops the import."""
    written = []
    present = {m.group(1) for m in (notes.ID_RE.match(p.name) for p in notes.NOTES_DIR.glob("*.md")) if m}
    for it in plan.items:
        path = notes.NOTES_DIR / it.filename
        if path.exists() or it.id in present:
            plan.skipped.append((it.rel, "a note with the same ID appeared while importing"))
            continue
        try:
            text = it.body.strip("\n") + "\n"
            content = vault.encrypt_body(it.id, text) if lock else text
        except Exception as e:
            return written, "Stopped at %s: %s" % (it.rel, e)
        try:
            post = frontmatter.Post(content)
            post.metadata.update(it.extra)
            post.metadata.update(
                id=it.id, title=it.title,
                created=it.created.isoformat(timespec="seconds"),
                modified=it.modified.isoformat(timespec="seconds"),
                type=it.type, kind=plan.kind, tags=it.tags, threads=[], refs=it.refs,
            )
            if lock:
                post.metadata["locked"] = True
            out = frontmatter.dumps(post)
            frontmatter.loads(out)  # what is written must be readable again
        except Exception as e:
            plan.skipped.append((it.rel, "its front matter can’t be kept in a Slipbox note (%s)" % type(e).__name__))
            continue
        try:
            notes._write_atomically(path, out)
            db.upsert_note(notes.parse_file(path))
        except Exception as e:
            try:
                path.unlink()  # never leave a half-made note behind
            except OSError:
                pass
            return written, "Stopped at %s: %s" % (it.rel, e)
        written.append(it)
    return written, ""
