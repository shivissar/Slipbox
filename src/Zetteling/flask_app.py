"""
app.py — Flask web UI for the Zettelkasten.

Configuration is read entirely from environment variables so the same
image can be deployed anywhere. See .env.example for the full list.

URL parameters use snake_case throughout because single-word URL params
get mangled by an upstream sanitizer in our toolchain.
"""

# Copyright (c) 2026 Shiv Issar
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import json
import re
import os
import secrets
import threading
import xml.etree.ElementTree as etree

import markdown
from flask import Flask, Response, abort, g, jsonify, redirect, render_template, request, url_for
from markdown.extensions import Extension
from markdown.inlinepatterns import InlineProcessor

from markupsafe import Markup, escape
import platform
import subprocess
from pathlib import Path

from . import ai
from . import bib
from . import chat
from . import db
from . import embed
from . import export
from . import notes
from . import vault
from . import history
from . import note_templates
from . import codebook
from . import updates as release_check
from . import settings
from . import themes
from . import watcher

__version__ = "5.2.0"

app = Flask(__name__)

# 5.2: the desktop app talks to its own window only, so it no longer trusts
# X-Forwarded-* headers (they could rewrite the Host a request claims) and
# answers only requests addressed to 127.0.0.1 / localhost. A web page that
# points its own domain name at this Mac ("DNS rebinding") is refused.
# Running behind a reverse proxy on a server: set ZETTEL_BEHIND_PROXY=1 and
# ZETTEL_TRUSTED_HOSTS (comma-separated names).
if os.environ.get("ZETTEL_BEHIND_PROXY") == "1":  # pragma: no cover - server deployments
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
_trusted = [h.strip() for h in os.environ.get("ZETTEL_TRUSTED_HOSTS", "").split(",") if h.strip()]
app.config["TRUSTED_HOSTS"] = _trusted or ["127.0.0.1", "localhost"]
# No sessions or signed cookies are used; a per-process random key keeps it
# that way safely if one is ever added.
app.config["SECRET_KEY"] = os.environ.get("ZETTEL_SECRET_KEY") or secrets.token_hex(32)

# v3.17: cap request body size. 10MB is generous for a single Markdown
# note but small enough to prevent a pathological POST from filling disk
# or RAM. Flask aborts with a 413 Request Entity Too Large if exceeded.
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024


@app.context_processor
def inject_globals():
    """Make version, attribution, appearance and sidebar counts available
    in every template."""
    s = settings.load()
    return {
        "csp_nonce": _csp_nonce(),
        "version": __version__,
        "attribution": Markup(
            'Created by <a href="https://www.shivissar.com">Shiv Issar, Ph.D.</a>, inspired by '
            'Prof. <a href="https://en.wikipedia.org/wiki/Niklas_Luhmann">Niklas Luhmann</a> and '
            'Prof. <a href="https://en.wikipedia.org/wiki/A._Aneesh">Aneesh Aneesh</a>'),
        "pandoc_available": export.PANDOC_AVAILABLE,
        "appearance": themes.current(),
        "nav_counts": db.sidebar_counts(),
        "kinds": KINDS,
        "vault_state": _vault_state(),
        "template_index": _template_index(),
        # 5.2: the welcome tour starts by itself once, on a main page
        "tour_autostart": (not s.get("tour_seen", False)
                           and request.endpoint in ("index", "view", "search")),
        # 5.2: a newer release is out (from the last update check)
        "update_status": release_check.status(__version__, s),
        # 5.2: Back (and esc) on Help, Export and Open beside
        "back_link": _back_link() if request.endpoint in _BACK_PAGES else None,
        # 5.2: one-off notices (e.g. notes locked by the sweep)
        "flash_msg": "" if request.headers.get("HX-Request") else _pop_flash(),
        # 5.2: note files that couldn't be read (they used to vanish silently)
        "unreadable_files": dict(watcher.PROBLEMS),
        # 5.2: settings.json couldn't be read (restored from the backup copy)
        "settings_problem": settings.problem_notice(),
    }


KINDS = ("research", "journal", "teaching", "life")


@app.template_filter("marks")
def _marks_only(text):
    """Search snippets come from note text with <mark> around the match.
    Escape everything, then let only those <mark> tags back in (5.2: the
    snippet used to be shown as raw HTML, so markup in a note ran)."""
    out = str(escape(text or ""))
    return Markup(out.replace("&lt;mark&gt;", "<mark>").replace("&lt;/mark&gt;", "</mark>"))


def _csp_nonce():
    nonce = getattr(g, "csp_nonce", None)
    if nonce is None:
        nonce = g.csp_nonce = secrets.token_urlsafe(16)
    return nonce


# 5.2: pages may only run the app's own scripts (and inline ones carrying
# this request's nonce). Even if a note's HTML got past sanitising, it
# couldn't run, and javascript: links do nothing.
_CSP = ("default-src 'self'; script-src 'self' 'nonce-{nonce}'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https: http:; font-src 'self' data:; connect-src 'self'; "
        "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


# ---------- 5.1 locked notes: state, idle lock, helpers ---------- #

# Requests that don't count as the person using the app.
_BACKGROUND_PATHS = ("/static/", "/api/embed/status", "/api/vault/status", "/favicon")


def _lock_after_minutes():
    try:
        return max(0, int(settings.get("lock_after_minutes", 15) or 0))
    except (TypeError, ValueError):
        return 15


def _locked_kinds():
    kinds = settings.get("locked_kinds", []) or []
    return [k for k in kinds if k in KINDS]


def _kind_locked(kind):
    return (kind or "").strip().lower() in _locked_kinds()


_TEMPLATE_CACHE = {"key": None, "value": []}


def _template_index():
    """(slug, name) pairs for the palette, re-read only when the templates
    folder changes."""
    try:
        d = note_templates.templates_dir()
        key = (str(d), d.stat().st_mtime) if d.exists() else (str(d), None)
        if key != _TEMPLATE_CACHE["key"]:
            _TEMPLATE_CACHE["value"] = [(t["slug"], t["name"]) for t in note_templates.list_templates()]
            _TEMPLATE_CACHE["key"] = (str(d), d.stat().st_mtime)
        return _TEMPLATE_CACHE["value"]
    except OSError:
        return []


def _vault_state():
    set_up = vault.is_set_up()
    return {
        "set_up": set_up,
        "open": vault.is_unlocked(),
        "idle_seconds": _lock_after_minutes() * 60,
        "locked_kinds": _locked_kinds(),
    }


@app.before_request
def _same_origin_posts():
    """Refuse changes requested by other websites. A page open in any
    browser on this Mac could otherwise post to Zetteling's local port
    (for example, to remove the locks from notes while they're open)."""
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return None
    source = request.headers.get("Origin") or request.headers.get("Referer")
    if not source:
        return None  # not sent by a browser page (tests, scripts)
    from urllib.parse import urlsplit
    if source == "null" or urlsplit(source).netloc != request.host:
        abort(403)
    return None


@app.before_request
def _vault_activity():
    """Close locked notes after the chosen idle time, then count this
    request as activity (unless it's a background request). While they're
    open, lock any plain-text notes of an always-locked kind (5.2)."""
    vault.autolock_if_idle(_lock_after_minutes())
    path = request.path or ""
    if not path.startswith(_BACKGROUND_PATHS):
        vault.touch()
        if request.method == "GET" and not path.startswith("/api/"):
            sweep_locked_kinds()


# --- 5.2: kinds that are always locked ---------------------------------
# Only a note whose kind *changes* to a locked kind used to be locked. A
# note of that kind arriving in plain text (synced from another Mac, made
# in another editor, or left by an interrupted bulk lock) stayed readable
# on disk, silently. While locked notes are open, such notes are now locked,
# at most once a minute, except ones whose lock was removed on purpose.
_SWEEP = {"at": -1e9, "msg": ""}


def _set_exempt(note_id, exempt):
    ids = [i for i in (settings.get("lock_exempt", []) or []) if isinstance(i, str)]
    if exempt and note_id not in ids:
        ids.append(note_id)
    elif not exempt and note_id in ids:
        ids.remove(note_id)
    else:
        return
    settings.save({"lock_exempt": ids})


def _pending_kind_locks():
    kinds = _locked_kinds()
    if not kinds:
        return []
    exempt = set(settings.get("lock_exempt", []) or [])
    return [i for i in db.unlocked_ids_of_kinds(kinds) if i not in exempt]


def sweep_locked_kinds(force=False):
    """Lock plain-text notes of always-locked kinds. Returns how many."""
    import time as _time
    if not vault.is_unlocked() or not _locked_kinds():
        return 0
    now = _time.monotonic()
    if not force and now - _SWEEP["at"] < 60:
        return 0
    _SWEEP["at"] = now
    kinds = _locked_kinds()
    count = 0
    for nid in _pending_kind_locks():
        note = _find_note_by_id(nid)
        if not note or note.locked or note.unreadable or note.kind not in kinds:
            continue
        try:
            with _SAVE_LOCK:
                _lock_note(note)
            count += 1
        except (OSError, vault.VaultError) as e:
            print("[vault] couldn't lock %s: %s" % (nid, e))
    if count:
        db.scrub_index()
        _SWEEP["msg"] = "Locked %s of a kind you always lock: %s arrived in plain text." % (
            _plural(count, "note"), "it" if count == 1 else "they")
    return count


def _pop_flash():
    msg, _SWEEP["msg"] = _SWEEP["msg"], ""
    return msg


# Side pages that get a Back button (and esc) returning to the note.
_BACK_PAGES = {"help_page", "export_view", "split_picker", "split_view"}
_NOTE_PATH_RE = re.compile(r"^/note/([^/]+)(?:/(?:edit|history|split))?/?$")


def _back_link():
    """Where Back and esc go from a side page: the note the page is about
    (an export, Open beside), else the note you came from, else the page
    you came from. None when there's nowhere sensible to go."""
    from urllib.parse import urlsplit
    va = request.view_args or {}
    ep = request.endpoint
    nid = None
    if ep == "export_view":
        if va.get("source_type") == "note":
            nid = va.get("source_id")
        elif va.get("source_type") == "thread":
            return {"href": url_for("thread_view", thread_name=va["source_id"]), "label": "Back to the thread"}
        elif va.get("source_type") == "tag":
            return {"href": url_for("tag_view", tag_name=va["source_id"]), "label": "Back to the tag"}
    elif ep == "split_picker":
        nid = va.get("note_id")
    elif ep == "split_view":
        nid = va.get("id1")
    if not nid:
        ref = request.referrer or ""
        parts = urlsplit(ref)
        if parts.netloc != request.host or not parts.path or parts.path == request.path:
            return None
        m = _NOTE_PATH_RE.match(parts.path)
        if m:
            nid = m.group(1)
        elif not parts.path.startswith(("/api/", "/update/", "/vault/", "/open-link")):
            return {"href": parts.path + ("?" + parts.query if parts.query else ""), "label": "Back"}
        else:
            return None
    title = db.title_for(nid)
    if title is None:
        return None
    return {"href": url_for("view", note_id=nid), "label": "Back to the note", "title": title}


@app.before_request
def _update_check_kickoff():
    """5.2: when a main page is opened, start the daily update check in the
    background if it's due. The answer shows on the next page."""
    if request.method == "GET" and request.endpoint in ("index", "view", "search"):
        release_check.check_in_background(__version__)


@app.after_request
def _no_store(response):
    """Pages and API replies can contain decrypted locked notes; keep them
    out of the web view's on-disk cache. Static files still cache."""
    if not (request.path or "").startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    if response.mimetype == "text/html":
        response.headers["Content-Security-Policy"] = _CSP.format(nonce=_csp_nonce())
        response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _safe_next(target, fallback="/"):
    target = (target or "").strip()
    if not target.startswith("/") or target.startswith("//") or "\\" in target:
        return fallback
    return target


def _locked_placeholder(note):
    if note.damaged:
        return Markup('<p class="locked-note-msg">This locked note can\u2019t be opened: '
                      "it was changed outside Zetteling or is damaged.</p>")
    return Markup('<p class="locked-note-msg">This note is locked. Unlock locked notes to read it.</p>')


def _locked_response(mode, message="Locked notes are closed. Enter your password to save this note."):
    if mode == "save":
        return render_template("locked_save.html", message=message), 423
    return jsonify({"ok": False, "locked": True, "message": message}), 423


def _lock_note(note):
    """Encrypt a readable note in place, and its saved versions. The caller
    scrubs the index."""
    note.locked = True
    notes.write_note(note)
    db.upsert_note(note)
    history.encrypt_versions(note.id)
    embed.delete_embedding_for_note(note.id)
    chat.clear_conversation(note.id)


def _unlock_note(note):
    history.snapshot(note.path, "unlock", force=True)
    note.locked = False
    note.cipher = ""
    notes.write_note(note)
    db.upsert_note(note)


# ---------- Wikilink markdown extension ---------- #

WIKILINK_PATTERN = r"\[\[(\d{14})(?:\|([^\]]+))?\]\]"


class WikilinkProcessor(InlineProcessor):
    def handleMatch(self, m, data):
        link_id = m.group(1)
        alias = m.group(2)
        title = db.title_for(link_id)
        el = etree.Element("a")
        el.set("href", f"/note/{link_id}")
        if title:
            el.set("class", "wikilink")
            el.text = alias or title
        else:
            el.set("class", "wikilink broken")
            el.text = alias or link_id
        return el, m.start(0), m.end(0)


class WikilinkExtension(Extension):
    def extendMarkdown(self, md):
        md.inlinePatterns.register(
            WikilinkProcessor(WIKILINK_PATTERN), "wikilink", 175
        )


# ---------- Citekey markdown extension ---------- #
# Pandoc-style @citekey references. Negative lookbehind avoids matching
# inside email addresses or @-mentions of usernames in code.

CITEKEY_PATTERN = r"(?<![A-Za-z0-9@_])@([A-Za-z][A-Za-z0-9_:-]+)"


class CitekeyProcessor(InlineProcessor):
    def handleMatch(self, m, data):
        citekey = m.group(1)
        entry = db.get_bib_entry(citekey)
        el = etree.Element("a")
        el.set("href", f"/citekeys#{citekey}")
        if entry:
            el.set("class", "citekey")
            tooltip = f"{entry.get('author', '')} ({entry.get('year', '')}) — {entry.get('title', '')}"
            el.set("title", f"{tooltip.strip(' —')}  ·  @{citekey}")
            # v5.0: show an author-date label ("Suchman 1987") instead of
            # the raw key. The note file still holds @citekey, so pandoc
            # export and citeproc are unaffected.
            el.text = _cite_label(entry) or f"@{citekey}"
        else:
            el.set("class", "citekey broken")
            el.set("title", f"Unknown citekey: {citekey}")
            el.text = f"@{citekey}"
        return el, m.start(0), m.end(0)


def _cite_label(entry):
    """'Suchman 1987', 'Latour and Woolgar 1979' or 'Star et al. 1999'
    from a bib entry's author and year fields."""
    authors = [a.strip() for a in (entry.get("author") or "").replace("\n", " ").split(" and ") if a.strip()]

    def surname(a):
        a = a.strip("{} ")
        if "," in a:
            return a.split(",", 1)[0].strip("{} ")
        return a.split()[-1].strip("{} ") if a.split() else a

    if not authors:
        return ""
    if len(authors) == 1:
        names = surname(authors[0])
    elif len(authors) == 2:
        names = f"{surname(authors[0])} and {surname(authors[1])}"
    else:
        names = f"{surname(authors[0])} et al."
    year = (entry.get("year") or "").strip()
    return f"{names} {year}".strip()


class CitekeyExtension(Extension):
    def extendMarkdown(self, md):
        md.inlinePatterns.register(
            CitekeyProcessor(CITEKEY_PATTERN), "citekey", 170
        )


# ---------- 5.2 coded passages ---------- #
# [passage]{.code .other} -> a highlighted passage followed by its codes.


class CodedSpanProcessor(InlineProcessor):
    def handleMatch(self, m, data):
        if "\n\n" in m.group(1):
            return None, None, None
        codes = re.findall(r"\.([\w-]+)", m.group(2))
        wrap = etree.Element("span")
        wrap.set("class", "qcode")
        mark = etree.SubElement(wrap, "mark")
        mark.text = m.group(1)
        tags = etree.SubElement(wrap, "span")
        tags.set("class", "qcode-tags")
        for c in codes:
            a = etree.SubElement(tags, "a")
            a.set("href", f"/codes/{c}")
            a.set("class", "qcode-tag")
            a.text = c
        return wrap, m.start(0), m.end(0)


class CodedSpanExtension(Extension):
    def extendMarkdown(self, md):
        md.inlinePatterns.register(
            CodedSpanProcessor(codebook.SPAN_RE.pattern), "codedspan", 172
        )


# 5.2: one Markdown converter per thread. A single shared one could mix
# two notes' footnotes (and other per-document state) when two pages were
# rendered at once on the server's request threads.
_MD_LOCAL = threading.local()


def _new_markdown():
    return markdown.Markdown(
        extensions=[
            "fenced_code",
            "tables",
            "footnotes",
            "codehilite",
            WikilinkExtension(),
            CitekeyExtension(),
            CodedSpanExtension(),
        ],
        extension_configs={
            "codehilite": {
                "css_class": "codehilite",
                "guess_lang": False,
            },
            # align="…" survives sanitising; style="…" wouldn't
            "tables": {"use_align_attribute": True},
        },
    )


def _md():
    md = getattr(_MD_LOCAL, "md", None)
    if md is None:
        md = _MD_LOCAL.md = _new_markdown()
    return md


# Note HTML is sanitised before display (5.2: on by default; Settings →
# Writing can turn it off). nh3 (Rust's ammonia) replaces bleach, which is
# deprecated. Scripts, event handlers, styles, forms and javascript: links go;
# Markdown's own output (headings, lists, tables, footnotes, code, images,
# links) and Zetteling's links, citations and coded passages stay.
_SAFE_TAGS = {
    "p", "br", "hr", "pre", "code", "blockquote", "kbd", "samp", "var",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "strong", "em", "b", "i", "u", "s", "del", "ins", "mark", "sub", "sup", "small",
    "abbr", "cite", "q", "dfn", "time",
    "ul", "ol", "li", "dl", "dt", "dd",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption", "colgroup", "col",
    "a", "img", "figure", "figcaption", "details", "summary",
    "div", "span",
}
_SAFE_ATTRS = {
    "a": {"href", "title", "class"},
    "img": {"src", "alt", "title", "width", "height"},
    "code": {"class"}, "pre": {"class"}, "span": {"class"}, "div": {"class"},
    "th": {"align", "colspan", "rowspan"}, "td": {"align", "colspan", "rowspan"},
    "ol": {"start"}, "abbr": {"title"}, "time": {"datetime"},
    "sup": {"id"}, "li": {"id"},  # footnotes
}
_FOOTNOTE_ID_RE = re.compile(r"^fn(?:ref)?:[\w-]+$")


def _keep_attr(tag, attr, value):
    # ids are kept only for Markdown footnotes, so a note can't take over
    # an id the app itself uses.
    if attr == "id" and not _FOOTNOTE_ID_RE.match(value or ""):
        return None
    return value


def _sanitise(html):
    try:
        import nh3
    except ImportError:  # pragma: no cover - nh3 ships with the app
        return str(escape(html))
    return nh3.clean(html, tags=_SAFE_TAGS, attributes=_SAFE_ATTRS, attribute_filter=_keep_attr,
                     url_schemes={"http", "https", "mailto"})


EMBED_LINE_RE = re.compile(r"^[ \t]*!\[\[(\d{14})(?:\|([^\]\n]*))?\]\][ \t]*$", re.M)


def _render_note_html(body, _depth=0, _seen=None):
    """Render a note body. Lines that are just ![[id]] embed that note
    (5.2): its text appears in place, framed, with a link to it. Embeds
    nest two levels deep; deeper or circular ones show as links."""
    if not EMBED_LINE_RE.search(body or ""):
        return _render_markdown_html(body)
    seen = set(_seen or ())
    out, last = [], 0
    for m in EMBED_LINE_RE.finditer(body):
        chunk = body[last:m.start()]
        if chunk.strip():
            out.append(str(_render_markdown_html(chunk)))
        out.append(_embed_html(m.group(1), _depth, seen))
        last = m.end()
    tail = body[last:]
    if tail.strip():
        out.append(str(_render_markdown_html(tail)))
    return Markup("".join(out))


def _embed_html(note_id, depth, seen):
    from markupsafe import escape
    note = _find_note_by_id(note_id)
    if note is None:
        return '<div class="embed embed-broken">Missing note: %s</div>' % escape(note_id)
    head = ('<div class="embed-head"><a href="/note/%s">%s</a><span>%s words</span></div>'
            % (escape(note.id), escape(note.title), len((note.body or "").split())))
    if note.unreadable:
        inner = str(_locked_placeholder(note))
    elif depth >= 2 or note.id in seen:
        inner = '<p class="hint">Embedded again further down; open it to read.</p>'
    else:
        inner = str(_render_note_html(note.body, depth + 1, seen | {note.id}))
    return '<section class="embed">%s<div class="embed-body">%s</div></section>' % (head, inner)


def _render_markdown_html(body):
    """Convert a note body to HTML, sanitised unless the person has turned
    that off in Settings → Writing."""
    md = _md()
    md.reset()
    raw = md.convert(body)
    if not settings.get("sanitize_note_html", True):
        return raw
    return _sanitise(raw)


def _find_note_by_id(note_id):
    meta = db.get_note_meta(note_id)
    if not meta:
        return None
    path = notes.NOTES_DIR / meta["filename"]
    if not path.exists():
        return None
    return notes.parse_file(path)


# ---------- Routes ---------- #


def _clean_kind(value, default="research", allow_all=True):
    value = (value or "").strip().lower()
    allowed = KINDS + (("all",) if allow_all else ())
    return value if value in allowed else default


def _list_for(kind):
    """Cards for the note list column."""
    return db.list_recent_cards(60, kind=None if kind == "all" else kind)


def _search_results(q):
    """Full-text search that never raises on odd input (an unmatched
    quote, a lone hyphen) — falls back to the forgiving prefix search."""
    if not q:
        return []
    if db.has_filters(db.parse_query(q)):
        return db.filtered_search(q)
    try:
        return db.search(q)
    except Exception:
        return [
            {"id": r["id"], "title": r["title"], "modified": r["modified"], "snip": r.get("snip", "")}
            for r in db.quick_search(q, limit=50)
        ]


@app.route("/")
def index():
    counts = db.kind_counts()
    if counts.get("all", 0) == 0 and not settings.get("welcomed", False):
        return redirect(url_for("welcome"))
    kind = _clean_kind(request.args.get("kind", "research"))
    return render_template(
        "index.html",
        list_cards=_list_for(kind),
        active_kind=kind,
        kind_counts=counts,
    )


@app.route("/welcome", methods=["GET", "POST"])
def welcome():
    """First-run screen. Shown once, when there are no notes yet."""
    notes_dir_changed = False
    if request.method == "POST":
        updates = {"welcomed": True,
                   "update_check": request.form.get("update_check") == "on"}
        new_dir = request.form.get("notes_dir", "").strip()
        if new_dir and str(Path(new_dir).expanduser()) != str(Path(settings.get_notes_dir()).expanduser()):
            target = Path(new_dir).expanduser()
            try:
                target.mkdir(parents=True, exist_ok=True)
                updates["notes_dir"] = str(target)
                notes_dir_changed = True
            except OSError as e:
                return render_template("welcome.html", error=f"Could not use that folder: {e}",
                                       active_notes_dir=settings.get_notes_dir(),
                                       bib_path=settings.get("bib_path", ""))
        bib_path = request.form.get("bib_path", "").strip()
        if bib_path:
            updates["bib_path"] = bib_path
        settings.save(updates)
        if bib_path:
            bib.restart_bib_watcher()
        if notes_dir_changed:
            return render_template("welcome.html", restart_needed=True,
                                   active_notes_dir=updates["notes_dir"],
                                   bib_path=settings.get("bib_path", ""))
        return redirect(url_for("new"))
    return render_template("welcome.html", active_notes_dir=settings.get_notes_dir(),
                           bib_path=settings.get("bib_path", ""))


@app.route("/welcome/skip", methods=["POST"])
def welcome_skip():
    settings.save({"welcomed": True})
    return redirect(url_for("index"))


@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    results = _search_results(q)
    filters = db.describe_filters(db.parse_query(q)) if q else []
    if request.headers.get("HX-Request"):
        return render_template("_search_results.html", results=results, q=q, filters=filters)
    return render_template(
        "index.html",
        list_cards=_list_for("all"),
        results=results,
        filters=filters,
        q=q,
        active_kind="all",
        kind_counts=db.kind_counts(),
    )


@app.route("/note/<note_id>")
def view(note_id):
    note = _find_note_by_id(note_id)
    if not note:
        abort(404)
    rendered = None if note.unreadable else _render_note_html(note.body)
    backlinks = db.get_backlinks(note_id, with_context=True)
    mentions = [] if note.unreadable else db.unlinked_mentions(note_id, note.title)
    outline = _outline(note) if note.type == "manuscript" and not note.unreadable else None
    thread_contexts = []
    for t in db.threads_for_note(note_id):
        thread_contexts.append({
            "name": t["name"],
            "prev": (
                {"id": t["prev_id"], "title": db.title_for(t["prev_id"])}
                if t["prev_id"] else None
            ),
            "next": (
                {"id": t["next_id"], "title": db.title_for(t["next_id"])}
                if t["next_id"] else None
            ),
        })
    thread_contexts = _thread_positions(note_id, thread_contexts)
    similar = embed.find_related(note_id, k=8) if db.SQLITE_VEC_AVAILABLE else []
    return render_template(
        "note.html",
        rail="context",
        backlinks=backlinks,
        mentions=mentions,
        outline=outline,
        all_threads=db.all_threads() if outline is not None else [],
        thread_contexts=thread_contexts,
        similar=similar,
        **_note_page_context(note, rendered),
    )


def _thread_positions(note_id, thread_contexts):
    """Add 'position' and 'length' to each thread context for the
    stepping-stones indicator in the side panel."""
    for tc in thread_contexts:
        chain = db.thread_chain(tc["name"])
        ids = [c["id"] for c in chain]
        tc["length"] = len(ids)
        tc["position"] = ids.index(note_id) + 1 if note_id in ids else None
    return thread_contexts


def _note_page_context(note, rendered):
    """Values every note page (reading or interrogating) shares."""
    kind = note.kind or "research"
    return {
        "note": note,
        "rendered": rendered,
        "active_kind": kind,
        "list_cards": _list_for(kind),
        "chat_length": chat.conversation_length(note.id),
        "chat_available": ai.get_provider() is not None and not note.locked,
        "refs_resolved": [
            {"key": k, "entry": db.get_bib_entry(k)} for k in (note.refs or [])
        ],
    }


# --- Saving and autosave (5.0.1) --------------------------------------
# The editor saves in three modes, sent as the hidden form field _autosave:
#   "1"     autosave while typing: write the file and index, no embedding,
#           no thread changes, JSON reply.
#   "leave" the editor is closing (sent as a beacon): as "1", plus thread
#           choice and embedding.
#   absent  an explicit Save / Cmd-S: as "leave", then redirect.
# Each editor page sends a random _draft token and an increasing _seq, so a
# slow, older request can never overwrite newer text, and a new note is
# created once however many saves race to create it.
import threading as _threading

_SAVE_LOCK = _threading.Lock()
_LAST_SEQ = {}   # draft token -> highest _seq written
_DRAFTS = {}     # draft token -> id of the note it created


def _save_mode():
    v = request.form.get("_autosave", "").strip()
    if v == "1":
        return "auto"
    if v == "leave":
        return "leave"
    return "save"


def _claim_seq():
    """True if this request is the newest from its editor page (or carries
    no token). Call with _SAVE_LOCK held."""
    token = request.form.get("_draft", "").strip()
    try:
        seq = int(request.form.get("_seq", "0") or 0)
    except ValueError:
        seq = 0
    if not token or not seq:
        return True
    if seq <= _LAST_SEQ.get(token, 0):
        return False
    _LAST_SEQ[token] = seq
    return True


def _apply_edit_form(note, form):
    note.title = form.get("title", note.title).strip() or note.title
    note.body = form.get("body", "")
    tags_raw = form.get("tags", "")
    note.tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
    new_kind = form.get("kind", "").strip().lower()
    if new_kind in KINDS:
        note.kind = new_kind


def _apply_thread_action(note, form):
    """Put a new note into a thread, as chosen on the new-note form. Sets
    note.threads (the caller writes the note) and updates the old tail."""
    thread_action = form.get("thread_action", "").strip()
    if thread_action == "new":
        thread_name = form.get("new_thread_name", "").strip()
        if thread_name:
            note.threads = [{"name": thread_name, "prev": None, "next": None}]
    elif thread_action == "existing":
        thread_name = form.get("existing_thread_name", "").strip()
        if not thread_name:
            return
        tail_id = db.thread_tail(thread_name)
        if not tail_id or tail_id == note.id:
            note.threads = [{"name": thread_name, "prev": None, "next": None}]
            return
        note.threads = [{"name": thread_name, "prev": tail_id, "next": None}]
        tail_meta = db.get_note_meta(tail_id)
        if tail_meta:
            tail_path = notes.NOTES_DIR / tail_meta["filename"]
            if tail_path.exists():
                tail_note = notes.parse_file(tail_path)
                updated = False
                for t in tail_note.threads:
                    if isinstance(t, dict) and t.get("name") == thread_name:
                        t["next"] = note.id
                        updated = True
                        break
                if updated:
                    notes.write_note(tail_note)
                    db.upsert_note(tail_note)


@app.route("/note/<note_id>/edit", methods=["GET", "POST"])
def edit(note_id):
    note = _find_note_by_id(note_id)
    if not note:
        abort(404)
    if request.method == "POST":
        mode = _save_mode()
        if note.unreadable:
            if note.damaged:
                return _locked_response(mode, "This locked note is damaged, so it can't be saved.")
            return _locked_response(mode)
        newly_locked = False
        try:
            with _SAVE_LOCK:
                fresh = _claim_seq()
                if fresh:
                    old_kind = note.kind
                    _apply_edit_form(note, request.form)
                    if not note.locked and note.kind != old_kind and _kind_locked(note.kind):
                        note.locked = newly_locked = True
                    if mode != "auto" and not note.threads:
                        _apply_thread_action(note, request.form)
                    notes.write_note(note)
                    db.upsert_note(note)
        except vault.VaultLocked:
            return _locked_response(mode)
        if newly_locked:
            history.encrypt_versions(note.id)
            embed.delete_embedding_for_note(note.id)
            db.scrub_index()
        elif fresh and mode != "auto":
            embed.embed_note_async(note)
        if mode == "save":
            return redirect(url_for("view", note_id=note.id))
        return jsonify({"ok": True, "id": note.id, "modified": note.modified, "locked": note.locked,
                        "outside_version_kept": bool(getattr(note, "kept_outside_version", False))})
    if note.unreadable:
        if note.damaged:
            return redirect(url_for("view", note_id=note.id))
        return redirect(url_for("unlock_page", next=url_for("edit", note_id=note.id)))
    notes.remember(note)
    return render_template("edit.html", note=note, autosave=bool(settings.get("autosave", True)))


@app.route("/note/<note_id>/delete", methods=["POST"])
def delete(note_id):
    note = _find_note_by_id(note_id)
    if not note:
        abort(404)
    notes.soft_delete(note)
    db.delete_note(note_id)
    embed.delete_embedding_for_note(note_id)
    return redirect(url_for("index"))


@app.route("/new", methods=["GET", "POST"])
def new():
    if request.method == "POST":
        mode = _save_mode()
        token = request.form.get("_draft", "").strip()
        title = request.form.get("title", "").strip()
        if mode != "save" and not title:
            # Autosave waits for a title, so filenames get a real slug.
            return jsonify({"ok": False, "message": "Add a title to start saving."}), 400
        try:
            with _SAVE_LOCK:
                existing_id = _DRAFTS.get(token) if token else None
                note = _find_note_by_id(existing_id) if existing_id else None
                if note is not None and note.unreadable:
                    raise vault.VaultLocked("closed")
                fresh = _claim_seq()
                if note is None:
                    kind = request.form.get("kind", "research").strip().lower()
                    if kind not in KINDS:
                        kind = "research"
                    tags_raw = request.form.get("tags", "")
                    note = notes.create_note(
                        title or "Untitled",
                        body=request.form.get("body", ""),
                        tags=[t.strip() for t in tags_raw.split(",") if t.strip()],
                        kind=kind,
                        locked=_kind_locked(kind),
                    )
                    if token:
                        _DRAFTS[token] = note.id
                    fresh = True
                elif fresh:
                    old_kind = note.kind
                    _apply_edit_form(note, request.form)
                    if not note.locked and note.kind != old_kind and _kind_locked(note.kind):
                        note.locked = True
                if fresh:
                    if mode != "auto" and not note.threads:
                        _apply_thread_action(note, request.form)
                    notes.write_note(note)
                    db.upsert_note(note)
        except vault.VaultLocked:
            return _locked_response(mode)
        if note.locked:
            embed.delete_embedding_for_note(note.id)
        elif fresh and mode != "auto":
            embed.embed_note_async(note)
        if mode == "save":
            return redirect(url_for("view", note_id=note.id))
        return jsonify({
            "ok": True,
            "id": note.id,
            "modified": note.modified,
            "edit_url": url_for("edit", note_id=note.id),
            "view_url": url_for("view", note_id=note.id),
            "locked": note.locked,
        })
    preselect_kind = request.args.get("kind", "research").strip().lower()
    if preselect_kind not in ("research", "journal", "teaching", "life"):
        preselect_kind = "research"
    prefill = note_templates.render(request.args.get("template", "").strip()) if request.args.get("template") else None
    if prefill:
        preselect_kind = prefill["kind"]
    if _kind_locked(preselect_kind) and not vault.is_unlocked():
        return redirect(url_for("unlock_page", next=request.full_path.rstrip("?")))
    return render_template(
        "edit.html",
        note=None,
        all_threads=db.all_threads(),
        preselect_kind=preselect_kind,
        prefill=prefill,
        note_templates=note_templates.list_templates(),
        autosave=bool(settings.get("autosave", True)),
    )


@app.route("/journal/new")
def journal_new():
    """Create a new journal entry titled with the current date and time
    and redirect to its edit page. Always creates a fresh entry, even if
    other journal entries exist for today — this supports the
    interstitial-journaling pattern where a user logs multiple discrete
    moments throughout the day rather than appending to one rolling
    document. As of v3.17.4 this replaces the v3.14 'today\'s entry'
    behavior, which was a find-or-create lookup that opened the
    existing day\'s entry for editing. That mental model turned out to
    mismatch how users actually journal: they think of entries as
    discrete events, not as a single rolling document, and the previous
    behavior made it easy to accidentally clobber an earlier entry by
    clearing the form and writing fresh content.

    5.2: nothing is written until you type. The entry opens as a new note
    already titled and of kind journal, and its first save creates the file.
    Before, this GET created the file at once, so every ⇧⌘N left an entry,
    empty if you changed your mind."""
    from datetime import datetime
    now = datetime.now()
    title = now.strftime("%Y-%m-%d %H:%M")  # e.g. "2026-04-09 14:32"
    if _kind_locked("journal") and not vault.is_unlocked():
        return redirect(url_for("unlock_page", next=url_for("journal_new")))
    return render_template(
        "edit.html",
        note=None,
        all_threads=db.all_threads(),
        preselect_kind="journal",
        prefill={"slug": None, "name": "", "title": title, "body": "", "kind": "journal", "tags": []},
        note_templates=note_templates.list_templates(),
        autosave=bool(settings.get("autosave", True)),
        focus_body=True,
    )


# --- 5.2: trash and history housekeeping (Settings → Trash and history) ---

def _live_ids():
    ids = set(db.all_known_ids())
    for p in notes.iter_note_files():
        stem = p.name.split("-", 1)[0]
        if stem.isdigit():
            ids.add(stem)
    return ids


def _housekeeping():
    trashed = notes.trashed()
    trash_ids = {nid for _, nid in trashed}
    live = _live_ids()
    orphans = [i for i in history.ids_with_history() if i not in live and i not in trash_ids]
    trash_bytes = 0
    for p, _ in trashed:
        try:
            trash_bytes += p.stat().st_size
        except OSError:
            pass
    trash_versions, trash_hist_bytes = history.usage(i for i in trash_ids if i not in live)
    orphan_versions, orphan_bytes = history.usage(orphans)
    return {
        "trash": len(trashed), "trash_bytes": trash_bytes + trash_hist_bytes, "trash_versions": trash_versions,
        "orphans": len(orphans), "orphan_versions": orphan_versions, "orphan_bytes": orphan_bytes,
    }


def _size(n):
    return "%d KB" % max(1, round(n / 1024)) if n < 1024 * 1024 else "%.1f MB" % (n / 1024 / 1024)


app.jinja_env.filters["filesize"] = _size


@app.route("/trash/empty", methods=["POST"])
def trash_empty():
    """Delete the notes in .trash for good, with their history (unless a
    note with the same ID is back in the folder)."""
    live = _live_ids()
    gone = failed = 0
    for path, nid in notes.trashed():
        try:
            path.unlink()
            gone += 1
        except OSError:
            failed += 1
            continue
        if nid not in live:
            history.delete_history(nid)
    msg = "Deleted %s from the trash, with %s history." % (_plural(gone, "note"), "its" if gone == 1 else "their")
    if failed:
        msg += " %s couldn’t be deleted; remove %s in Finder." % (_plural(failed, "file"), "it" if failed == 1 else "them")
    return redirect(url_for("settings_page", hk_msg=msg) + "#housekeeping")


@app.route("/history/prune", methods=["POST"])
def history_prune():
    """Delete the history of notes that are neither in the folder nor in
    the trash (deleted in Finder, or the trash emptied by hand)."""
    live = _live_ids()
    trash_ids = {nid for _, nid in notes.trashed()}
    n = 0
    for nid in history.ids_with_history():
        if nid not in live and nid not in trash_ids:
            history.delete_history(nid)
            n += 1
    return redirect(url_for("settings_page", hk_msg="Deleted the history of %s." % _plural(n, "deleted note")) + "#housekeeping")


@app.route("/journal/today")
def journal_today():
    """Backward-compatibility alias. Redirects to journal_new so any
    bookmark or memorized URL from before v3.17.4 still works."""
    return redirect(url_for("journal_new"))


@app.route("/tags")
def tags_index():
    return render_template("tags.html", tags=db.all_tags())


@app.route("/tag/<tag_name>")
def tag_view(tag_name):
    return render_template("tag.html", tag=tag_name, notes=db.notes_by_tag(tag_name))


@app.route("/threads")
def threads_index():
    return render_template("threads.html", threads=db.all_threads())


@app.route("/thread/<thread_name>")
def thread_view(thread_name):
    return render_template(
        "thread.html", name=thread_name, chain=db.thread_chain(thread_name)
    )


@app.route("/help")
def help_page():
    """Help. The locked-notes section reports the exact crypto library and
    password settings this copy uses."""
    return render_template(
        "help.html",
        crypto_versions=vault.library_versions(),
        vault_kdf=vault.kdf_description() if vault.is_set_up() else "",
        argon2_available=vault.HAVE_ARGON2,
    )


@app.route("/citekeys")
def citekeys_index():
    return render_template("citekeys.html", entries=db.all_bib(), bib_path=settings.get_bib_path() or "")


@app.route("/api/citekeys")
def api_citekeys():
    """JSON endpoint for the in-editor citation picker."""
    q = request.args.get("q", "").strip()
    return jsonify(db.search_bib(q, limit=15))


@app.route("/api/notes/search")
def api_notes_search():
    """JSON for the [[ link picker and the command palette."""
    q = request.args.get("q", "").strip()[:100]
    exclude = request.args.get("exclude", "").strip() or None
    limit = min(max(int(request.args.get("limit", 8) or 8), 1), 20)
    return jsonify(db.quick_search(q, limit=limit, exclude_id=exclude))


@app.route("/api/notes/titles")
def api_notes_titles():
    """Titles for a comma-separated list of note ids (editor's 'Links in
    this note' list)."""
    ids = [i.strip() for i in request.args.get("ids", "").split(",") if i.strip()]
    return jsonify(db.titles_for(ids))


@app.route("/api/appearance", methods=["POST"])
def api_appearance():
    """Save appearance choices. Called by Settings → Appearance and by the
    command palette's theme commands; the page applies changes itself."""
    data = request.get_json(silent=True) or request.form.to_dict()
    cleaned = themes.clean(data)
    if not cleaned:
        return jsonify({"ok": False, "message": "Nothing to save."}), 400
    settings.save(cleaned)
    return jsonify({"ok": True, "saved": cleaned, "appearance": themes.current()})


@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    saved = False
    error = None
    notes_dir_changed = False
    if request.method == "POST":
        updates = {
            "bib_path": request.form.get("bib_path", "").strip(),
            "notes_dir": request.form.get("notes_dir", "").strip(),
            "export_dir": request.form.get("export_dir", "").strip(),
            "ai_chat_provider": request.form.get("ai_chat_provider", "").strip(),
            "ai_chat_model": request.form.get("ai_chat_model", "").strip(),
            "ai_chat_api_key": request.form.get("ai_chat_api_key", "").strip(),
            "ai_chat_base_url": request.form.get("ai_chat_base_url", "").strip(),
            "ai_embed_provider": request.form.get("ai_embed_provider", "").strip(),
            "ai_embed_model": request.form.get("ai_embed_model", "").strip(),
            "ai_embed_api_key": request.form.get("ai_embed_api_key", "").strip(),
            "ai_embed_base_url": request.form.get("ai_embed_base_url", "").strip(),
            # Checkbox: present in form data = True, absent = False
            "sanitize_note_html": request.form.get("sanitize_note_html") == "on",
            "autosave": request.form.get("autosave") == "on",
        }
        try:
            old_notes = settings.get_notes_dir()
            settings.save(updates)
            new_notes = settings.get_notes_dir()
            notes_dir_changed = old_notes != new_notes
            bib.restart_bib_watcher()
            saved = True
        except Exception as e:
            error = str(e)
    current = settings.load()
    env_override = os.environ.get("ZETTEL_BIB_PATH", "").strip()
    env_notes_override = os.environ.get("ZETTEL_NOTES_DIR", "").strip()
    return render_template(
        "settings.html",
        vault_msg=request.args.get("vault_msg", "")[:200],
        tpl_msg=request.args.get("tpl_msg", "")[:200],
        housekeeping=_housekeeping(),
        history_max=history.MAX_VERSIONS,
        hk_msg=request.args.get("hk_msg", "")[:300],
        upd_msg=request.args.get("upd_msg", "")[:200],
        note_templates=note_templates.list_templates(),
        vault_err=request.args.get("vault_err") == "1",
        vault_kdf=vault.kdf_description() if vault.is_set_up() else "",
        locked_count=len(db.locked_ids()),
        pending_kind_locks=len(_pending_kind_locks()),
        current=current,
        env_override=env_override,
        env_notes_override=env_notes_override,
        active_notes_dir=settings.get_notes_dir(),
        saved=saved,
        notes_dir_changed=notes_dir_changed,
        error=error,
        ai_status=ai.provider_status(),
        ai_providers=ai.PROVIDERS,
        ai_provider_labels=ai.PROVIDER_LABELS,
        ai_default_models=ai.DEFAULT_MODELS,
        ai_default_base_urls=ai.DEFAULT_BASE_URLS,
        ai_embed_providers=ai.EMBED_PROVIDERS,
        ai_default_embed_models=ai.DEFAULT_EMBED_MODELS,
        sqlite_vec_available=db.SQLITE_VEC_AVAILABLE,
        embedding_count=db.embedding_count() if db.SQLITE_VEC_AVAILABLE else 0,
        total_notes=len(db.all_note_ids()),
        active_export_dir=settings.get_export_dir(),
        env_export_override=os.environ.get("ZETTEL_EXPORT_DIR", "").strip(),
        retired_warnings=ai.retired_model_warnings(),
        theme_options=themes.THEMES,
        light_themes=themes.LIGHT_THEMES,
        dark_themes=themes.DARK_THEMES,
        theme_modes=themes.MODES,
        font_options=themes.FONTS,
        text_sizes=themes.TEXT_SIZES,
        accent_swatches=themes.ACCENT_SWATCHES,
    )


@app.route("/api/ai/test", methods=["POST"])
def api_ai_test():
    """Test the configured AI chat provider with a minimal call."""
    provider = ai.get_provider()
    if not provider:
        return jsonify({"ok": False, "message": "No AI provider configured. Pick one above and save first."})
    ok, msg = provider.test_connection()
    return jsonify({"ok": ok, "message": msg})


@app.route("/api/embed/reindex", methods=["POST"])
def api_embed_reindex():
    """Kick off a background re-embedding of all notes."""
    if not db.SQLITE_VEC_AVAILABLE:
        return jsonify({"ok": False, "message": "Semantic search is unavailable in this build (SQLite extension loading disabled). Other features are unaffected."})
    if not ai.get_embed_provider():
        return jsonify({"ok": False, "message": "No embed provider configured. Pick one above and save first."})
    started, msg = embed.start_reindex()
    return jsonify({"ok": started, "message": msg})


@app.route("/api/embed/status")
def api_embed_status():
    return jsonify(embed.reindex_status())


@app.route("/note/<note_id>/chat", methods=["GET", "POST"])
def note_chat(note_id):
    """Note interrogation chat. The conversation is held in memory and
    wiped on process restart — by design, not a bug."""
    meta = db.get_note_meta(note_id)
    if not meta:
        abort(404)
    note_path = notes.NOTES_DIR / meta["filename"]
    if not note_path.exists():
        abort(404)
    note = notes.parse_file(note_path)
    if note.locked:
        # Locked notes are never sent to an AI provider.
        return redirect(url_for("view", note_id=note_id))

    error = None
    if request.method == "POST":
        user_message = request.form.get("message", "").strip()
        if user_message:
            ok, response = chat.ask(note, user_message)
            if not ok:
                error = response
        return redirect(url_for("note_chat", note_id=note_id))

    conversation = chat.get_conversation(note_id)
    provider = ai.get_provider()
    return render_template(
        "note.html",
        rail="chat",
        conversation=conversation,
        error=error,
        provider=provider,
        **_note_page_context(note, _render_note_html(note.body)),
        suggested_prompts=[
            "What does this note assume?",
            "What's the strongest counterargument to this?",
            "Where is this argument weakest?",
            "What's missing from this argument?",
            "What evidence would refute this?",
        ],
    )


@app.route("/note/<note_id>/chat/clear", methods=["POST"])
def note_chat_clear(note_id):
    chat.clear_conversation(note_id)
    return redirect(url_for("note_chat", note_id=note_id))


@app.route("/note/<note_id>/chat/stream", methods=["POST"])
def note_chat_stream(note_id):
    """Server-Sent Events endpoint for streaming chat responses. The
    generator in chat.stream_ask() yields (kind, payload) tuples; we
    serialize them as SSE events so the browser can render tokens
    progressively.

    Event format:
        event: chunk    (data: raw content fragment)
        event: error    (data: error message)
        event: done     (no data; signals end of response)
    """
    meta = db.get_note_meta(note_id)
    if not meta:
        abort(404)
    note_path = notes.NOTES_DIR / meta["filename"]
    if not note_path.exists():
        abort(404)
    note = notes.parse_file(note_path)
    if note.locked:
        return Response('event: error\ndata: "Locked notes are never sent to an AI provider."\n\n',
                        mimetype="text/event-stream")

    user_message = request.form.get("message", "").strip()
    if not user_message:
        return Response("event: error\ndata: empty message\n\n", mimetype="text/event-stream")

    def generate():
        for kind, payload in chat.stream_ask(note, user_message):
            # Escape newlines in the data field since SSE treats \n\n as
            # end-of-event. JSON-encoding the payload handles this cleanly
            # and also escapes any other special characters.
            data_line = json.dumps(payload)
            yield f"event: {kind}\ndata: {data_line}\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disables buffering in reverse proxies
        },
    )


@app.route("/api/tags/suggest", methods=["POST"])
def api_tags_suggest():
    """Return AI-suggested tags for the given note content. Suggestions
    are constrained to existing tags in the corpus — the AI cannot
    invent new ones."""
    title = request.form.get("title", "").strip()
    body = request.form.get("body", "").strip()
    note_id = request.form.get("note_id", "").strip()
    kind = request.form.get("kind", "").strip().lower()
    if (note_id and db.is_locked(note_id)) or _kind_locked(kind):
        return jsonify({"ok": False, "tags": [], "message": "Locked notes are never sent to an AI provider."})
    if not title and not body:
        return jsonify({"ok": False, "tags": [], "message": "No content to analyze"})
    existing = [t for t, _ in db.all_tags()]
    ok, suggestions, msg = chat.suggest_tags(title, body, existing)
    return jsonify({"ok": ok, "tags": suggestions, "message": msg})


@app.route("/api/exports/reveal", methods=["POST"])
def api_exports_reveal():
    """Open the platform's file manager at the given path. Used by the
    export success page's 'Reveal in Finder' button."""
    target = request.form.get("path", "").strip()
    if not target:
        return jsonify({"ok": False, "message": "No path provided"})
    target_path = Path(target).expanduser()
    if not target_path.exists():
        return jsonify({"ok": False, "message": f"Path does not exist: {target}"})
    ok, message = _reveal(target_path)
    return jsonify({"ok": ok, "message": message})


def _reveal(target_path):
    """Show a file in Finder (selected in its folder). Returns (ok, message)."""
    system = platform.system()
    try:
        if system == "Darwin":
            # 'open -R' reveals the file in Finder (selects it in its parent folder)
            subprocess.run(["open", "-R", str(target_path)], check=False)
        elif system == "Windows":
            subprocess.run(["explorer", "/select,", str(target_path)], check=False)
        elif system == "Linux":
            # xdg-open opens the parent directory; can't reliably "select" the file
            parent = target_path.parent if target_path.is_file() else target_path
            subprocess.run(["xdg-open", str(parent)], check=False)
        else:
            return False, f"Unsupported platform: {system}"
    except Exception as e:
        return False, f"Failed to open file manager: {e}"
    return True, "Opened in file manager"


@app.route("/privacy")
def privacy_page():
    return render_template("privacy.html")


@app.route("/license")
def license_page():
    """Render the full LICENSE file as a readable page, with a short
    user-facing summary at the top. The file is read fresh on each
    request rather than at startup so that edits to LICENSE during
    development are reflected immediately without a restart."""
    license_path = Path(__file__).parent / "LICENSE"
    if license_path.exists():
        try:
            license_text = license_path.read_text(encoding="utf-8")
        except OSError as e:
            license_text = f"(Could not read LICENSE file: {e})"
    else:
        license_text = (
            "(LICENSE file not found in the project directory. This should "
            "not happen in a correctly-installed copy of Zetteling. The "
            "canonical license text is available at "
            "https://polyformproject.org/licenses/noncommercial/1.0.0/)"
        )
    return render_template("license.html", license_text=license_text)


@app.route("/about")
def about_page():
    return render_template("about.html")


@app.route("/stats")
def stats_page():
    return render_template(
        "stats.html",
        summary=db.stats_summary(),
        per_day=db.notes_per_day(30),
        most_linked=db.most_linked_notes(10),
        hours=db.hour_of_day_distribution(),
        all_tags=db.all_tags()[:15],
    )


@app.route("/split/<id1>/<id2>")
def split_view(id1, id2):
    left = _find_note_by_id(id1)
    right = _find_note_by_id(id2)
    if not left or not right:
        abort(404)
    left_html = _locked_placeholder(left) if left.unreadable else _render_note_html(left.body)
    right_html = _locked_placeholder(right) if right.unreadable else _render_note_html(right.body)
    return render_template(
        "split.html",
        left=left, right=right,
        left_html=left_html, right_html=right_html,
    )


@app.route("/note/<note_id>/split", methods=["GET"])
def split_picker(note_id):
    note = _find_note_by_id(note_id)
    if not note:
        abort(404)
    return render_template(
        "split_picker.html",
        note=note,
        recent=db.list_recent(50),
    )


@app.route("/export/<source_type>/<path:source_id>", methods=["GET", "POST"])
def export_view(source_type, source_id):
    if source_type not in ("note", "thread", "tag"):
        abort(404)

    notes_list = export.collect_notes_for_source(source_type, source_id)
    if not notes_list:
        abort(404)
    if any(n.unreadable for n in notes_list) or any(export.embeds_unreadable(n) for n in notes_list):
        return redirect(url_for("unlock_page", next=request.full_path.rstrip("?")))

    # Determine a sensible title for the export
    if source_type == "note":
        title = notes_list[0].title
    elif source_type == "thread":
        title = f"Thread: {source_id}"
    else:
        title = f"Tag: {source_id}"

    if request.method == "POST":
        fmt = request.form.get("format", "docx")
        citeproc = request.form.get("citeproc") == "on"
        embed_titles = request.form.get("embed_titles") == "on"
        markdown_text = export.build_combined_markdown(notes_list, title=title, embed_titles=embed_titles,
                                                       single=(source_type == "note"))
        output, error = export.run_pandoc(markdown_text, fmt, citeproc=citeproc)
        if error:
            return render_template(
                "export.html",
                source_type=source_type, source_id=source_id,
                title=title, notes_list=notes_list,
                formats=export.SUPPORTED_FORMATS,
                error=error,
                export_dir=settings.get_export_dir(),
                locked_titles=export.locked_included(notes_list),
            )
        ext = export.SUPPORTED_FORMATS[fmt]["ext"]
        # Filename: sanitized title + extension, with timestamp prefix to
        # avoid overwriting existing exports of the same source.
        from datetime import datetime
        safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in title)[:60]
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        export_dir = Path(settings.get_export_dir()).expanduser()
        try:
            export_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return render_template(
                "export.html",
                source_type=source_type, source_id=source_id,
                title=title, notes_list=notes_list,
                formats=export.SUPPORTED_FORMATS,
                error=f"Could not create export directory {export_dir}: {e}",
                export_dir=str(export_dir),
                locked_titles=export.locked_included(notes_list),
            )
        out_path = export_dir / f"{stamp}-{safe}.{ext}"
        out_path.write_bytes(output)
        return render_template(
            "export_success.html",
            file_path=str(out_path),
            file_name=out_path.name,
            export_dir=str(export_dir),
            source_type=source_type,
            source_id=source_id,
            title=title,
            fmt=fmt,
            size_kb=round(len(output) / 1024, 1),
        )

    return render_template(
        "export.html",
        source_type=source_type, source_id=source_id,
        title=title, notes_list=notes_list,
        formats=export.SUPPORTED_FORMATS,
        error=None,
        export_dir=settings.get_export_dir(),
        locked_titles=export.locked_included(notes_list),
    )


# ---------- 5.1 locked notes: routes ---------- #


def _wants_json():
    best = request.accept_mimetypes.best_match(["application/json", "text/html"])
    return best == "application/json" and request.accept_mimetypes[best] > request.accept_mimetypes["text/html"]


def _to_settings(msg, error=False):
    return redirect(url_for("settings_page", vault_msg=msg, vault_err="1" if error else None) + "#locked")


@app.route("/unlock", methods=["GET", "POST"])
def unlock_page():
    nxt = _safe_next(request.values.get("next"), url_for("index"))
    if not vault.is_set_up():
        return _to_settings("Set up locked notes first.", error=True)
    error = None
    if request.method == "POST":
        try:
            vault.unlock(request.form.get("password", ""))
        except vault.VaultError as e:
            error = str(e)
            if _wants_json():
                return jsonify({"ok": False, "message": error}), 403
        else:
            sweep_locked_kinds(force=True)
            if _wants_json():
                return jsonify({"ok": True})
            return redirect(nxt)
    elif vault.is_unlocked():
        return redirect(nxt)
    return render_template("unlock.html", next=nxt, error=error)


@app.route("/vault/lock", methods=["POST"])
def vault_lock():
    vault.lock()
    if _wants_json():
        return jsonify({"ok": True})
    return redirect(_safe_next(request.form.get("next"), url_for("index")))


@app.route("/api/vault/status")
def api_vault_status():
    return jsonify(_vault_state())


@app.route("/vault/setup", methods=["POST"])
def vault_setup():
    pw = request.form.get("password", "")
    if pw != request.form.get("confirm", ""):
        return _to_settings("The two passwords don't match.", error=True)
    try:
        code = vault.setup(pw)
    except vault.VaultError as e:
        return _to_settings(str(e), error=True)
    return render_template("vault_recovery.html", code=code, kdf=vault.kdf_description())


def _recovery_save_dirs():
    """Where the recovery key may be saved, in order: Downloads, then the
    home folder. Never inside the notes folder: the key mustn't sit beside
    the locked notes."""
    notes_dir = Path(notes.NOTES_DIR).expanduser().resolve()
    out = []
    for folder, label in ((Path.home() / "Downloads", "your Downloads folder"), (Path.home(), "your home folder")):
        try:
            folder = folder.resolve()
        except OSError:
            continue
        if folder.is_dir() and folder != notes_dir and notes_dir not in folder.parents:
            out.append((folder, label))
    return out


def _write_recovery_file(folder, text, today):
    stem = f"Zetteling recovery key {today.isoformat()}"
    path, n = folder / f"{stem}.txt", 2
    while path.exists():
        path, n = folder / f"{stem} ({n}).txt", n + 1
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return path


@app.route("/vault/recovery/save", methods=["POST"])
def vault_recovery_save():
    """5.2: save the recovery key shown after set-up as a text file, since
    the app window can't print. The key comes from the page (Zetteling
    never stores it) and is checked for the right format first."""
    from datetime import date
    try:
        code = vault.format_recovery(vault.parse_recovery(request.form.get("code", "")))
    except vault.VaultError as e:
        return jsonify({"ok": False, "message": str(e)}), 400
    today = date.today()
    text = (
        "Zetteling recovery key\n"
        "======================\n\n"
        f"{code}\n\n"
        f"Created {today.day} {today.strftime('%B %Y')}.\n\n"
        "This key opens your locked notes if you forget your password.\n"
        "Zetteling doesn't keep a copy.\n\n"
        "Keep it somewhere safe and away from your notes: a password manager,\n"
        "or printed and filed. Don't keep it in your notes folder or in a\n"
        "note, and delete this file once the key is stored elsewhere.\n\n"
        "To use it: Zetteling > Settings > Locked notes > Use recovery key.\n"
    )
    path = label = None
    errors = []
    for folder, where in _recovery_save_dirs():
        try:
            path, label = _write_recovery_file(folder, text, today), where
            break
        except OSError as e:  # macOS may refuse a folder (Privacy & Security)
            errors.append(e)
    if path is None:
        msg = ("Zetteling couldn’t save a file (macOS refused access%s). Use Copy instead, "
               "or allow access in System Settings → Privacy & Security → Files and Folders."
               % (": " + errors[0].strerror if errors and errors[0].strerror else ""))
        if _wants_json():
            return jsonify({"ok": False, "message": msg}), 200
        return render_template("vault_recovery.html", code=code, kdf=vault.kdf_description(), save_error=msg)
    if platform.system() == "Darwin":
        subprocess.run(["open", "-R", str(path)], check=False)
    msg = f"Saved to {label} as “{path.name}”. Move it somewhere safe, then delete it there."
    if _wants_json():
        return jsonify({"ok": True, "path": str(path), "message": msg})
    return render_template("vault_recovery.html", code=code, kdf=vault.kdf_description(), saved_msg=msg)


@app.route("/vault/password", methods=["POST"])
def vault_password():
    new = request.form.get("new_password", "")
    if new != request.form.get("confirm", ""):
        return _to_settings("The two new passwords don't match.", error=True)
    try:
        vault.change_password(request.form.get("old_password", ""), new)
    except vault.VaultError as e:
        return _to_settings(str(e), error=True)
    return _to_settings("Password changed. Your recovery key still works.")


@app.route("/vault/recover", methods=["GET", "POST"])
def vault_recover():
    error = None
    if request.method == "POST":
        new = request.form.get("new_password", "")
        if new != request.form.get("confirm", ""):
            error = "The two new passwords don't match."
        else:
            try:
                vault.recover(request.form.get("recovery", ""), new)
            except vault.VaultError as e:
                error = str(e)
            else:
                sweep_locked_kinds(force=True)
                return _to_settings("New password set. Your recovery key still works.")
    return render_template("vault_recover.html", error=error)


@app.route("/vault/settings", methods=["POST"])
def vault_settings():
    kinds = [k for k in request.form.getlist("locked_kinds") if k in KINDS]
    try:
        minutes = int(request.form.get("lock_after_minutes", "15"))
    except ValueError:
        minutes = 15
    if minutes not in (0, 5, 15, 30, 60):
        minutes = 15
    added = [k for k in kinds if k not in _locked_kinds()]
    if added and not vault.is_unlocked():
        return _to_settings("Unlock locked notes first, so existing notes of that kind can be locked.", error=True)
    settings.save({"locked_kinds": kinds, "lock_after_minutes": minutes})
    if added:  # ticking a kind locks all of it, earlier exceptions included
        newly = set()
        for k in added:
            newly.update(db.ids_for_kind(k))
        settings.save({"lock_exempt": [i for i in (settings.get("lock_exempt", []) or []) if i not in newly]})
    count = 0
    for k in added:
        for nid in db.ids_for_kind(k):
            note = _find_note_by_id(nid)
            if note and not note.locked and not note.unreadable:
                _lock_note(note)
                count += 1
    if count:
        db.scrub_index()
    msg = "Saved."
    if count:
        msg = "Saved. Locked %d existing note%s." % (count, "" if count == 1 else "s")
    return _to_settings(msg)


@app.route("/vault/remove-all", methods=["POST"])
def vault_remove_all():
    if not vault.is_unlocked():
        return _to_settings("Unlock locked notes first.", error=True)
    count = 0
    for nid in db.locked_ids():
        note = _find_note_by_id(nid)
        if note and note.locked and not note.unreadable:
            _unlock_note(note)
            count += 1
    settings.save({"locked_kinds": [], "lock_exempt": []})
    return _to_settings("Removed the lock from %d note%s. They are plain text again." % (count, "" if count == 1 else "s"))


@app.route("/note/<note_id>/lock", methods=["POST"])
def note_lock(note_id):
    note = _find_note_by_id(note_id)
    if not note:
        abort(404)
    if not vault.is_set_up():
        return _to_settings("Set up locked notes first: choose a password below.", error=True)
    if note.unreadable or not vault.is_unlocked():
        return redirect(url_for("unlock_page", next=url_for("view", note_id=note_id)))
    if not note.locked:
        _lock_note(note)
        db.scrub_index()
        _set_exempt(note_id, False)
        return redirect(url_for("view", note_id=note_id, locked=1))
    return redirect(url_for("view", note_id=note_id))


@app.route("/note/<note_id>/remove-lock", methods=["POST"])
def note_remove_lock(note_id):
    note = _find_note_by_id(note_id)
    if not note:
        abort(404)
    if note.unreadable:
        if note.damaged:
            return redirect(url_for("view", note_id=note_id))
        return redirect(url_for("unlock_page", next=url_for("view", note_id=note_id)))
    if note.locked:
        _unlock_note(note)
        if _kind_locked(note.kind):
            _set_exempt(note.id, True)  # don't lock it again by itself
        embed.embed_note_async(note)
    return redirect(url_for("view", note_id=note_id))


# ---------- 5.2 manuscripts ---------- #

_HEADING_LINE_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def _outline_items(body):
    """[(line index, 'heading'|'embed', level or note id, text)]"""
    items = []
    in_code = False
    for i, line in enumerate((body or "").split("\n")):
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        m = EMBED_LINE_RE.match(line)
        if m:
            items.append((i, "embed", m.group(1), m.group(2) or ""))
            continue
        h = _HEADING_LINE_RE.match(line)
        if h:
            items.append((i, "heading", len(h.group(1)), h.group(2)))
    return items


def _outline(note):
    """The manuscript's sections and embedded notes, with word counts."""
    rows, total = [], len(EMBED_LINE_RE.sub("", note.body or "").split())
    for n, (line, kind, a, b) in enumerate(_outline_items(note.body)):
        if kind == "heading":
            rows.append({"n": n, "kind": "heading", "level": a, "text": b})
            continue
        meta = db.get_note_meta(a)
        words = 0
        if meta and not meta.get("locked"):
            words = len((db.body_for(a) or "").split())
        total += words
        rows.append({"n": n, "kind": "embed", "id": a, "title": (meta or {}).get("title") or "Missing note",
                     "missing": meta is None, "locked": bool(meta and meta.get("locked")), "words": words})
    return {"items": rows, "words": total, "notes": sum(1 for r in rows if r["kind"] == "embed")}


@app.route("/manuscripts")
def manuscripts_page():
    rows = []
    for m in db.manuscripts():
        note = _find_note_by_id(m["id"])
        if not note:
            continue
        o = _outline(note) if not note.unreadable else {"notes": 0, "words": 0}
        rows.append({"id": note.id, "title": note.title, "modified": note.modified, "notes": o["notes"],
                     "words": o["words"], "locked": note.locked})
    return render_template("manuscripts.html", rows=rows)


@app.route("/manuscripts/new", methods=["POST"])
def manuscript_new():
    with _SAVE_LOCK:
        note = notes.create_note("Untitled manuscript", body="", tags=[], type_="manuscript", kind="research")
        db.upsert_note(note)
    return redirect(url_for("edit", note_id=note.id))


@app.route("/note/<note_id>/outline", methods=["POST"])
def manuscript_outline(note_id):
    """Reorder, remove or add the notes in a manuscript."""
    note = _find_note_by_id(note_id)
    if not note:
        abort(404)
    if note.unreadable:
        return redirect(url_for("unlock_page", next=url_for("view", note_id=note_id)))
    lines = (note.body or "").split("\n")
    items = _outline_items(note.body)
    action = request.form.get("action", "")
    try:
        n = int(request.form.get("n", "-1"))
    except ValueError:
        n = -1
    changed = False
    if action in ("up", "down", "remove") and 0 <= n < len(items):
        i = items[n][0]
        if action == "remove" and items[n][1] == "embed":
            del lines[i]
            changed = True
        elif action == "up" and n > 0:
            j = items[n - 1][0]
            lines[i], lines[j] = lines[j], lines[i]
            changed = True
        elif action == "down" and n < len(items) - 1:
            j = items[n + 1][0]
            lines[i], lines[j] = lines[j], lines[i]
            changed = True
    elif action == "add_note":
        m = re.search(r"(\d{14})", request.form.get("note", ""))
        if m and m.group(1) != note.id and db.get_note_meta(m.group(1)):
            lines += ["", "![[%s]]" % m.group(1)]
            changed = True
    elif action == "add_thread":
        ids = [c["id"] for c in db.thread_chain(request.form.get("thread", ""))]
        ids = [i for i in ids if i != note.id]
        if ids:
            lines += [""] + ["![[%s]]" % i for i in ids]
            changed = True
    elif action == "add_heading":
        text = request.form.get("heading", "").strip()
        if text:
            lines += ["", "## " + text]
            changed = True
    if changed:
        with _SAVE_LOCK:
            history.snapshot(note.path, "outline", force=True)
            note.body = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip("\n") + "\n"
            try:
                notes.write_note(note)
            except vault.VaultLocked:
                return redirect(url_for("unlock_page", next=url_for("view", note_id=note_id)))
            db.upsert_note(note)
    return redirect(url_for("view", note_id=note_id) + "#outline")


# ---------- 5.2 qualitative coding ---------- #


def _code_or_404(code):
    code = codebook.normalize(code)
    if not codebook.CODE_NAME_RE.match(code or ""):
        abort(404)
    return code


def _passage_rows(occ):
    """Group coded passages by note for display, with plain-text excerpts."""
    by_note = {}
    for o in occ:
        meta = db.get_note_meta(o["note_id"])
        if not meta:
            continue
        g = by_note.setdefault(o["note_id"], {"id": o["note_id"], "title": meta["title"], "kind": meta["kind"],
                                              "created": meta.get("created", ""), "locked": o["locked"], "passages": []})
        titles = db.titles_for(set(re.findall(r"\[\[(\d{14})", o["excerpt"])))
        g["passages"].append({"text": db._plain_text(o["excerpt"], titles).strip(), "codes": o["codes"]})
    return sorted(by_note.values(), key=lambda g: g["created"], reverse=True)


@app.route("/codes")
def codebook_page():
    rows, closed = codebook.summary()
    return render_template("codebook.html", rows=rows, closed=closed, msg=request.args.get("msg", "")[:400])


@app.route("/codes/<code>")
def code_page(code):
    code = _code_or_404(code)
    occ = codebook.occurrences(code)
    defs = codebook.load_definitions().get(code, {})
    together, same_notes = codebook.cooccurrence(code)
    _, closed = codebook.summary()
    return render_template(
        "code.html", code=code, groups=_passage_rows(occ), n_passages=len(occ),
        definition=defs, together=together[:12], same_notes=same_notes[:12], closed=closed,
        categories=sorted({d.get("category", "") for d in codebook.load_definitions().values() if d.get("category")}),
        msg=request.args.get("msg", "")[:400],
    )


@app.route("/codes/<code>/define", methods=["POST"])
def code_define(code):
    code = _code_or_404(code)
    codebook.set_definition(code, request.form.get("description"), request.form.get("category"))
    return redirect(url_for("code_page", code=code, msg="Saved."))


def _rewrite_code(old, new):
    """Rename/merge (new) or remove (new=None) a code in every note that has
    it. Returns (notes changed, locked notes that couldn't be read)."""
    ids = set(db.notes_with_code(old))
    if vault.is_unlocked():
        ids |= {o["note_id"] for o in codebook._locked_occurrences() if old in o["codes"]}
    changed = 0
    for nid in sorted(ids):
        note = _find_note_by_id(nid)
        if not note or note.unreadable:
            continue
        new_body = codebook.apply_rename(note.body, old, new)
        if new_body == note.body:
            continue
        with _SAVE_LOCK:
            history.snapshot(note.path, "rename-code", force=True)
            note.body = new_body
            notes.write_note(note)
            db.upsert_note(note)
        changed += 1
    defs = codebook.load_definitions()
    if old in defs:
        if new and new not in defs:
            defs[new] = defs[old]
        defs.pop(old, None)
        codebook.save_definitions(defs)
    closed = 0 if vault.is_unlocked() else len(db.locked_ids())
    return changed, closed


def _plural(n, word):
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


@app.route("/codes/<code>/rename", methods=["POST"])
def code_rename(code):
    old = _code_or_404(code)
    new = codebook.normalize(request.form.get("new_name", ""))
    if not codebook.CODE_NAME_RE.match(new or "") or new == old:
        return redirect(url_for("code_page", code=old, msg="Choose a different name: letters, numbers and hyphens."))
    merging = bool(db.notes_with_code(new)) or new in codebook.load_definitions()
    changed, closed = _rewrite_code(old, new)
    msg = "%s “%s” into “%s” in %s." % ("Merged" if merging else "Renamed", old, new, _plural(changed, "note"))
    if closed:
        msg += " %s couldn’t be checked because locked notes are closed." % _plural(closed, "locked note")
    return redirect(url_for("code_page", code=new, msg=msg))


@app.route("/codes/<code>/remove", methods=["POST"])
def code_remove(code):
    old = _code_or_404(code)
    changed, closed = _rewrite_code(old, None)
    msg = "Removed the code “%s” from %s. The passages themselves are unchanged." % (old, _plural(changed, "note"))
    if closed:
        msg += " %s couldn’t be checked because locked notes are closed." % _plural(closed, "locked note")
    return redirect(url_for("codebook_page", msg=msg))


@app.route("/codes/export", methods=["POST"])
def codes_export():
    """5.2: write the CSV to the export folder and show it in Finder. The
    app's window can't download files, so the old link did nothing there."""
    from datetime import datetime
    code = request.form.get("code") or None
    code = _code_or_404(code) if code else None
    back = url_for("code_page", code=code) if code else url_for("codebook_page")
    data = codebook.to_csv(code)
    n_locked = codebook.locked_rows(code)
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in (code or "codebook"))[:60]
    export_dir = Path(settings.get_export_dir()).expanduser()
    out = export_dir / ("%s-zetteling-%s.csv" % (datetime.now().strftime("%Y%m%d-%H%M%S"), safe))
    try:
        export_dir.mkdir(parents=True, exist_ok=True)
        with open(out, "x", encoding="utf-8", newline="") as f:
            f.write(data)
    except OSError as e:
        return redirect(_with_args(back, msg="Couldn’t save the CSV in %s: %s" % (export_dir, e.strerror or e)))
    revealed, _ = _reveal(out)
    msg = "Saved %s in your export folder%s." % (out.name, ", shown in Finder" if revealed else " (%s)" % export_dir)
    if n_locked:
        msg += " It includes %s from locked notes; the file is not encrypted." % _plural(n_locked, "passage")
    return redirect(_with_args(back, msg=msg))


def _with_args(url, **args):
    from urllib.parse import urlencode
    return url + ("&" if "?" in url else "?") + urlencode(args)


@app.route("/codes.csv")
def codes_csv():
    code = request.args.get("code")
    code = _code_or_404(code) if code else None
    data = codebook.to_csv(code)
    name = "zetteling-%s.csv" % (code or "codebook")
    return Response(data, mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=%s" % name})


@app.route("/api/codes")
def api_codes():
    return jsonify(codebook.suggestions(request.args.get("q", "")))


# ---------- 5.2 welcome tour ---------- #


# --- Updates (5.2): a daily check for a newer release on GitHub --------
# Nothing is downloaded or installed here: the page links to the release
# in the browser and the person installs the DMG as usual.

@app.route("/update")
def update_page():
    msg = request.args.get("msg", "")[:200]
    return render_template("update.html", upd=release_check.status(__version__),
                           opened=request.args.get("opened", ""), msg=msg,
                           checked=request.args.get("checked") == "1")


@app.route("/update/check", methods=["POST"])
def update_check():
    st = release_check.check(__version__, force=True)
    if st["error"]:
        msg = st["error"]
    elif st["newer"]:
        msg = f"Zetteling {st['latest']['version']} is available."
    else:
        msg = f"You have the latest version ({__version__})."
    nxt = _safe_next(request.form.get("next"), "/update")
    if nxt.startswith("/settings"):
        return redirect(url_for("settings_page", upd_msg=msg) + "#updates")
    return redirect(url_for("update_page", msg=msg, checked=1))


@app.route("/update/open", methods=["POST"])
def update_open():
    st = release_check.status(__version__)
    latest = st["latest"] or {}
    target = request.form.get("target", "page")
    if target == "download" and latest.get("dmg_url"):
        url = latest["dmg_url"]
    else:
        target = "page"
        url = latest.get("url") or release_check.RELEASES_PAGE
    ok = release_check.open_external(url)
    return redirect(url_for("update_page", opened=target if ok else "no"))


@app.route("/update/skip", methods=["POST"])
def update_skip():
    st = release_check.status(__version__)
    if st["latest"] and st["newer"]:
        settings.save({"update_skip": st["latest"]["version"]})
    return redirect(_safe_next(request.form.get("next"), url_for("update_page")))


@app.route("/update/settings", methods=["POST"])
def update_settings():
    on = request.form.get("update_check") == "on"
    changes = {"update_check": on}
    if on:
        changes["update_next_check"] = 0  # check on the next main page
    settings.save(changes)
    msg = ("Zetteling will check for new versions once a day." if on
           else "Update checks are off. Use Check now whenever you like.")
    return redirect(url_for("settings_page", upd_msg=msg) + "#updates")


@app.route("/open-link", methods=["POST"])
def open_link():
    """5.2: links to other websites (in notes, Privacy, Settings → AI) open
    in the browser. Without this the app window would load the site, with
    no way back. The page's click handler posts the link here."""
    data = request.get_json(silent=True) or {}
    url = data.get("url") if isinstance(data, dict) else None
    url = url or request.form.get("url")
    target = request.host_url.rstrip("/")
    if isinstance(url, str) and url.startswith(target + "/"):
        return jsonify({"ok": False, "error": "That link is part of Zetteling."}), 400
    if not release_check.open_in_browser(url):
        return jsonify({"ok": False, "error": "Zetteling only opens web and email links."}), 400
    return jsonify({"ok": True})


@app.route("/tour")
def tour():
    """Start the tour on the most recent note, so every stop has something
    to point at (or on the note list if there are no notes yet)."""
    step = request.args.get("step", "1")
    step = step if step.isdigit() and 0 < int(step) < 50 else "1"
    recent = db.list_recent_cards(1)
    if recent:
        return redirect(url_for("view", note_id=recent[0]["id"], tour=step))
    return redirect(url_for("index", tour=step))


@app.route("/api/tour/seen", methods=["POST"])
def tour_seen():
    settings.save({"tour_seen": True})
    return jsonify({"ok": True})


# ---------- 5.2 templates ---------- #


@app.route("/templates/new", methods=["GET", "POST"])
@app.route("/templates/<slug>/edit", methods=["GET", "POST"])
def template_edit(slug=None):
    t = note_templates.get(slug) if slug else None
    if slug and t is None:
        abort(404)
    if request.method == "POST":
        f = request.form
        try:
            note_templates.save(slug, f.get("name"), f.get("description"), f.get("kind"),
                                f.get("tags"), f.get("title"), f.get("body"))
        except OSError as e:
            return redirect(url_for("settings_page", tpl_msg=_templates_unwritable(e)) + "#templates")
        return redirect(url_for("settings_page", tpl_msg="Template saved.") + "#templates")
    return render_template("template_edit.html", t=t, slug=slug)


@app.route("/templates/<slug>/delete", methods=["POST"])
def template_delete(slug):
    try:
        note_templates.delete(slug)
    except OSError as e:
        return redirect(url_for("settings_page", tpl_msg=_templates_unwritable(e)) + "#templates")
    return redirect(url_for("settings_page", tpl_msg="Template deleted.") + "#templates")


def _templates_unwritable(e):
    return ("Couldn’t change the templates: Zetteling can’t write to the .templates folder in your notes "
            "folder (%s). Check the folder’s permissions in Finder." % (e.strerror or e))


@app.route("/templates/restore", methods=["POST"])
def templates_restore():
    try:
        n = note_templates.restore_defaults()
    except OSError as e:
        return redirect(url_for("settings_page", tpl_msg=_templates_unwritable(e)) + "#templates")
    msg = "Restored %d built-in template%s." % (n, "" if n == 1 else "s") if n else "All built-in templates are already there."
    return redirect(url_for("settings_page", tpl_msg=msg) + "#templates")


# ---------- 5.2 unlinked mentions ---------- #


@app.route("/note/<note_id>/link-mention", methods=["POST"])
def link_mention(note_id):
    """Turn the first plain mention of this note's title in another note
    into a link, keeping that note's wording as the link text."""
    target = _find_note_by_id(note_id)
    source = _find_note_by_id(request.form.get("source", ""))
    if not target or not source:
        abort(404)
    if source.locked or source.unreadable:
        abort(409)
    with _SAVE_LOCK:
        span = None
        for variant in db.title_variants(target.title):
            span = db.find_mention(source.body, variant)
            if span:
                break
        if span:
            history.snapshot(source.path, "link", force=True)
            s, e = span
            source.body = source.body[:s] + "[[%s|%s]]" % (target.id, source.body[s:e]) + source.body[e:]
            notes.write_note(source)
            db.upsert_note(source)
    return redirect(url_for("view", note_id=note_id) + "#mentions")


# ---------- 5.2 note history ---------- #


@app.route("/note/<note_id>/history")
def note_history(note_id):
    note = _find_note_by_id(note_id)
    if not note:
        abort(404)
    if note.unreadable and not note.damaged:
        return redirect(url_for("unlock_page", next=request.full_path.rstrip("?")))
    versions = history.list_versions(note_id)
    chosen = request.args.get("v") or (versions[0]["name"] if versions else "")
    version = history.read_version(note_id, chosen) if chosen else None
    if version is not None and version.unreadable and not version.damaged:
        return redirect(url_for("unlock_page", next=request.full_path.rstrip("?")))
    preview = diff = None
    if version is not None and not version.unreadable:
        preview = _render_note_html(version.body)
        diff = history.diff_lines(version.body, note.body)
    return render_template(
        "note_history.html", note=note, versions=versions, chosen=chosen,
        version=version, preview=preview, diff=diff,
        restored=request.args.get("restored") == "1",
    )


@app.route("/note/<note_id>/history/restore", methods=["POST"])
def note_history_restore(note_id):
    note = _find_note_by_id(note_id)
    name = request.form.get("v", "")
    version = history.read_version(note_id, name) if note else None
    if not note or version is None:
        abort(404)
    if note.unreadable or version.unreadable:
        if note.damaged or version.damaged:
            abort(409)
        return redirect(url_for("unlock_page", next=url_for("note_history", note_id=note_id, v=name)))
    try:
        with _SAVE_LOCK:
            history.snapshot(note.path, "restore", force=True)
            note.title, note.body, note.tags, note.kind = version.title, version.body, version.tags, version.kind
            notes.write_note(note)
            db.upsert_note(note)
    except vault.VaultLocked:
        return redirect(url_for("unlock_page", next=url_for("note_history", note_id=note_id, v=name)))
    embed.embed_note_async(note)
    return redirect(url_for("note_history", note_id=note_id, restored=1))


def _apply_once_settings():
    """One-time changes for installs upgrading to 5.2."""
    s = settings.load()
    if not s.get("html_safety_applied"):
        # Before 5.2 sanitising was off by default and every save wrote
        # "false", so a deliberate choice can't be told apart from the old
        # default. Turn it on once; Settings → Writing can turn it off.
        settings.save({"sanitize_note_html": True, "html_safety_applied": True})


def create_app():
    _apply_once_settings()
    db.init_db()
    watcher.start_watcher()
    bib.start_bib_watcher()
    return app


def main():
    # create_app() already ran at module import time below
    host = os.environ.get("ZETTEL_HOST", "127.0.0.1")
    port = int(os.environ.get("ZETTEL_PORT", "5005"))
    app.run(host=host, port=port, debug=False)


# Allow gunicorn to import as `app:application`
application = create_app()


if __name__ == "__main__":
    main()
