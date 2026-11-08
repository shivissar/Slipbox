"""
codebook.py — Qualitative coding (5.2).

A coded passage is written with pandoc's bracketed-span syntax:

    [the pilot said she trusted the autopilot]{.trust .automation}

Each class is a code. The text stays readable in any editor, and pandoc
exports it as ordinary text. Codes from unlocked notes are indexed in the
`codes` table; codes in locked notes are read live while locked notes are
open and never stored. Code definitions and categories live in
.verweis-codebook.json in the notes folder, so they travel with the notes.
"""

# Copyright (c) 2026 Shiv Issar
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import csv
import io
import json
import os
import re
from pathlib import Path

CODE_NAME_RE = re.compile(r"^[\w][\w-]{0,59}$", re.UNICODE)
# [excerpt]{.a .b}: the excerpt may hold [[links]] and [@citations], but no
# blank line; it can't start with "[" (that would be a wikilink).
SPAN_RE = re.compile(
    r"(?<![\[!\\])\[(?!\[)((?:\[\[[^\]\n]*\]\]|\[[^\[\]\n]*\]|[^\[\]])+?)\]\{((?:\s*\.[\w-]+)+)\s*\}",
    re.UNICODE,
)
_CLASS_RE = re.compile(r"\.([\w-]+)", re.UNICODE)


def normalize(name):
    """'Human–machine trust' -> 'human-machine-trust'."""
    s = (name or "").strip().lower()
    s = re.sub(r"[\s_/–—]+", "-", s)
    s = re.sub(r"[^\w-]", "", s, flags=re.UNICODE).strip("-")
    return s[:60]


def extract(body):
    """[(codes, excerpt, start)] for every coded passage in body."""
    out = []
    for m in SPAN_RE.finditer(body or ""):
        if "\n\n" in m.group(1):
            continue
        codes = [c for c in _CLASS_RE.findall(m.group(2))]
        if codes:
            out.append((codes, m.group(1), m.start()))
    return out


def apply_rename(body, old, new):
    """Rename (or merge, if `new` is already there) a code in body; with
    new=None, remove it, unwrapping passages left with no code."""
    def fix(m):
        codes = _CLASS_RE.findall(m.group(2))
        if old not in codes:
            return m.group(0)
        kept = []
        for c in codes:
            c = new if c == old else c
            if c and c not in kept:
                kept.append(c)
        if not kept:
            return m.group(1)
        return "[%s]{%s}" % (m.group(1), " ".join("." + c for c in kept))
    return SPAN_RE.sub(fix, body or "")


# ---------------------------------------------------------------- definitions

FILE_NAME = ".verweis-codebook.json"
LEGACY_FILE_NAMES = (".zetteling-codebook.json",)  # before the rename (5.2)


def _file():
    from . import notes
    path = Path(notes.NOTES_DIR) / FILE_NAME
    if not path.exists():
        for name in LEGACY_FILE_NAMES:
            old = Path(notes.NOTES_DIR) / name
            if old.exists():
                return old
    return path


def load_definitions():
    try:
        data = json.loads(_file().read_text(encoding="utf-8"))
        codes = data.get("codes", {})
        return codes if isinstance(codes, dict) else {}
    except (OSError, ValueError):
        return {}


def save_definitions(codes):
    p = _file()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": 1, "codes": codes}, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)


def set_definition(code, description, category):
    codes = load_definitions()
    entry = {"description": (description or "").strip(), "category": (category or "").strip()}
    if entry["description"] or entry["category"]:
        codes[code] = entry
    else:
        codes.pop(code, None)
    save_definitions(codes)


# ---------------------------------------------------------------- gathering

def _locked_occurrences():
    """Coded passages in locked notes, read live (only while open)."""
    from . import db, notes, vault
    out = []
    if not vault.is_unlocked():
        return out
    for nid in db.locked_ids():
        meta = db.get_note_meta(nid)
        if not meta:
            continue
        path = Path(notes.NOTES_DIR) / meta["filename"]
        try:
            note = notes.parse_file(path)
        except Exception:
            continue
        if note.unreadable:
            continue
        for codes, excerpt, pos in extract(note.body):
            out.append({"note_id": nid, "codes": codes, "excerpt": excerpt, "pos": pos, "locked": True})
    return out


def occurrences(code=None):
    """Every coded passage (optionally only those with `code`), from the
    index plus open locked notes. Each: note_id, codes, excerpt, pos, locked."""
    from . import db
    rows = db.coded_passages(code)
    out = [dict(r, locked=False) for r in rows]
    out += [o for o in _locked_occurrences() if code is None or code in o["codes"]]
    return out


def summary():
    """Per code: excerpts, notes, definition, category; plus how many locked
    notes couldn't be read."""
    from . import db, vault
    defs = load_definitions()
    stats = {}
    for o in occurrences():
        for c in o["codes"]:
            s = stats.setdefault(c, {"code": c, "excerpts": 0, "notes": set(), "locked": 0})
            s["excerpts"] += 1
            s["notes"].add(o["note_id"])
            if o["locked"]:
                s["locked"] += 1
    for c in defs:
        stats.setdefault(c, {"code": c, "excerpts": 0, "notes": set(), "locked": 0})
    out = []
    for c, s in stats.items():
        d = defs.get(c, {})
        out.append({"code": c, "excerpts": s["excerpts"], "notes": len(s["notes"]), "locked": s["locked"],
                    "description": d.get("description", ""), "category": d.get("category", "")})
    out.sort(key=lambda x: ((x["category"] or "~").lower(), x["code"]))
    closed = 0 if vault.is_unlocked() else len(db.locked_ids())
    return out, closed


def cooccurrence(code):
    """Codes applied to the same passages as `code`, and codes used in the
    same notes, with counts."""
    same_passage, same_note = {}, {}
    occ = occurrences()
    notes_with = {o["note_id"] for o in occ if code in o["codes"]}
    for o in occ:
        if code in o["codes"]:
            for c in o["codes"]:
                if c != code:
                    same_passage[c] = same_passage.get(c, 0) + 1
    for o in occ:
        if o["note_id"] in notes_with:
            for c in o["codes"]:
                if c != code:
                    same_note.setdefault(c, set()).add(o["note_id"])
    sp = sorted(same_passage.items(), key=lambda kv: (-kv[1], kv[0]))
    sn = sorted(((c, len(n)) for c, n in same_note.items()), key=lambda kv: (-kv[1], kv[0]))
    return sp, sn


def suggestions(q="", limit=12):
    q = normalize(q)
    rows, _ = summary()
    names = [r for r in rows if not q or q in r["code"]]
    names.sort(key=lambda r: (not r["code"].startswith(q), -r["excerpts"], r["code"]))
    return [{"code": r["code"], "count": r["excerpts"], "description": r["description"]} for r in names[:limit]]


_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _cell(value):
    """Spreadsheet apps run a cell that starts with = + - or @ as a formula
    (an excerpt quoting "@smith2020" showed #NAME?, and a crafted one could
    do worse). 5.2: such cells start with an apostrophe, which keeps them text."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_START) else text


def to_csv(code=None):
    from . import db
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["code", "category", "note_title", "note_id", "kind", "created", "locked", "excerpt"])
    defs = load_definitions()
    metas = {}
    for o in occurrences(code):
        meta = metas.get(o["note_id"]) or db.get_note_meta(o["note_id"]) or {}
        metas[o["note_id"]] = meta
        for c in o["codes"]:
            if code and c != code:
                continue
            w.writerow([_cell(v) for v in (
                c, defs.get(c, {}).get("category", ""), meta.get("title", ""), o["note_id"],
                meta.get("kind", ""), meta.get("created", ""), "yes" if o["locked"] else "no",
                re.sub(r"\s+", " ", o["excerpt"]).strip())])
    return buf.getvalue()


def locked_rows(code=None):
    """How many passages in to_csv(code) come from locked notes."""
    return sum(1 for o in occurrences(code) if o["locked"]
               for c in o["codes"] if not code or c == code)
