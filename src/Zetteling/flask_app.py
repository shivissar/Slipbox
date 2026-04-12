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
from . import settings
from . import watcher

__version__ = "4.0.2"

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
    """Make version and attribution available in every template."""
    return {
        "version": __version__,
        "attribution": "Created by Shiv Issar, Ph.D., inspired by Prof. Niklas Luhmann and Prof. Aneesh Aneesh",
        "pandoc_available": export.PANDOC_AVAILABLE,
    }


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
            el.set("title", tooltip.strip(" —"))
        else:
            el.set("class", "citekey broken")
            el.set("title", f"Unknown citekey: {citekey}")
        el.text = f"@{citekey}"
        return el, m.start(0), m.end(0)


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


# v3.17: optional HTML sanitization. Defaults OFF so existing users who
# legitimately use inline HTML in their notes see no change. Turn ON via
# Settings → sanitize_note_html if you ever import external markdown,
# sync notes with another person via Syncthing, or expose the app over
# the network. See the trust model section on the Privacy page.
_BLEACH_AVAILABLE = None
_bleach = None


def _get_bleach():
    """Lazy-import bleach so users who don't enable sanitization don't
    pay the dependency cost. Returns the bleach module or None."""
    global _BLEACH_AVAILABLE, _bleach
    if _BLEACH_AVAILABLE is None:
        try:
            import bleach as _bl
            _bleach = _bl
            _BLEACH_AVAILABLE = True
        except ImportError:
            _BLEACH_AVAILABLE = False
    return _bleach if _BLEACH_AVAILABLE else None


# Conservative allowlist. Covers everything Markdown normally generates
# (headings, lists, code blocks, tables, footnotes, links, emphasis)
# plus the specific classes Zetteling's wikilink and citekey extensions
# produce. Does NOT allow <script>, <iframe>, <object>, <embed>, or
# style/on* attributes.
_SAFE_TAGS = [
    "a", "abbr", "b", "blockquote", "br", "code", "em", "h1", "h2", "h3",
    "h4", "h5", "h6", "hr", "i", "img", "li", "ol", "p", "pre", "span",
    "strong", "sub", "sup", "table", "tbody", "td", "th", "thead", "tr",
    "ul", "div", "mark",
]
_SAFE_ATTRS = {
    "a": ["href", "title", "class"],
    "span": ["class"],
    "div": ["class"],
    "img": ["src", "alt", "title"],
    "code": ["class"],
    "th": ["align", "scope"],
    "td": ["align"],
}


def render_markdown(body):
    """Convert Markdown to HTML, optionally sanitizing if the user has
    opted in via the sanitize_note_html setting. The MD converter's
    .reset() is important because the same instance is reused across
    requests and carries footnote state between calls otherwise."""
    MD.reset()
    html = MD.convert(body)
    if settings.get("sanitize_note_html", False):
        bleach = _get_bleach()
        if bleach is not None:
            html = bleach.clean(
                html,
                tags=_SAFE_TAGS,
                attributes=_SAFE_ATTRS,
                strip=True,
            )
    return html


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


@app.route("/")
def index():
    kind = request.args.get("kind", "research").strip().lower()
    if kind not in ("research", "journal", "teaching", "life", "all"):
        kind = "research"
    filter_kind = None if kind == "all" else kind
    return render_template(
        "index.html",
        recent=db.list_recent(50, kind=filter_kind),
        active_kind=kind,
        kind_counts=db.kind_counts(),
    )


@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    results = db.search(q) if q else []
    if request.headers.get("HX-Request"):
        return render_template("_search_results.html", results=results, q=q)
    return render_template(
        "index.html",
        recent=db.list_recent(50, kind="research"),
        results=results,
        q=q,
        active_kind="research",
        kind_counts=db.kind_counts(),
    )


@app.route("/note/<note_id>")
def view(note_id):
    note = _find_note_by_id(note_id)
    if not note:
        abort(404)
    rendered = _render_note_html(note.body)
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
    similar = embed.find_related(note_id, k=8) if db.SQLITE_VEC_AVAILABLE else []
    chat_length = chat.conversation_length(note_id)
    chat_available = ai.get_provider() is not None
    return render_template(
        "note.html",
        note=note,
        rendered=rendered,
        backlinks=backlinks,
        thread_contexts=thread_contexts,
        similar=similar,
        chat_length=chat_length,
        chat_available=chat_available,
    )


@app.route("/note/<note_id>/edit", methods=["GET", "POST"])
def edit(note_id):
    note = _find_note_by_id(note_id)
    if not note:
        abort(404)
    if request.method == "POST":
        note.title = request.form.get("title", note.title).strip() or note.title
        note.body = request.form.get("body", "")
        tags_raw = request.form.get("tags", "")
        note.tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
        new_kind = request.form.get("kind", "").strip().lower()
        if new_kind in ("research", "journal", "teaching", "life"):
            note.kind = new_kind
        notes.write_note(note)
        db.upsert_note(note)
        embed.embed_note(note)
        return redirect(url_for("view", note_id=note.id))
    return render_template("edit.html", note=note)


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
        title = request.form.get("title", "").strip() or "Untitled"
        body = request.form.get("body", "")
        tags_raw = request.form.get("tags", "")
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
        thread_action = request.form.get("thread_action", "").strip()
        kind = request.form.get("kind", "research").strip().lower()
        if kind not in ("research", "journal", "teaching", "life"):
            kind = "research"

        # Create the note (without thread info initially)
        note = notes.create_note(title, body=body, tags=tags, kind=kind)

        # Handle thread workflow
        if thread_action == "new":
            thread_name = request.form.get("new_thread_name", "").strip()
            if thread_name:
                note.threads = [{"name": thread_name, "prev": None, "next": None}]
                notes.write_note(note)
        elif thread_action == "existing":
            thread_name = request.form.get("existing_thread_name", "").strip()
            if thread_name:
                # Find the current tail of this thread
                tail_id = db.thread_tail(thread_name)
                if tail_id:
                    # Set the new note as a continuation
                    note.threads = [{"name": thread_name, "prev": tail_id, "next": None}]
                    notes.write_note(note)
                    # Update the tail note to point at the new note
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
                else:
                    # Thread name was given but no existing notes — start fresh
                    note.threads = [{"name": thread_name, "prev": None, "next": None}]
                    notes.write_note(note)

        db.upsert_note(note)
        embed.embed_note(note)
        return redirect(url_for("view", note_id=note.id))
    preselect_kind = request.args.get("kind", "research").strip().lower()
    if preselect_kind not in ("research", "journal", "teaching", "life"):
        preselect_kind = "research"
    return render_template(
        "edit.html",
        note=None,
        all_threads=db.all_threads(),
        preselect_kind=preselect_kind,
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
    note = notes.create_note(
        title,
        body="",
        tags=[],
        kind="journal",
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
    return render_template("help.html")


@app.route("/citekeys")
def citekeys_index():
    return render_template("citekeys.html", entries=db.all_bib(), bib_path=settings.get_bib_path() or "")


@app.route("/api/citekeys")
def api_citekeys():
    """JSON endpoint for the in-editor citation picker."""
    q = request.args.get("q", "").strip()
    return jsonify(db.search_bib(q, limit=15))


@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    saved = False
    error = None
    notes_dir_changed = False
    if request.method == "POST":
        updates = {
            "bib_path": request.form.get("bib_path", "").strip(),
            "notes_dir": request.form.get("notes_dir", "").strip(),
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
        current=current,
        env_override=env_override,
        env_notes_override=env_notes_override,
        active_notes_dir=settings.get_notes_dir(),
        saved=saved,
        notes_dir_changed=notes_dir_changed,
        error=error,
        bibtexparser_available=bib.BIBTEXPARSER_AVAILABLE,
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
        "chat.html",
        note=note,
        conversation=conversation,
        error=error,
        provider=provider,
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
    left_html = _render_note_html(left.body)
    MD.reset()
    right_html = _render_note_html(right.body)
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
