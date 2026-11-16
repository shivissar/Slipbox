"""
threads.py — Putting existing notes into threads, taking them out, and
moving them along (5.3).

A thread is a named sequence of notes. Each member note's front matter
holds an entry {name, prev, next} for every thread it is in, so the
sequence is a chain of links from note to note. Before 5.3 a note could
join a thread only when it was created, at the end.

Every change here works out the thread's new order, then rewrites the
prev/next of each member whose entry differs, so a chain that was broken
(by a note deleted in Finder, say) is mended as a side effect. Locked
notes can be moved too: only their front matter changes, and their
encrypted text is written back as it was.

A note's place in a thread isn't part of what the note says, so these
changes leave its "modified" date alone and add nothing to its history.
Every member is read before any is written: if one can't be read, the
thread is left as it was and the note is named.

Branches (5.4). A thread can branch: from any of its notes a second line
of thought can leave, as a thread of its own, and that branch can branch
again, ten levels deep. This is Luhmann's Folgezettel proper, where card
1/1 could be followed by 1/1a as well as by 1/2. A branch is stored as an
ordinary thread whose notes each carry two more fields in their entry:
"parent" (the thread it leaves) and "from" (the ID of the note it leaves
from). Every member carries the pair, so it survives the loss of any one
note. Nothing else about a thread changes, so all of the above works on
a branch as on any thread, and a file written by 5.3 or read by it stays
valid. If the note a branch leaves from is taken out of its thread, the
branch moves to the note before it; if a whole thread goes, its branches
move up to where that thread left from.
"""

# Copyright (c) 2026 Shiv Issar
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import re

from . import db
from . import notes

NAME_MAX = 80
MAX_DEPTH = 10      # how many levels of branch a thread can have below it
_KEEP = object()    # _write: leave the thread a branch of whatever it is now


class ThreadError(Exception):
    """A change that can't be made; the message is shown to the person."""


def clean_name(raw):
    """A thread name as typed, tidied, or ThreadError."""
    name = re.sub(r"\s+", " ", raw or "").strip()
    if not name:
        raise ThreadError("Give the new thread a name.")
    if "/" in name:
        raise ThreadError("A thread name can’t contain a slash (/).")
    if not name.strip("."):
        raise ThreadError("A thread’s name needs a letter or a number: a name of full stops alone can’t be opened.")
    if len(name) > NAME_MAX:
        raise ThreadError("Keep the thread’s name under %d characters." % NAME_MAX)
    return name


def order(name):
    """The thread's note IDs, first to last, leaving out any whose file is
    gone (the next change then joins up the notes either side)."""
    out = []
    for c in db.thread_chain(name):
        meta = db.get_note_meta(c["id"])
        if meta and (notes.NOTES_DIR / meta["filename"]).exists():
            out.append(c["id"])
    return out


def _load(note_id):
    meta = db.get_note_meta(note_id)
    path = notes.NOTES_DIR / meta["filename"] if meta else None
    if path is None or not path.exists():
        raise ThreadError("A note in this thread can’t be found. Reload the page and try again.")
    try:
        return notes.parse_file(path)
    except Exception as e:  # front matter that isn't valid, a file that isn't text
        raise ThreadError(
            "“%s” can’t be read, so the thread wasn’t changed. Its file (%s) has a problem in the "
            "lines between --- at the top: %s. Open it in a text editor to fix it." % (
                meta.get("title") or note_id, meta["filename"],
                (str(e).splitlines() or [type(e).__name__])[0][:120]))


def _save(note):
    try:
        notes.write_note(note, touch=False)
    except OSError as e:
        raise ThreadError("“%s” couldn’t be saved (%s). The thread may be out of order: "
                          "try the change again." % (note.title, e.strerror or type(e).__name__))
    db.upsert_note(note)


def _id(value):
    return str(value) if value else None


def _link_of(entry):
    if not entry.get("parent"):
        return None
    return str(entry["parent"]), _id(entry.get("from")) or ""


def _write(name, new_order, removed=(), link=_KEEP):
    """Give every note in new_order the right neighbours in thread `name`,
    and take the thread out of the notes in `removed`. `link` says what
    the thread branches from: (parent thread, note ID), None for a thread
    of its own, or _KEEP for whatever it is now. Returns the IDs of the
    notes rewritten."""
    if link is _KEEP:
        link = db.thread_links().get(name)
    link = (str(link[0]), str(link[1] or "")) if link else None
    changed = []
    loaded = {nid: _load(nid) for nid in list(new_order) + list(removed)}  # all, before any write
    for i, nid in enumerate(new_order):
        prev_id = new_order[i - 1] if i else None
        next_id = new_order[i + 1] if i + 1 < len(new_order) else None
        note = loaded[nid]
        entries = [t for t in note.threads if isinstance(t, dict) and t.get("name") == name]
        if not entries:
            entry = {"name": name, "prev": prev_id, "next": next_id}
            note.threads.append(entry)
        elif (len(entries) == 1 and _id(entries[0].get("prev")) == prev_id
              and _id(entries[0].get("next")) == next_id and _link_of(entries[0]) == link):
            continue
        else:
            # Keep one entry for the thread (a hand-edited file may have two).
            entry = entries[0]
            note.threads = [t for t in note.threads if t is entry or t not in entries]
            entry["prev"], entry["next"] = prev_id, next_id
        if link:
            entry["parent"], entry["from"] = link
        else:
            entry.pop("parent", None)
            entry.pop("from", None)
        _save(note)
        changed.append(nid)
    for nid in removed:
        note = loaded[nid]
        kept = [t for t in note.threads if not (isinstance(t, dict) and t.get("name") == name)]
        if len(kept) != len(note.threads):
            note.threads = kept
            _save(note)
            changed.append(nid)
    return changed


def _readable(thread_names):
    """Read every note of these threads now, so that a change touching
    several threads stops before its first write if any can't be read."""
    for t in thread_names:
        for nid in order(t):
            _load(nid)


# ---------- Branches (5.4): who leaves from where ---------- #

def _names():
    return {n for n, _ in db.all_threads()}


def tree_links():
    """{branch: (parent thread, ID of the note left from)}, kept only where
    it makes a proper tree: the parent exists, and there is no loop. Only
    editing files by hand can make a loop (attach() refuses one); it is
    cut at its first thread by name, which then counts as a thread of its
    own. Everything below reads the tree through this."""
    names = _names()
    good = {b: link for b, link in db.thread_links().items()
            if b in names and link[0] in names and link[0] != b}
    for start in sorted(good):
        seen, cur = [start], start
        while cur in good:
            cur = good[cur][0]
            if cur in seen:
                del good[min(seen[seen.index(cur):])]
                break
            seen.append(cur)
    return good


def ancestors(name, links=None):
    """[(parent thread, ID of the note left from), ...], nearest first."""
    links = tree_links() if links is None else links
    out = []
    while name in links:
        out.append(links[name])
        name = links[name][0]
    return out


def parent_of(name, links=None):
    links = tree_links() if links is None else links
    return links.get(name)


def children(name, links=None):
    """The branches that leave thread `name`, by name."""
    links = tree_links() if links is None else links
    return sorted((b for b, link in links.items() if link[0] == name), key=lambda b: (b.casefold(), b))


def depth(name, links=None):
    """0 for a thread of its own, 1 for a branch of one, and so on."""
    return len(ancestors(name, links))


def height(name, links=None):
    """How many levels of branch hang below this thread."""
    links = tree_links() if links is None else links
    below = children(name, links)
    return 1 + max(height(b, links) for b in below) if below else 0


def _letters(n):
    """0 → a, 25 → z, 26 → aa: the letter a branch adds to its label."""
    out = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(97 + r) + out
    return out


def _too_deep():
    return ThreadError("A thread can branch %d levels deep, and this would go deeper. "
                       "Branch from a note nearer the first thread." % MAX_DEPTH)


def _anchor(parent, from_id):
    """The parent's order, having checked that from_id is one of its notes."""
    current = order(parent)
    if not current:
        raise ThreadError("The thread “%s” can’t be found. Reload the page and try again." % parent)
    if from_id not in current:
        raise ThreadError("That note isn’t in “%s” any more. Reload the page and try again." % parent)
    return current


def new_name(raw):
    name = clean_name(raw)
    if db.thread_chain(name):
        raise ThreadError("There is already a thread called “%s”. Give the branch a name of its own." % name)
    return name


def start_branch(name, parent, from_id, note_id):
    """Start a branch called `name` with one note, leaving thread `parent`
    from its note `from_id`."""
    name = new_name(name)
    check_branch_point(parent, from_id)
    if not db.get_note_meta(note_id):
        raise ThreadError("Choose a note from the suggestions as you type.")
    if note_id == from_id:
        raise ThreadError("A branch starts with a different note from the one it leaves.")
    _write(name, [note_id], link=(parent, from_id))
    return name


def check_branch_point(parent, from_id):
    """ThreadError unless a new branch can leave `parent` from `from_id`."""
    _anchor(parent, from_id)
    if depth(parent) + 1 > MAX_DEPTH:
        raise _too_deep()


def cut(parent, from_id, name):
    """Split a thread in two: the notes after `from_id` leave `parent` and
    become the branch `name`, which leaves from `from_id`. Branches that
    left from the moved notes go with them. Returns how many notes moved."""
    name = new_name(name)
    current = _anchor(parent, from_id)
    rest = current[current.index(from_id) + 1:]
    if not rest:
        raise ThreadError("There are no notes after this one to move into a branch.")
    links = tree_links()
    moving = [b for b in children(parent, links) if links[b][1] in rest]
    if depth(parent, links) + 1 + (1 + max(height(b, links) for b in moving) if moving else 0) > MAX_DEPTH:
        raise _too_deep()
    _readable([parent] + moving)
    # Into the new branch first, out of the old thread last: if a write
    # fails partway (a full disk, a file another app holds), the notes are
    # in both threads, never in neither.
    try:
        _write(name, rest, link=(parent, from_id))
        _write(parent, current[:current.index(from_id) + 1], removed=rest)
        for b in moving:
            _write(b, order(b), link=(name, links[b][1]))
    except ThreadError as e:
        raise ThreadError("The split stopped partway: %s No note has dropped out of a thread, but some may now "
                          "be in both “%s” and “%s”. Once the cause is dealt with, take them out of one."
                          % (str(e).split(" The thread may be")[0], parent, name))
    return len(rest)


def attach(thread, parent, from_id):
    """Make an existing thread a branch of `parent`, leaving from its note
    `from_id`; or move a branch to another note."""
    if thread == parent:
        raise ThreadError("A thread can’t branch from itself.")
    mine = order(thread)
    if not mine:
        raise ThreadError("The thread “%s” can’t be found. Reload the page and try again." % thread)
    _anchor(parent, from_id)
    links = tree_links()
    if thread in [p for p, _ in ancestors(parent, links)]:
        raise ThreadError("“%s” already branches from “%s”, so it can’t be the other way round as well." % (parent, thread))
    if depth(parent, links) + 1 + height(thread, links) > MAX_DEPTH:
        raise _too_deep()
    _write(thread, mine, link=(parent, from_id))


def detach(thread):
    """Make a branch a thread of its own. Its notes and its own branches
    stay as they are."""
    mine = order(thread)
    if not mine:
        raise ThreadError("The thread “%s” can’t be found. Reload the page and try again." % thread)
    if thread not in db.thread_links():
        raise ThreadError("“%s” isn’t a branch." % thread)
    _write(thread, mine, link=None)


def tree(name, links=None):
    """Thread `name` and everything that branches from it, for its page:
    {"name", "label", "count", "notes": [{"id", "title", "label",
    "branches": [the same, one level down]}], "loose": [branches whose
    note has left the thread], "all": [every branch one level down]}.
    Labels are Luhmann's: the third note is 3, the first branch from it 3a,
    that branch's notes 3a1, 3a2, a branch from 3a2 is 3a2a, and so on."""
    links = tree_links() if links is None else links

    def build(t, prefix, level):
        chain = db.thread_chain(t)
        ids = [c["id"] for c in chain]
        at, loose = {}, []
        for b in children(t, links) if level < MAX_DEPTH else []:
            (at.setdefault(links[b][1], []) if links[b][1] in ids else loose).append(b)
        out = {"name": t, "label": prefix, "count": len(chain), "notes": [], "all": []}
        for i, c in enumerate(chain, 1):
            lab = "%s%d" % (prefix, i)
            below = [build(b, lab + _letters(k), level + 1) for k, b in enumerate(at.get(c["id"], []))]
            out["notes"].append({"id": c["id"], "title": c["title"], "label": lab, "branches": below})
            out["all"] += below
        out["loose"] = [build(b, prefix + "?" + _letters(k), level + 1) for k, b in enumerate(loose)]
        out["all"] += out["loose"]
        return out

    return build(name, label(name, links), 0)


def label(name, links=None):
    """A branch's own label ("3a", "3a2b"); "" for a thread of its own."""
    links = tree_links() if links is None else links
    if name not in links:
        return ""
    parent, from_id = links[name]
    ids = [c["id"] for c in db.thread_chain(parent)]
    beside = [b for b in children(parent, links) if (links[b][1] == from_id) or (from_id not in ids and links[b][1] not in ids)]
    place = str(ids.index(from_id) + 1) if from_id in ids else "?"
    return label(parent, links) + place + _letters(beside.index(name))


def forest():
    """Every thread, for the Threads page: the threads of their own, by
    name, each with its branches nested inside (see tree())."""
    links = tree_links()
    roots = sorted((n for n in _names() if n not in links), key=lambda n: (n.casefold(), n))
    return [tree(n, links) for n in roots]


def add(note_id, name, after=None):
    """Put a note into thread `name`: after the note `after`, at the start
    if after is "start", else at the end. A name no thread has yet starts
    a new thread with this note."""
    if not db.get_note_meta(note_id):
        raise ThreadError("That note can’t be found.")
    current = order(name)
    if note_id in current:
        raise ThreadError("This note is already in “%s”." % name)
    if after == "start":
        at = 0
    elif after and after in current:
        at = current.index(after) + 1
    else:
        at = len(current)
    _write(name, current[:at] + [note_id] + current[at:])
    return at + 1, len(current) + 1


def remove(note_id, name):
    """Take a note out of a thread; the notes either side are joined up.
    Branches that left from the note move to the note before it (the note
    after, if it was the first); if it was the thread's only note, they
    move up to where this thread itself left from, or become threads of
    their own. Returns how many notes are left in the thread."""
    current = order(name)
    if note_id not in current:
        raise ThreadError("This note isn’t in “%s”." % name)
    i = current.index(note_id)
    rest = [n for n in current if n != note_id]
    links = tree_links()
    below = children(name, links)
    moving = [b for b in below if links[b][1] == note_id] if rest else below
    up = parent_of(name, links)
    _readable(moving)
    _write(name, rest, removed=[note_id])
    for b in moving:
        _write(b, order(b), link=(name, rest[i - 1] if i else rest[0]) if rest else up)
    return len(rest)


def move(note_id, name, step):
    """Move a note one place earlier (step -1) or later (+1)."""
    current = order(name)
    if note_id not in current:
        raise ThreadError("This note isn’t in “%s”." % name)
    i = current.index(note_id)
    j = i + (1 if step > 0 else -1)
    if not 0 <= j < len(current):
        return i + 1
    current[i], current[j] = current[j], current[i]
    _write(name, current)
    return j + 1
