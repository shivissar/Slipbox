"""
db.py — SQLite + FTS5 index for the Zettelkasten.

The filesystem is the source of truth. This database is a disposable
index, rebuilt from disk on startup and kept in sync by the watcher.
Delete zettel.db at any time and it will be regenerated from the notes/
directory on next launch.

v3.18: sqlite-vec replaced with pure-Python cosine distance over numpy
arrays stored as BLOBs. Briefcase's bundled Python on macOS is built
without enable_load_extension support, so the C extension can't load
inside the packaged .dmg. Pure-Python is fast enough at personal-
Zettelkasten scale (single-digit ms for thousands of notes) and ships
with no native dependencies beyond numpy.
"""

# Copyright (c) 2026 Shiv Issar
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import json
import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import numpy as np

# Always True now — pure-Python path has no optional native dependency.
# Kept as a module constant so any leftover `if db.SQLITE_VEC_AVAILABLE`
# checks elsewhere keep working until we clean them up in embed.py.
SQLITE_VEC_AVAILABLE = True

DB_PATH = Path(os.environ.get("ZETTEL_DB_PATH", Path(__file__).parent / "zettel.db"))


SCHEMA = """
CREATE TABLE IF NOT EXISTS notes_meta (
    id           TEXT PRIMARY KEY,
    filename     TEXT NOT NULL UNIQUE,
    title        TEXT,
    created      TEXT,
    modified     TEXT,
    type         TEXT,
    kind         TEXT DEFAULT 'research',
    tags_json    TEXT,
    threads_json TEXT,
    refs_json    TEXT,
    mtime        REAL
);
CREATE INDEX IF NOT EXISTS idx_notes_kind ON notes_meta(kind);
CREATE INDEX IF NOT EXISTS idx_notes_modified ON notes_meta(modified);

CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    id UNINDEXED, title, body, tags,
    tokenize = 'porter unicode61'
);

CREATE TABLE IF NOT EXISTS links (
    src_id TEXT NOT NULL,
    dst_id TEXT NOT NULL,
    PRIMARY KEY (src_id, dst_id)
);
CREATE INDEX IF NOT EXISTS idx_links_dst ON links(dst_id);

-- 5.2 qualitative coding: one row per (passage, code); never for locked notes
CREATE TABLE IF NOT EXISTS codes (
    note_id TEXT NOT NULL,
    code    TEXT NOT NULL,
    pos     INTEGER NOT NULL,
    codes_json TEXT NOT NULL,
    excerpt TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_codes_code ON codes(code);
CREATE INDEX IF NOT EXISTS idx_codes_note ON codes(note_id);

CREATE TABLE IF NOT EXISTS thread_membership (
    thread_name TEXT NOT NULL,
    note_id     TEXT NOT NULL,
    prev_id     TEXT,
    next_id     TEXT,
    PRIMARY KEY (thread_name, note_id)
);
CREATE INDEX IF NOT EXISTS idx_thread_name ON thread_membership(thread_name);
CREATE INDEX IF NOT EXISTS idx_thread_note ON thread_membership(note_id);

CREATE TABLE IF NOT EXISTS bib_entries (
    citekey TEXT PRIMARY KEY,
    title TEXT, author TEXT, year TEXT, raw TEXT
);
CREATE INDEX IF NOT EXISTS idx_bib_year ON bib_entries(year);

CREATE TABLE IF NOT EXISTS embed_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS note_embeddings (
    note_id        TEXT PRIMARY KEY,
    vector         BLOB NOT NULL,
    dim            INTEGER NOT NULL,
    content_hash   TEXT,
    provider_model TEXT
);
CREATE INDEX IF NOT EXISTS idx_note_embeddings_dim ON note_embeddings(dim);
"""


@contextmanager
def get_conn():
    # timeout=10 prevents "database is locked" under contention between
    # the watcher thread and HTTP request handlers.
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        pass
    # 5.1: overwrite deleted rows with zeros, so text removed from the
    # index when a note is locked doesn't linger in free pages.
    conn.execute("PRAGMA secure_delete=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()


def init_db():
    # Pre-migration: the v3.17 note_embeddings table had columns
    # (vec_rowid, model_dim) and was paired with sqlite-vec's vec0
    # virtual table. The new SCHEMA below tries to create an index on
    # the `dim` column, which the old table doesn't have, so we have to
    # drop the old table BEFORE executescript runs — otherwise the whole
    # script aborts with "no such column: dim". Same goes for the
    # leftover vec_notes virtual table from sqlite-vec days.
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='note_embeddings'"
        ).fetchone()
        if existing:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(note_embeddings)").fetchall()}
            if "vec_rowid" in cols and "vector" not in cols:
                print("[db] migrating note_embeddings to v3.18 BLOB schema; reindex required")
                conn.execute("DROP TABLE note_embeddings")
                try:
                    conn.execute("DROP TABLE IF EXISTS vec_notes")
                except sqlite3.OperationalError:
                    pass
                conn.execute("DELETE FROM embed_meta WHERE key='dim'")

    with get_conn() as conn:
        conn.executescript(SCHEMA)
        # Migration: add kind column for databases that predate v3.14.
        try:
            conn.execute("ALTER TABLE notes_meta ADD COLUMN kind TEXT DEFAULT 'research'")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_kind ON notes_meta(kind)")
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise
        conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_modified ON notes_meta(modified)")
        # Migration: 5.1 locked notes.
        try:
            conn.execute("ALTER TABLE notes_meta ADD COLUMN locked INTEGER DEFAULT 0")
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise




def upsert_note(note):
    """Insert or update a note in meta, FTS, links, and thread tables."""
    locked = bool(getattr(note, "locked", False))
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO notes_meta
                (id, filename, title, created, modified, type, kind,
                 tags_json, threads_json, refs_json, mtime, locked)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                filename     = excluded.filename,
                title        = excluded.title,
                created      = excluded.created,
                modified     = excluded.modified,
                type         = excluded.type,
                kind         = excluded.kind,
                tags_json    = excluded.tags_json,
                threads_json = excluded.threads_json,
                refs_json    = excluded.refs_json,
                mtime        = excluded.mtime,
                locked       = excluded.locked
            """,
            (
                note.id,
                note.filename,
                note.title,
                note.created,
                note.modified,
                note.type,
                getattr(note, "kind", "research") or "research",
                json.dumps(note.tags),
                json.dumps(note.threads),
                json.dumps(note.refs),
                note.mtime,
                1 if locked else 0,
            ),
        )
        conn.execute("DELETE FROM notes_fts WHERE id = ?", (note.id,))
        conn.execute(
            "INSERT INTO notes_fts (id, title, body, tags) VALUES (?, ?, ?, ?)",
            # A locked note's text never enters the index, even while open.
            (note.id, note.title, "" if locked else note.body, " ".join(note.tags)),
        )
        conn.execute("DELETE FROM codes WHERE note_id = ?", (note.id,))
        if not locked:
            from . import codebook
            for codes, excerpt, pos in codebook.extract(note.body):
                cj = json.dumps(codes)
                conn.executemany(
                    "INSERT INTO codes (note_id, code, pos, codes_json, excerpt) VALUES (?, ?, ?, ?, ?)",
                    [(note.id, c, pos, cj, excerpt) for c in codes],
                )
        conn.execute("DELETE FROM links WHERE src_id = ?", (note.id,))
        for dst in (() if locked else note.outgoing_links):
            if dst != note.id:
                conn.execute(
                    "INSERT OR IGNORE INTO links (src_id, dst_id) VALUES (?, ?)",
                    (note.id, dst),
                )
        conn.execute("DELETE FROM thread_membership WHERE note_id = ?", (note.id,))
        for t in note.threads:
            if not isinstance(t, dict) or "name" not in t:
                continue
            conn.execute(
                """INSERT OR REPLACE INTO thread_membership
                   (thread_name, note_id, prev_id, next_id)
                   VALUES (?, ?, ?, ?)""",
                (
                    t["name"],
                    note.id,
                    str(t["prev"]) if t.get("prev") else None,
                    str(t["next"]) if t.get("next") else None,
                ),
            )


def delete_note(note_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM notes_meta WHERE id = ?", (note_id,))
        conn.execute("DELETE FROM notes_fts WHERE id = ?", (note_id,))
        conn.execute("DELETE FROM links WHERE src_id = ? OR dst_id = ?", (note_id, note_id))
        conn.execute("DELETE FROM thread_membership WHERE note_id = ?", (note_id,))
        conn.execute("DELETE FROM codes WHERE note_id = ?", (note_id,))


def get_backlinks(note_id, with_context=False):
    """Notes linking to note_id. With with_context, each also gets
    'context': (before, link text, after), the sentence around the first
    link, in plain text (empty for locked notes, whose text isn't indexed)."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT m.id, m.title, m.kind, f.body FROM links l
               JOIN notes_meta m ON m.id = l.src_id
               LEFT JOIN notes_fts f ON f.id = m.id
               WHERE l.dst_id = ? ORDER BY m.modified DESC""",
            (note_id,),
        ).fetchall()
    out = []
    target_title = None
    for r in rows:
        d = {"id": r["id"], "title": r["title"], "kind": r["kind"]}
        body = r["body"] or ""
        if with_context and body:
            m = re.search(r"\[\[" + re.escape(note_id) + r"(?:\|([^\]]+))?\]\]", body)
            if m:
                if target_title is None:
                    target_title = (titles_for([note_id]).get(note_id) or {}).get("title", "")
                titles = titles_for(set(x.group(1) for x in _LINK_RE.finditer(body)))
                d["context"] = context_around(body, m.start(), m.end(), m.group(1) or target_title, titles)
        out.append(d)
    return out


def _plain_text(text, titles):
    text = re.sub(r"\]\{[^}]*\}", "]", text)  # coded spans: drop the {.codes}
    text = _LINK_RE.sub(lambda m: m.group(2) or titles.get(m.group(1), {}).get("title", ""), text)
    text = _MD_NOISE_RE.sub(" ", text)
    return _SPACE_RE.sub(" ", text)


def context_around(body, start, end, shown, titles=None, width=110):
    """The sentence around body[start:end], as (before, shown, after)."""
    titles = titles or {}
    left, right = body[:start], body[end:]
    cut = -1
    for sep in ("\n\n", ". ", "? ", "! ", "\n- ", "\n* "):
        i = left.rfind(sep)
        if i >= 0:
            cut = max(cut, i + len(sep))
    before = left[cut:] if cut >= 0 else left
    m = re.search(r"[.!?](?=\s|$)|\n\n|\n[-*] ", right)
    after = right[:m.end()] if m else right
    before = _plain_text(before, titles).lstrip()
    after = _plain_text(after, titles).rstrip()
    if len(before) > width:
        before = "…" + before[-width:].split(" ", 1)[-1]
    if len(after) > width:
        after = after[:width].rsplit(" ", 1)[0] + "…"
    return (before, shown, after)


_MASK_RE = re.compile(r"\[\[[^\]]*\]\]|\[@[^\]]*\]|`[^`]*`|```.*?```|\]\{[^}]*\}|https?://\S+", re.S)


def find_mention(body, title):
    """First place `title` appears in body as ordinary text (not inside a
    link, citation, code or URL), matching whole words, any case, with any
    punctuation or spacing between the words. Returns (start, end) or None."""
    words = re.findall(r"\w+", title or "")
    if not words:
        return None
    masked = _MASK_RE.sub(lambda m: " " * len(m.group(0)), body)
    pat = re.compile(r"(?<!\w)" + r"\W+".join(re.escape(w) for w in words) + r"(?!\w)", re.I)
    m = pat.search(masked)
    return (m.start(), m.end()) if m else None


def title_variants(title):
    """The title, plus its main part before a subtitle (" — ", ": " ...),
    since notes usually mention the short form."""
    title = (title or "").strip()
    out = [title] if title else []
    main = re.split(r"\s+[—–-]\s+|:\s+", title, maxsplit=1)[0].strip()
    if main and main != title and len(main) >= 4:
        out.append(main)
    return out


def unlinked_mentions(note_id, title, limit=15):
    """Notes whose text mentions this note's title (or its main part, before
    a subtitle) but don't link to it. Locked notes are skipped (their text
    isn't indexed)."""
    variants = [v for v in title_variants(title) if len(v) >= 4]
    phrases = ['"' + " ".join(re.findall(r"\w+", v)) + '"' for v in variants]
    phrases = [p for p in phrases if p != '""']
    if not phrases:
        return []
    with get_conn() as conn:
        try:
            rows = conn.execute(
                """SELECT m.id, m.title, m.kind, f.body FROM notes_fts f
                   JOIN notes_meta m ON m.id = f.id
                   WHERE notes_fts MATCH ? AND m.id != ? AND COALESCE(m.locked, 0) = 0
                     AND NOT EXISTS (SELECT 1 FROM links l WHERE l.src_id = m.id AND l.dst_id = ?)
                   ORDER BY m.modified DESC LIMIT ?""",
                ("body : (" + " OR ".join(phrases) + ")", note_id, note_id, limit * 3),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    out = []
    for r in rows:
        body = r["body"] or ""
        span = None
        for v in variants:
            span = find_mention(body, v)
            if span:
                break
        if not span:
            continue  # FTS stemming matched something looser
        titles = titles_for(set(x.group(1) for x in _LINK_RE.finditer(body)))
        out.append({"id": r["id"], "title": r["title"], "kind": r["kind"],
                    "context": context_around(body, span[0], span[1], body[span[0]:span[1]], titles)})
        if len(out) >= limit:
            break
    return out


def get_outlinks(note_id):
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT l.dst_id AS id, m.title FROM links l
               LEFT JOIN notes_meta m ON m.id = l.dst_id
               WHERE l.src_id = ?""",
            (note_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def all_tags():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT tags_json FROM notes_meta WHERE tags_json IS NOT NULL"
        ).fetchall()
    counts = {}
    for r in rows:
        try:
            tags = json.loads(r["tags_json"])
        except (ValueError, TypeError) as e:
            print(f"[db] skipping malformed tags_json row: {e}")
            continue
        if not isinstance(tags, list):
            continue
        for tag in tags:
            counts[tag] = counts.get(tag, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def notes_by_tag(tag):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, title, modified, tags_json, kind FROM notes_meta ORDER BY modified DESC"
        ).fetchall()
    out = []
    for r in rows:
        try:
            tags = json.loads(r["tags_json"] or "[]")
        except (ValueError, TypeError):
            continue
        if not isinstance(tags, list):
            continue
        if tag in tags:
            out.append({
                "id": r["id"], "title": r["title"],
                "modified": r["modified"], "kind": r["kind"] or "research",
            })
    return out


def all_threads():
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT thread_name, COUNT(*) AS n FROM thread_membership
               GROUP BY thread_name ORDER BY thread_name"""
        ).fetchall()
        return [(r["thread_name"], r["n"]) for r in rows]


def thread_chain(thread_name):
    """Reconstruct ordered thread sequence by walking prev/next links."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT t.note_id AS id, t.prev_id, t.next_id, m.title, m.modified
               FROM thread_membership t JOIN notes_meta m ON m.id = t.note_id
               WHERE t.thread_name = ?""",
            (thread_name,),
        ).fetchall()
    if not rows:
        return []
    by_id = {r["id"]: dict(r) for r in rows}
    heads = [r for r in by_id.values() if not r["prev_id"] or r["prev_id"] not in by_id]
    if not heads:
        return sorted(
            ({"id": r["id"], "title": r["title"]} for r in by_id.values()),
            key=lambda x: x["id"],
        )
    chain = []
    seen = set()
    cur = heads[0]
    while cur and cur["id"] not in seen:
        seen.add(cur["id"])
        chain.append({"id": cur["id"], "title": cur["title"]})
        nxt_id = cur["next_id"]
        cur = by_id.get(nxt_id) if nxt_id else None
    for r in by_id.values():
        if r["id"] not in seen:
            chain.append({"id": r["id"], "title": r["title"]})
    return chain


def threads_for_note(note_id):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT thread_name, prev_id, next_id FROM thread_membership WHERE note_id = ?",
            (note_id,),
        ).fetchall()
        return [
            {"name": r["thread_name"], "prev_id": r["prev_id"], "next_id": r["next_id"]}
            for r in rows
        ]


def title_for(note_id):
    with get_conn() as conn:
        row = conn.execute("SELECT title FROM notes_meta WHERE id = ?", (note_id,)).fetchone()
        return row["title"] if row else None


def get_note_meta(note_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM notes_meta WHERE id = ?", (note_id,)).fetchone()
        return dict(row) if row else None


def list_recent(limit=50, kind=None):
    with get_conn() as conn:
        if kind:
            rows = conn.execute(
                "SELECT id, title, modified, type, kind FROM notes_meta "
                "WHERE kind = ? ORDER BY modified DESC LIMIT ?",
                (kind, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, title, modified, type, kind FROM notes_meta "
                "ORDER BY modified DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


def kind_counts():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT COALESCE(kind, 'research') AS k, COUNT(*) AS n "
            "FROM notes_meta GROUP BY k"
        ).fetchall()
    out = {r["k"]: r["n"] for r in rows}
    out["all"] = sum(out.values())
    for k in ("research", "journal", "teaching", "life"):
        out.setdefault(k, 0)
    return out


def search(query, limit=50):
    """Full-text search via FTS5. Returns ranked results."""
    if not query.strip():
        return []
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT n.id, n.title, n.modified,
                      snippet(notes_fts, 2, '<mark>', '</mark>', '...', 20) AS snip
               FROM notes_fts JOIN notes_meta n ON n.id = notes_fts.id
               WHERE notes_fts MATCH ? ORDER BY rank LIMIT ?""",
            (query, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def ids_mentioning(note_id):
    """Notes whose indexed text contains this 14-digit ID (5.2: used to find
    links to a deleted note, whose rows in `links` go with it). Locked
    notes' text isn't indexed, so they never appear."""
    if not (note_id or "").isdigit():
        return []
    with get_conn() as conn:
        try:
            rows = conn.execute("SELECT id FROM notes_fts WHERE notes_fts MATCH ?",
                                ('body : "%s"' % note_id,)).fetchall()
        except sqlite3.OperationalError:
            return []
    return [r["id"] for r in rows]


def indexed_mtimes():
    """{filename: (id, mtime)} for every indexed note (start-up shortcut)."""
    with get_conn() as conn:
        rows = conn.execute("SELECT id, filename, mtime FROM notes_meta").fetchall()
        return {r["filename"]: (r["id"], r["mtime"]) for r in rows}


def all_known_ids():
    with get_conn() as conn:
        rows = conn.execute("SELECT id FROM notes_meta").fetchall()
        return {r["id"] for r in rows}


# ---------- Bibliography (Zotero / BibTeX) ---------- #

def replace_bib_entries(entries):
    with get_conn() as conn:
        conn.execute("DELETE FROM bib_entries")
        conn.executemany(
            """INSERT INTO bib_entries (citekey, title, author, year, raw)
               VALUES (:citekey, :title, :author, :year, :raw)""",
            entries,
        )


def search_bib(q, limit=20):
    if not q:
        return []
    pat = f"%{q.lower()}%"
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT citekey, title, author, year FROM bib_entries
               WHERE LOWER(citekey) LIKE ? OR LOWER(title) LIKE ? OR LOWER(author) LIKE ?
               ORDER BY year DESC, citekey ASC LIMIT ?""",
            (pat, pat, pat, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def all_bib():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT citekey, title, author, year FROM bib_entries "
            "ORDER BY year DESC, citekey ASC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_bib_entry(citekey):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM bib_entries WHERE citekey = ?", (citekey,)).fetchone()
        return dict(row) if row else None


def thread_tail(thread_name):
    """Return the note_id at the tail of a thread (next_id is null), or None."""
    with get_conn() as conn:
        row = conn.execute(
            """SELECT note_id FROM thread_membership
               WHERE thread_name = ? AND next_id IS NULL
               ORDER BY note_id DESC LIMIT 1""",
            (thread_name,),
        ).fetchone()
        return row["note_id"] if row else None


# ---------- Stats helpers (v3.7) ---------- #


def stats_summary():
    """Return a dict of headline corpus statistics."""
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) AS n FROM notes_meta").fetchone()["n"]
        rows = conn.execute("SELECT body FROM notes_fts").fetchall()
    total_words = sum(len((r["body"] or "").split()) for r in rows)
    word_counts = sorted(len((r["body"] or "").split()) for r in rows)
    median_words = word_counts[len(word_counts) // 2] if word_counts else 0
    mean_words = total_words // total if total else 0
    return {
        "total_notes": total,
        "total_words": total_words,
        "mean_words": mean_words,
        "median_words": median_words,
    }


def notes_per_day(days=30):
    """Returns [(date_str, count), ...] for the last `days` days, oldest first."""
    from datetime import date, timedelta
    today = date.today()
    out = []
    with get_conn() as conn:
        for offset in range(days - 1, -1, -1):
            d = today - timedelta(days=offset)
            ds = d.isoformat()
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM notes_meta WHERE substr(created, 1, 10) = ?",
                (ds,),
            ).fetchone()
            out.append((ds, row["n"]))
    return out


def most_linked_notes(limit=10):
    """Notes with the most backlinks."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT n.id, n.title, COUNT(l.src_id) AS link_count
               FROM notes_meta n LEFT JOIN links l ON l.dst_id = n.id
               GROUP BY n.id HAVING link_count > 0
               ORDER BY link_count DESC, n.modified DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def hour_of_day_distribution():
    """How many notes were created at each hour of the day (0-23)."""
    with get_conn() as conn:
        rows = conn.execute("SELECT created FROM notes_meta WHERE created != ''").fetchall()
    counts = [0] * 24
    for r in rows:
        try:
            hour = int(r["created"][11:13])
            if 0 <= hour < 24:
                counts[hour] += 1
        except (ValueError, IndexError):
            continue
    return counts


# ---------- Embeddings storage (v3.18, pure-Python cosine) ---------- #
#
# Replaces the previous sqlite-vec / vec0 implementation. Embeddings are
# stored as float32 numpy BLOBs in the note_embeddings table, and
# similarity is computed in pure Python via numpy at query time. At
# personal-Zettelkasten scale (a few thousand notes, 1536-dim vectors)
# this runs in well under 50ms per query — fast enough that the absence
# of a vector index is not noticeable.


def _vec_to_blob(vector):
    """Serialize a Python list/tuple of floats to a float32 BLOB."""
    arr = np.asarray(vector, dtype=np.float32)
    return arr.tobytes()


def _blob_to_vec(blob, dim):
    """Deserialize a float32 BLOB back to a 1-D numpy array."""
    return np.frombuffer(blob, dtype=np.float32, count=dim)


def store_embedding(note_id, vector, content_hash=None, provider_model=None):
    """Store or replace the embedding for a note."""
    if vector is None or len(vector) == 0:
        raise ValueError("empty vector")
    dim = len(vector)
    blob = _vec_to_blob(vector)
    with get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO note_embeddings
               (note_id, vector, dim, content_hash, provider_model)
               VALUES (?, ?, ?, ?, ?)""",
            (note_id, blob, dim, content_hash, provider_model),
        )
        conn.execute(
            "INSERT OR REPLACE INTO embed_meta (key, value) VALUES ('dim', ?)",
            (str(dim),),
        )


def delete_embedding(note_id):
    """Remove the embedding for a deleted note."""
    with get_conn() as conn:
        conn.execute("DELETE FROM note_embeddings WHERE note_id = ?", (note_id,))


def drop_vec_table():
    """Clear all stored embeddings. Name kept for compatibility with embed.py."""
    with get_conn() as conn:
        conn.execute("DELETE FROM note_embeddings")
        conn.execute("DELETE FROM embed_meta WHERE key='dim'")


def vec_table_exists(conn=None):
    """Compatibility shim. The table always exists post-init_db; what
    callers actually want to know is whether any rows are present."""
    if conn is None:
        with get_conn() as c:
            return vec_table_exists(c)
    row = conn.execute("SELECT 1 FROM note_embeddings LIMIT 1").fetchone()
    return row is not None


def get_embed_dim():
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM embed_meta WHERE key='dim'").fetchone()
    return int(row["value"]) if row and row["value"] else None


def initialize_vec_table(dim):
    """Compatibility shim. The BLOB table is created by init_db() and
    needs no per-dimension setup. We just record the dim in embed_meta."""
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO embed_meta (key, value) VALUES ('dim', ?)",
            (str(dim),),
        )


def get_embed_meta(key):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM embed_meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def set_embed_meta(key, value):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO embed_meta (key, value) VALUES (?, ?)",
            (key, str(value)),
        )


def get_embedding_hash(note_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT content_hash FROM note_embeddings WHERE note_id = ?", (note_id,)
        ).fetchone()
        return row["content_hash"] if row else None


def get_embedding_fingerprint(note_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT content_hash, provider_model FROM note_embeddings WHERE note_id = ?",
            (note_id,),
        ).fetchone()
        if not row:
            return (None, None)
        return (row["content_hash"], row["provider_model"])


def set_embedding_hash(note_id, content_hash):
    with get_conn() as conn:
        conn.execute(
            "UPDATE note_embeddings SET content_hash = ? WHERE note_id = ?",
            (content_hash, note_id),
        )


def find_similar(note_id, k=8):
    """Return the k most similar notes by cosine distance.

    Loads all embeddings into memory, normalizes once, computes the
    query-vs-corpus dot product in a single numpy call, and returns
    cosine distance (1 - similarity) so smaller = more similar — matching
    what help.html and note.html already promise the user.

    At ~5000 notes with 1536-dim vectors this runs in well under 50ms.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT e.note_id, e.vector, e.dim, m.title, m.kind
               FROM note_embeddings e
               LEFT JOIN notes_meta m ON m.id = e.note_id"""
        ).fetchall()

    if not rows:
        return []

    query_vec = None
    ids, titles, kinds, vectors = [], [], [], []
    expected_dim = None
    for r in rows:
        dim = r["dim"]
        if expected_dim is None:
            expected_dim = dim
        elif dim != expected_dim:
            # Mixed dimensions shouldn't happen (drop_vec_table is called
            # on provider switch) but skip rather than crash if it does.
            continue
        v = _blob_to_vec(r["vector"], dim)
        if r["note_id"] == note_id:
            query_vec = v
        ids.append(r["note_id"])
        titles.append(r["title"] or "(unknown)")
        kinds.append(r["kind"] or "research")
        vectors.append(v)

    if query_vec is None or not vectors:
        return []

    matrix = np.vstack(vectors)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms = np.clip(norms, 1e-12, None)
    matrix_norm = matrix / norms
    q_norm_val = max(float(np.linalg.norm(query_vec)), 1e-12)
    q_norm = query_vec / q_norm_val

    similarities = matrix_norm @ q_norm
    distances = 1.0 - similarities  # cosine distance, smaller = more similar

    order = np.argsort(distances)
    out = []
    for idx in order:
        nid = ids[idx]
        if nid == note_id:
            continue
        out.append({
            "id": nid,
            "title": titles[idx],
            "distance": float(distances[idx]),
            "kind": kinds[idx],
        })
        if len(out) >= k:
            break
    return out


def embedding_count():
    with get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM note_embeddings").fetchone()
        return row["n"] if row else 0


def all_note_ids():
    with get_conn() as conn:
        rows = conn.execute("SELECT id FROM notes_meta ORDER BY id").fetchall()
        return [r["id"] for r in rows]


# ---------- v5.0 helpers: note list cards, quick search, sidebar counts ---------- #

_LINK_RE = re.compile(r"\[\[(\d{14})(?:\|([^\]]+))?\]\]")
_MD_NOISE_RE = re.compile(r"[#>*_`~\[\]()!|]+|(?<!\w)-(?!\w)")
_SPACE_RE = re.compile(r"\s+")


def _plain_snippet(body, limit=120, titles=None):
    """A one-line plain-text preview of a note body for list cards.
    Wikilinks become the linked note's title (or their alias)."""
    if not body:
        return ""
    titles = titles or {}
    text = _LINK_RE.sub(lambda m: m.group(2) or titles.get(m.group(1), {}).get("title", ""), body)
    text = _MD_NOISE_RE.sub(" ", text)
    text = _SPACE_RE.sub(" ", text).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "…"
    return text


def _first_thread(threads_json):
    try:
        threads = json.loads(threads_json or "[]")
    except (ValueError, TypeError):
        return None
    for t in threads if isinstance(threads, list) else []:
        if isinstance(t, dict) and t.get("name"):
            return t["name"]
    return None


def list_recent_cards(limit=50, kind=None):
    """Like list_recent, plus a short plain-text snippet and the first
    thread name, for the note list column."""
    sql = (
        "SELECT m.id, m.title, m.modified, m.kind, m.threads_json, m.locked, "
        "(SELECT substr(f.body, 1, 400) FROM notes_fts f WHERE f.id = m.id LIMIT 1) AS head "
        "FROM notes_meta m "
    )
    params = []
    if kind:
        sql += "WHERE m.kind = ? "
        params.append(kind)
    sql += "ORDER BY m.modified DESC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    linked = {m.group(1) for r in rows for m in _LINK_RE.finditer(r["head"] or "")}
    titles = titles_for(list(linked)) if linked else {}
    out = []
    for r in rows:
        out.append({
            "id": r["id"], "title": r["title"], "modified": r["modified"],
            "kind": r["kind"] or "research",
            "snippet": _plain_snippet(r["head"], titles=titles),
            "thread": _first_thread(r["threads_json"]),
            "locked": bool(r["locked"]),
        })
    return out


def _fts_prefix_query(q):
    """Turn free text into a safe FTS5 query where every word is a prefix
    match, e.g. 'waypo miss' -> '"waypo"* "miss"*'. Quotes inside words
    are dropped so user input can never break the MATCH syntax."""
    words = [w.replace('"', "") for w in re.findall(r"[\w'-]+", q, flags=re.UNICODE)]
    # The porter tokenizer stems query terms too, and it turns a final
    # "y" into "i" ("way" -> "wai"), which then fails to prefix-match
    # "waypoint". Dropping a trailing y keeps as-you-type search working.
    words = [w[:-1] if len(w) > 2 and w.lower().endswith("y") else w for w in words if w]
    return " ".join(f'"{w}"*' for w in words[:8])


def quick_search(q, limit=8, exclude_id=None):
    """Search for the link picker and the command palette. Title matches
    come first, then full-text prefix matches with a highlighted snippet.
    An empty query returns the most recently modified notes."""
    q = (q or "").strip()
    results, seen = [], set()
    if exclude_id:
        seen.add(exclude_id)
    if q and has_filters(parse_query(q)):
        rows = filtered_search(q, limit=limit + 1)
        return [{**r, "match": "body" if r.get("snip") else "filter"} for r in rows if r["id"] != exclude_id][:limit]
    with get_conn() as conn:
        if not q:
            rows = conn.execute(
                "SELECT id, title, modified, kind FROM notes_meta ORDER BY modified DESC LIMIT ?",
                (limit + 1,),
            ).fetchall()
            for r in rows:
                if r["id"] in seen:
                    continue
                seen.add(r["id"])
                results.append({**dict(r), "kind": r["kind"] or "research", "match": "recent", "snip": ""})
            return results[:limit]
        rows = conn.execute(
            "SELECT id, title, modified, kind FROM notes_meta "
            "WHERE LOWER(title) LIKE ? ORDER BY "
            "CASE WHEN LOWER(title) LIKE ? THEN 0 ELSE 1 END, modified DESC LIMIT ?",
            (f"%{q.lower()}%", f"{q.lower()}%", limit),
        ).fetchall()
        for r in rows:
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            results.append({**dict(r), "kind": r["kind"] or "research", "match": "title", "snip": ""})
        fts = _fts_prefix_query(q)
        if fts and len(results) < limit:
            try:
                rows = conn.execute(
                    """SELECT n.id, n.title, n.modified, n.kind,
                              snippet(notes_fts, 2, '<mark>', '</mark>', '…', 10) AS snip
                       FROM notes_fts JOIN notes_meta n ON n.id = notes_fts.id
                       WHERE notes_fts MATCH ? ORDER BY rank LIMIT ?""",
                    (fts, limit * 2),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            for r in rows:
                if r["id"] in seen:
                    continue
                seen.add(r["id"])
                results.append({**dict(r), "kind": r["kind"] or "research", "match": "body"})
                if len(results) >= limit:
                    break
    return results[:limit]


def titles_for(ids):
    """Map of id -> {title, kind} for the given note ids (unknown ids omitted)."""
    ids = [i for i in ids if isinstance(i, str) and i.isdigit()][:200]
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT id, title, kind FROM notes_meta WHERE id IN ({marks})", ids
        ).fetchall()
    return {r["id"]: {"title": r["title"], "kind": r["kind"] or "research"} for r in rows}


def sidebar_counts():
    """Counts shown in the sidebar. Never raises: a broken count should
    not take the whole page down."""
    out = {"research": 0, "journal": 0, "teaching": 0, "life": 0, "all": 0,
           "tags": 0, "threads": 0, "citations": 0, "codes": 0, "manuscripts": 0}
    try:
        out.update(kind_counts())
        out["tags"] = len(all_tags())
        with get_conn() as conn:
            out["threads"] = conn.execute(
                "SELECT COUNT(DISTINCT thread_name) AS n FROM thread_membership"
            ).fetchone()["n"]
            out["citations"] = conn.execute(
                "SELECT COUNT(*) AS n FROM bib_entries"
            ).fetchone()["n"]
            out["codes"] = conn.execute(
                "SELECT COUNT(DISTINCT code) AS n FROM codes"
            ).fetchone()["n"]
            out["manuscripts"] = conn.execute(
                "SELECT COUNT(*) AS n FROM notes_meta WHERE type = 'manuscript'"
            ).fetchone()["n"]
    except Exception as e:  # pragma: no cover - defensive
        print(f"[db] sidebar_counts failed: {e}")
    return out


# ---------- 5.1 locked notes ---------- #


def scrub_index():
    """After notes are locked: merge the full-text index so deleted text is
    rewritten out of its segments, and fold the write-ahead log back into
    the database file. With secure_delete on, freed pages are zeroed."""
    with get_conn() as conn:
        try:
            conn.execute("INSERT INTO notes_fts(notes_fts) VALUES('optimize')")
        except sqlite3.OperationalError:
            pass
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.execute("PRAGMA secure_delete=ON")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
    except sqlite3.Error:
        pass


def locked_ids(kind=None):
    sql = "SELECT id FROM notes_meta WHERE locked = 1"
    params = []
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    with get_conn() as conn:
        return [r["id"] for r in conn.execute(sql, params).fetchall()]


def unlocked_ids_of_kinds(kinds):
    """Notes of these kinds whose text isn't locked (5.2 sweep)."""
    kinds = [k for k in kinds if k]
    if not kinds:
        return []
    marks = ",".join("?" for _ in kinds)
    with get_conn() as conn:
        return [r["id"] for r in conn.execute(
            f"SELECT id FROM notes_meta WHERE kind IN ({marks}) AND COALESCE(locked, 0) = 0", kinds).fetchall()]


def ids_for_kind(kind):
    with get_conn() as conn:
        return [r["id"] for r in conn.execute("SELECT id FROM notes_meta WHERE kind = ?", (kind,)).fetchall()]


def is_locked(note_id):
    with get_conn() as conn:
        r = conn.execute("SELECT locked FROM notes_meta WHERE id = ?", (note_id,)).fetchone()
    return bool(r and r["locked"])


# ---------- 5.2 qualitative coding ---------- #


def coded_passages(code=None):
    """Indexed coded passages (unlocked notes only): one dict per passage
    with note_id, codes, excerpt, pos."""
    with get_conn() as conn:
        if code:
            rows = conn.execute(
                "SELECT note_id, pos, codes_json, excerpt FROM codes WHERE code = ? ORDER BY note_id, pos", (code,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT note_id, pos, codes_json, excerpt FROM codes ORDER BY note_id, pos"
            ).fetchall()
    return [{"note_id": r["note_id"], "pos": r["pos"], "codes": json.loads(r["codes_json"]), "excerpt": r["excerpt"]} for r in rows]


def notes_with_code(code):
    with get_conn() as conn:
        return [r["note_id"] for r in conn.execute("SELECT DISTINCT note_id FROM codes WHERE code = ?", (code,)).fetchall()]


# ---------- 5.2 search filters ---------- #

_FILTER_KEYS = ("kind", "tag", "thread", "code", "before", "after", "is")
_TOKEN_RE = re.compile(r'(-?)(?:(\w+):)?(?:"([^"]*)"|(\S+))', re.UNICODE)
_DATE_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")


def parse_query(q):
    """Split a search into free text and filters.

    kind:journal  tag:drones  -tag:old  thread:"tethered autonomy"
    code:trust  before:2025-06  after:2024  is:locked  "exact phrase"  -word
    """
    out = {"words": [], "phrases": [], "not_words": [], "kind": [], "tag": [], "not_tag": [],
           "thread": [], "code": [], "not_code": [], "before": None, "after": None, "is": [], "bad": []}
    for m in _TOKEN_RE.finditer(q or ""):
        neg, key, quoted, bare = m.group(1) == "-", (m.group(2) or "").lower(), m.group(3), m.group(4)
        val = quoted if quoted is not None else (bare or "")
        if key and key in _FILTER_KEYS:
            v = val.strip().lower()
            if not v:
                continue
            if key == "kind":
                out["kind"].append(v)
            elif key in ("tag", "code"):
                out[("not_" if neg else "") + key].append(v.lstrip("#."))
            elif key == "thread":
                out["thread"].append(v)
            elif key in ("before", "after"):
                if _DATE_RE.match(v):
                    out[key] = v
                else:
                    out["bad"].append(m.group(0))
            elif key == "is":
                out["is"].append(v)
            continue
        if key:  # "word:with-colon" that isn't a filter: treat as text
            val = m.group(0).lstrip("-")
        if quoted is not None and not key:
            if val.strip():
                (out["not_words"] if neg else out["phrases"]).append(val.strip())
        elif val:
            (out["not_words"] if neg else out["words"]).append(val)
    return out


def has_filters(parsed):
    return any(parsed[k] for k in ("phrases", "not_words", "kind", "tag", "not_tag", "thread",
                                   "code", "not_code", "before", "after", "is", "bad"))


def describe_filters(parsed):
    """Plain-English summary of the filters, for the results page."""
    bits = []
    if parsed["kind"]:
        bits.append("kind is " + " or ".join(parsed["kind"]))
    for t in parsed["tag"]:
        bits.append("tagged #" + t)
    for t in parsed["not_tag"]:
        bits.append("not tagged #" + t)
    for t in parsed["thread"]:
        bits.append("in the thread “%s”" % t)
    for c in parsed["code"]:
        bits.append("coded " + c)
    for c in parsed["not_code"]:
        bits.append("not coded " + c)
    if parsed["after"]:
        bits.append("written in or after " + parsed["after"])
    if parsed["before"]:
        bits.append("written before " + parsed["before"])
    if "locked" in parsed["is"]:
        bits.append("locked")
    if "unlocked" in parsed["is"]:
        bits.append("not locked")
    for p in parsed["phrases"]:
        bits.append("containing “%s”" % p)
    for w in parsed["not_words"]:
        bits.append("without “%s”" % w)
    for b in parsed["bad"]:
        bits.append("(ignoring %s: dates look like 2025, 2025-06 or 2025-06-30)" % b)
    return bits


def _fts_terms(words):
    q = _fts_prefix_query(" ".join(words))
    return q


def filtered_search(q, limit=50):
    """Search with filters. Returns dicts with id, title, modified, kind,
    snip (highlighted when there was text to match)."""
    p = parse_query(q)
    where, params = [], []
    if p["kind"]:
        where.append("m.kind IN (%s)" % ",".join("?" * len(p["kind"])))
        params += p["kind"]
    for t in p["tag"]:
        where.append("EXISTS (SELECT 1 FROM json_each(m.tags_json) j WHERE LOWER(j.value) = ?)")
        params.append(t)
    for t in p["not_tag"]:
        where.append("NOT EXISTS (SELECT 1 FROM json_each(m.tags_json) j WHERE LOWER(j.value) = ?)")
        params.append(t)
    for t in p["thread"]:
        where.append("EXISTS (SELECT 1 FROM thread_membership t WHERE t.note_id = m.id AND LOWER(t.thread_name) = ?)")
        params.append(t)
    for c in p["code"]:
        where.append("EXISTS (SELECT 1 FROM codes c WHERE c.note_id = m.id AND c.code = ?)")
        params.append(c)
    for c in p["not_code"]:
        where.append("NOT EXISTS (SELECT 1 FROM codes c WHERE c.note_id = m.id AND c.code = ?)")
        params.append(c)
    if p["after"]:
        where.append("m.created >= ?")
        params.append(p["after"])
    if p["before"]:
        where.append("m.created < ?")
        params.append(p["before"])
    if "locked" in p["is"]:
        where.append("COALESCE(m.locked, 0) = 1")
    if "unlocked" in p["is"]:
        where.append("COALESCE(m.locked, 0) = 0")

    match_parts = []
    pos = _fts_terms(p["words"])
    if pos:
        match_parts.append(pos)
    for ph in p["phrases"]:
        words = re.findall(r"\w+", ph, flags=re.UNICODE)
        if words:
            match_parts.append('"' + " ".join(words) + '"')
    for w in p["not_words"]:
        words = re.findall(r"\w+", w, flags=re.UNICODE)
        if words:
            where.append("m.id NOT IN (SELECT id FROM notes_fts WHERE notes_fts MATCH ?)")
            params.append('"' + " ".join(words) + '"' + ("*" if len(words) == 1 else ""))
    with get_conn() as conn:
        try:
            if match_parts:
                sql = ("SELECT m.id, m.title, m.modified, m.kind, "
                       "snippet(notes_fts, 2, '<mark>', '</mark>', '…', 16) AS snip "
                       "FROM notes_fts JOIN notes_meta m ON m.id = notes_fts.id "
                       "WHERE notes_fts MATCH ?" + "".join(" AND " + w for w in where) +
                       " ORDER BY rank LIMIT ?")
                rows = conn.execute(sql, [" AND ".join(match_parts)] + params + [limit]).fetchall()
            else:
                sql = ("SELECT m.id, m.title, m.modified, m.kind, '' AS snip FROM notes_meta m" +
                       (" WHERE " + " AND ".join(where) if where else "") +
                       " ORDER BY m.modified DESC LIMIT ?")
                rows = conn.execute(sql, params + [limit]).fetchall()
        except sqlite3.OperationalError as e:
            print("[db] filtered_search failed (%s)" % type(e).__name__)  # the message can quote the search
            return []
    return [{**dict(r), "kind": r["kind"] or "research"} for r in rows]


# ---------- 5.2 manuscripts ---------- #


def body_for(note_id):
    """Indexed text of a note ('' for locked notes)."""
    with get_conn() as conn:
        r = conn.execute("SELECT body FROM notes_fts WHERE id = ?", (note_id,)).fetchone()
    return r["body"] if r else ""


def manuscripts():
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, title, modified FROM notes_meta WHERE type = 'manuscript' ORDER BY modified DESC").fetchall()]
