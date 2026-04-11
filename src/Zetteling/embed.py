"""
embed.py — Embedding pipeline for semantic find-related-notes.

Three responsibilities:
1. Embed a single note when it's created or modified (called from app.py
   after notes.write_note + db.upsert_note). Failures are swallowed —
   the note still saves; the embedding gets retried on next reindex.

2. Bulk reindex of the entire corpus, run in a background thread with
   progress reported via a module-level state object. The /settings page
   triggers this and polls the status endpoint.

3. The user-facing find_related() function called by the note view.

The embedding provider is read fresh from settings on every call so the
user can switch backends without a restart.

v3.18: sqlite-vec dependency removed. Storage and similarity now go
through db.py's pure-Python BLOB+numpy path, which works inside the
Briefcase-packaged .dmg where C extension loading is unavailable.
"""

# Copyright (c) 2026 Shiv Issar
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import hashlib
import threading
import time
import traceback

try:
    from . import ai
    from . import db
    from . import notes
except ImportError:
    import ai
    import db
    import notes


# Length cap on text sent to embed APIs. Longer notes get truncated; the
# vast majority of zettel atomic notes are well under this. Cloud embed
# models typically charge per token, so the cap is also a cost guardrail.
MAX_EMBED_CHARS = 8000


def _truncate(text):
    return text[:MAX_EMBED_CHARS] if len(text) > MAX_EMBED_CHARS else text


def _embed_text_for_note(note):
    """The text we actually send to the embed API. Title + body, since
    the title carries a lot of conceptual signal in atomic notes."""
    return _truncate(f"{note.title}\n\n{note.body}")


def _content_hash(text, provider_name, model_name):
    """SHA-256 of the exact text + model pair. Used to skip re-embedding
    when a note is saved without content changes. The model is part of
    the hash because the same text embedded by two different models is
    genuinely different data."""
    h = hashlib.sha256()
    h.update(text.encode("utf-8", errors="replace"))
    h.update(b"\x00")
    h.update((provider_name or "").encode("utf-8"))
    h.update(b"\x00")
    h.update((model_name or "").encode("utf-8"))
    return h.hexdigest()


def _provider_signature(provider):
    """A 'provider/model' string used to detect when the user has
    switched providers or models since the last embedding pass."""
    name = getattr(provider, "name", "unknown")
    model = getattr(provider, "model", "unknown")
    return f"{name}/{model}"


def _check_provider_change(provider):
    """If the user has switched providers or models since the last
    embedding was written, drop all stored vectors so we don't end up
    with mixed-model vectors that would produce nonsense similarity
    scores. The embed_meta row is updated to the new signature so the
    next call is a no-op."""
    current = _provider_signature(provider)
    stored = db.get_embed_meta("provider_signature")
    if stored is None:
        db.set_embed_meta("provider_signature", current)
        return False
    if stored != current:
        print(
            f"[embed] provider changed: {stored!r} -> {current!r}. "
            f"Dropping stale vectors; old embeddings will be regenerated."
        )
        db.drop_vec_table()
        db.set_embed_meta("provider_signature", current)
        return True
    return False


# ---------- Reindex state ---------- #

class _ReindexState:
    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.total = 0
        self.processed = 0
        self.errors = 0
        self.last_error = ""
        self.started_at = 0.0
        self.finished_at = 0.0

    def snapshot(self):
        with self.lock:
            return {
                "running": self.running,
                "total": self.total,
                "processed": self.processed,
                "errors": self.errors,
                "last_error": self.last_error,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
            }


_state = _ReindexState()


def reindex_status():
    return _state.snapshot()


# ---------- Single-note embedding ---------- #


def embed_note(note):
    """Embed a single note. Returns True on success, False on failure.
    Failures are logged but not raised — embedding is best-effort, and
    a missed note will be picked up by the next manual reindex.

    - Checks for provider/model changes and drops stale vectors if needed
    - Skips the API call if the content+model hash matches what was
      already embedded (avoids redundant re-embedding on unchanged saves)
    - Retries up to 3 times with exponential backoff on transient errors
    """
    provider = ai.get_embed_provider()
    if not provider:
        return False

    _check_provider_change(provider)

    text = _embed_text_for_note(note)
    provider_name = getattr(provider, "name", "")
    model_name = getattr(provider, "model", "")
    new_hash = _content_hash(text, provider_name, model_name)

    # Hash cache: skip the API call if content and model are unchanged
    existing_hash = db.get_embedding_hash(note.id)
    if existing_hash is not None and existing_hash == new_hash:
        return True

    provider_model = _provider_signature(provider)

    # Retry with exponential backoff on transient errors. Permanent
    # errors (missing API key, not-found model) are not worth retrying.
    last_err = None
    for attempt in range(3):
        try:
            vec = provider.embed(text)
            db.store_embedding(
                note.id, vec,
                content_hash=new_hash,
                provider_model=provider_model,
            )
            return True
        except ai.AIError as e:
            msg = str(e).lower()
            if any(s in msg for s in ("api key", "not found", "rejected", "not installed")):
                print(f"[embed] permanent failure for {note.id}: {e}")
                return False
            last_err = e
        except Exception as e:
            last_err = e
        if attempt < 2:
            delay = 2 ** attempt  # 1, 2, 4 seconds
            time.sleep(delay)

    print(f"[embed] failed for {note.id} after 3 attempts: {last_err}")
    return False


def delete_embedding_for_note(note_id):
    """Convenience wrapper called when a note is soft-deleted."""
    db.delete_embedding(note_id)


# ---------- Bulk reindex ---------- #


def _reindex_worker():
    """Background worker. Drops all existing embeddings (so dimension
    changes are handled cleanly) and re-embeds every note in the corpus."""
    global _state
    try:
        provider = ai.get_embed_provider()
        if not provider:
            with _state.lock:
                _state.running = False
                _state.last_error = "No embed provider configured"
                _state.finished_at = time.time()
            return

        ids = db.all_note_ids()

        # Drop and clear so dimension changes are handled and stale
        # entries from deleted notes are removed.
        db.drop_vec_table()
        db.set_embed_meta("provider_signature", _provider_signature(provider))

        with _state.lock:
            _state.total = len(ids)
            _state.processed = 0
            _state.errors = 0
            _state.last_error = ""

        provider_name = getattr(provider, "name", "")
        model_name = getattr(provider, "model", "")
        provider_model = _provider_signature(provider)

        for note_id in ids:
            meta = db.get_note_meta(note_id)
            if not meta:
                continue
            path = notes.NOTES_DIR / meta["filename"]
            if not path.exists():
                continue
            try:
                note = notes.parse_file(path)
                text = _embed_text_for_note(note)
                vec = provider.embed(text)
                db.store_embedding(
                    note.id, vec,
                    content_hash=_content_hash(text, provider_name, model_name),
                    provider_model=provider_model,
                )
            except Exception as e:
                with _state.lock:
                    _state.errors += 1
                    _state.last_error = f"{note_id}: {e}"
                print(f"[embed/reindex] failed for {note_id}: {e}")
            with _state.lock:
                _state.processed += 1
    except Exception as e:
        traceback.print_exc()
        with _state.lock:
            _state.last_error = f"reindex crashed: {e}"
    finally:
        with _state.lock:
            _state.running = False
            _state.finished_at = time.time()


def start_reindex():
    """Kick off a background reindex. Returns immediately. Returns
    (started: bool, message: str). If a reindex is already running,
    returns (False, ...) without starting another."""
    global _state
    with _state.lock:
        if _state.running:
            return False, "Reindex already in progress"
        _state.running = True
        _state.started_at = time.time()
        _state.finished_at = 0.0
        _state.processed = 0
        _state.total = 0
        _state.errors = 0
        _state.last_error = ""
    t = threading.Thread(target=_reindex_worker, daemon=True)
    t.start()
    return True, "Reindex started"


# ---------- Find related (user-facing) ---------- #


def find_related(note_id, k=8):
    """Return [{id, title, distance, kind}, ...] for the k most similar notes."""
    return db.find_similar(note_id, k=k)
