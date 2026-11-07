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
import os
import xml.etree.ElementTree as etree

import markdown
from flask import Flask, Response, abort, jsonify, redirect, render_template, request, url_for
from markdown.extensions import Extension
from markdown.inlinepatterns import InlineProcessor
from werkzeug.middleware.proxy_fix import ProxyFix

from flask import send_file
from markupsafe import Markup
import io
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
from . import settings
from . import themes
from . import watcher

__version__ = "5.1.0"

app = Flask(__name__)

# Trust X-Forwarded-* from a reverse proxy (Caddy). Without this, url_for
# generates http:// URLs even when the user came in over https.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config["SECRET_KEY"] = os.environ.get("ZETTEL_SECRET_KEY", "dev-only-change-me")

# v3.17: cap request body size. 10MB is generous for a single Markdown
# note but small enough to prevent a pathological POST from filling disk
# or RAM. Flask aborts with a 413 Request Entity Too Large if exceeded.
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024


def _warn_if_insecure_secret_key():
    """Log a loud warning at startup if SECRET_KEY is still the default
    AND the app is bound to anything other than loopback. For local
    single-user use the default is fine because Zetteling does not use
    sessions for anything privileged. For remote deployment it is a
    real concern and the operator should set ZETTEL_SECRET_KEY in the
    environment."""
    if app.config["SECRET_KEY"] != "dev-only-change-me":
        return
    host = os.environ.get("ZETTEL_HOST", "127.0.0.1").strip()
    if host in ("127.0.0.1", "localhost", "::1", ""):
        return
    print(
        "\n" + "=" * 70 + "\n"
        "  WARNING: ZETTEL_SECRET_KEY is not set.\n"
        f"  The app is bound to {host}, which is not loopback.\n"
        "  Set ZETTEL_SECRET_KEY to a strong random string (for example\n"
        "  the output of: python -c 'import secrets; print(secrets.token_hex(32))')\n"
        "  before exposing this install to any untrusted network.\n"
        + "=" * 70 + "\n"
    )


_warn_if_insecure_secret_key()


@app.context_processor
def inject_globals():
    """Make version, attribution, appearance and sidebar counts available
    in every template."""
    return {
        "version": __version__,
        "attribution": "Created by Shiv Issar, Ph.D., inspired by Prof. Niklas Luhmann and Prof. Aneesh Aneesh",
        "pandoc_available": export.PANDOC_AVAILABLE,
        "appearance": themes.current(),
        "nav_counts": db.sidebar_counts(),
        "kinds": KINDS,
        "vault_state": _vault_state(),
    }


KINDS = ("research", "journal", "teaching", "life")


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
    request as activity (unless it's a background request)."""
    vault.autolock_if_idle(_lock_after_minutes())
    path = request.path or ""
    if not path.startswith(_BACKGROUND_PATHS):
        vault.touch()


@app.after_request
def _no_store(response):
    """Pages and API replies can contain decrypted locked notes; keep them
    out of the web view's on-disk cache. Static files still cache."""
    if not (request.path or "").startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
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
    """Encrypt a readable note in place. The caller scrubs the index."""
    note.locked = True
    notes.write_note(note)
    db.upsert_note(note)
    embed.delete_embedding_for_note(note.id)
    chat.clear_conversation(note.id)


def _unlock_note(note):
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


MD = markdown.Markdown(
    extensions=[
        "fenced_code",
        "tables",
        "footnotes",
        "codehilite",
        WikilinkExtension(),
        CitekeyExtension(),
    ],
    extension_configs={
        "codehilite": {
            "css_class": "codehilite",
            "guess_lang": False,
        },
    },
)


# v3.17: optional HTML sanitization, off by default so existing users who
# legitimately use inline HTML in their notes see no change. Turn it on in
# Settings → Writing if you ever import external Markdown. v5.0 removed a
# second, unused copy of this renderer (render_markdown) that had a
# slightly different allowlist; _render_note_html is the only one.


def _render_note_html(body):
    """Convert a note body to HTML and optionally sanitize. Sanitization
    is gated on the `sanitize_note_html` setting (default False) which
    users enable when they import external markdown from an untrusted
    source. When enabled, the output is passed through bleach.clean()
    with a conservative allowlist that keeps markdown's visual features
    but strips scripts, event handlers, and other XSS vectors.

    If sanitization is on but bleach isn't installed, falls back to
    unsanitized rendering with a one-time console warning so the user
    knows to install it."""
    MD.reset()
    raw = MD.convert(body)
    if not settings.get("sanitize_note_html", False):
        return raw
    try:
        import bleach
    except ImportError:
        if not getattr(_render_note_html, "_warned", False):
            print(
                "[app] sanitize_note_html is enabled but the 'bleach' package "
                "is not installed. Falling back to unsanitized output. "
                "Install it with: .venv/bin/pip install bleach"
            )
            _render_note_html._warned = True
        return raw
    allowed_tags = [
        "p", "br", "hr", "pre", "code", "blockquote",
        "h1", "h2", "h3", "h4", "h5", "h6",
        "strong", "em", "b", "i", "u", "s", "del", "mark", "sub", "sup",
        "ul", "ol", "li",
        "table", "thead", "tbody", "tr", "th", "td",
        "a", "img",
        "div", "span",
        "dl", "dt", "dd",
    ]
    allowed_attrs = {
        "a": ["href", "title", "class"],
        "img": ["src", "alt", "title"],
        "code": ["class"],
        "pre": ["class"],
        "span": ["class"],
        "div": ["class"],
        "th": ["align"],
        "td": ["align"],
    }
    return bleach.clean(
        raw,
        tags=allowed_tags,
        attributes=allowed_attrs,
        protocols=["http", "https", "mailto"],
        strip=True,
    )


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
        updates = {"welcomed": True}
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
    if request.headers.get("HX-Request"):
        return render_template("_search_results.html", results=results, q=q)
    return render_template(
        "index.html",
        list_cards=_list_for("all"),
        results=results,
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
    backlinks = db.get_backlinks(note_id)
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
            embed.delete_embedding_for_note(note.id)
            db.scrub_index()
        elif fresh and mode != "auto":
            embed.embed_note(note)
        if mode == "save":
            return redirect(url_for("view", note_id=note.id))
        return jsonify({"ok": True, "id": note.id, "modified": note.modified, "locked": note.locked})
    if note.unreadable:
        if note.damaged:
            return redirect(url_for("view", note_id=note.id))
        return redirect(url_for("unlock_page", next=url_for("edit", note_id=note.id)))
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
            embed.embed_note(note)
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
    if _kind_locked(preselect_kind) and not vault.is_unlocked():
        return redirect(url_for("unlock_page", next=request.full_path.rstrip("?")))
    return render_template(
        "edit.html",
        note=None,
        all_threads=db.all_threads(),
        preselect_kind=preselect_kind,
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
    clearing the form and writing fresh content."""
    from datetime import datetime
    now = datetime.now()
    title = now.strftime("%Y-%m-%d %H:%M")  # e.g. "2026-04-09 14:32"
    if _kind_locked("journal") and not vault.is_unlocked():
        return redirect(url_for("unlock_page", next=url_for("journal_new")))
    note = notes.create_note(
        title,
        body="",
        tags=[],
        kind="journal",
        locked=_kind_locked("journal"),
    )
    db.upsert_note(note)
    embed.embed_note(note)
    return redirect(url_for("edit", note_id=note.id))


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
        vault_err=request.args.get("vault_err") == "1",
        vault_kdf=vault.kdf_description() if vault.is_set_up() else "",
        locked_count=len(db.locked_ids()),
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
            return jsonify({"ok": False, "message": f"Unsupported platform: {system}"})
    except Exception as e:
        return jsonify({"ok": False, "message": f"Failed to open file manager: {e}"})
    return jsonify({"ok": True, "message": "Opened in file manager"})


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
    MD.reset()
    left_html = _locked_placeholder(left) if left.unreadable else _render_note_html(left.body)
    MD.reset()
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
    if any(n.unreadable for n in notes_list):
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
        markdown_text = export.build_combined_markdown(notes_list, title=title)
        output, error = export.run_pandoc(markdown_text, fmt, citeproc=citeproc)
        if error:
            return render_template(
                "export.html",
                source_type=source_type, source_id=source_id,
                title=title, notes_list=notes_list,
                formats=export.SUPPORTED_FORMATS,
                error=error,
                export_dir=settings.get_export_dir(),
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
    settings.save({"locked_kinds": []})
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
        embed.embed_note(note)
    return redirect(url_for("view", note_id=note_id))


def create_app():
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
