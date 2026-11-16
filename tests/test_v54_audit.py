"""5.4 audit (16 Nov 2026): the import survives front matter the YAML
reader rejects for any reason, never writes a note Slipbox couldn't read
back, and doesn't point links at the wrong note; a split interrupted
partway leaves no note out of every thread; the index is rebuilt after an
older Slipbox has used it; small things about names and IDs."""
import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

_tmp = Path(tempfile.mkdtemp(prefix="z54a-"))
os.environ.setdefault("ZETTEL_SETTINGS_PATH", str(_tmp / "settings.json"))
os.environ.setdefault("ZETTEL_NOTES_DIR", str(_tmp / "notes"))
os.environ.setdefault("ZETTEL_DB_PATH", str(_tmp / "zettel.db"))
os.environ["ZETTEL_VAULT_KDF"] = "light"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from Slipbox import (db, embed, flask_app, history, importer, notes,  # noqa: E402
                     settings, threads, vault, watcher)

app = flask_app.app


@pytest.fixture
def client(tmp_path, monkeypatch):
    d = tmp_path / "notes"
    d.mkdir()
    monkeypatch.setattr(notes, "NOTES_DIR", d)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "zettel.db")
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(embed, "embed_note_async", lambda *a, **k: None)
    monkeypatch.setattr(history, "INTERVAL", 0)
    db.init_db()
    settings.save({"welcomed": True, "tour_seen": True, "update_check": False,
                   "html_safety_applied": True, "history_max": 200,
                   "export_dir": str(tmp_path / "exports")})
    vault.lock()
    flask_app._SWEEP["msg"] = ""
    flask_app._IMPORT_PREVIEWS.clear()
    flask_app._IMPORT_REPORTS.clear()
    app.testing = True
    yield app.test_client()
    vault.lock()


def _folder(tmp_path, files, name="vault"):
    src = tmp_path / name
    src.mkdir(exist_ok=True)
    for rel, text in files.items():
        f = src / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    return src


def _make(*titles):
    out = []
    for t in titles:
        n = notes.create_note(t, body="Text of %s." % t)
        db.upsert_note(n)
        out.append(n.id)
    return out


def _note(note_id):
    return notes.parse_file(notes.NOTES_DIR / db.get_note_meta(note_id)["filename"])


def _chain(name):
    return [c["id"] for c in db.thread_chain(name)]


def _import(client, src, kind="research"):
    page = client.post("/import/preview", data={"folder": str(src), "kind": kind}).get_data(as_text=True)
    token = re.search(r'name="token" value="([^"]+)"', page).group(1)
    r = client.post("/import/run", data={"folder": str(src), "kind": kind, "token": token})
    return page, client.get(r.location).get_data(as_text=True)


# ---- A1: front matter the YAML reader refuses, for whatever reason ---------------

@pytest.mark.parametrize("block,title,kept", [
    # A value that can't be: the fields are still read, the value kept as the text it was written as.
    ("title: Draft\ntags: [a, b]\ndate: 0000-00-00", "Draft", {"date": "0000-00-00"}),
    ("title: Feb\ndate: 2023-02-30", "Feb", {"date": "2023-02-30"}),
    ("title: Thirteen\ncreated: 2024-13-01T10:00:00", "Thirteen", {"source-created": "2024-13-01T10:00:00"}),
    ("title: Late\nupdated: 2024-01-01 25:00:00", "Late", {"updated": "2024-01-01 25:00:00"}),
    ("title: Big\nn: " + "9" * 5000, "Big", {"n": "9" * 5000}),
    # Not readable as fields at all: the block stays in the note's text.
    ("t: " + "[" * 2000 + "]" * 2000, None, None),
    ('title: "unclosed', None, None),
    ("x: !!python/object:os.system ls", None, None),
])
def test_a_file_the_yaml_reader_refuses_does_not_stop_the_preview(client, tmp_path, block, title, kept):
    src = _folder(tmp_path, {"odd.md": "---\n%s\n---\nThe body of the odd one.\n" % block, "fine.md": "Fine.\n"})
    plan = importer.scan(str(src), "research")
    odd = next(i for i in plan.items if i.rel == "odd.md")
    if title:
        assert odd.title == title and odd.extra == kept and plan.yaml_problems == []
        assert odd.body == "The body of the odd one." and odd.created.year >= 2026       # the file's own date is used
        if "tags" in block:
            assert odd.tags == ["a", "b", "imported"]
    else:
        assert plan.yaml_problems == ["odd.md"] and odd.title == "odd" and odd.extra == {}
        assert odd.body.startswith("---") and "The body of the odd one." in odd.body  # nothing taken out
    preview, report = _import(client, src)
    assert "Import 2 notes" in preview and "Imported 2 notes" in report
    assert ("Opening lines kept as text" in preview) == (title is None)
    for p in notes.NOTES_DIR.glob("*.md"):
        note = notes.parse_file(p)                                                        # every note reads back
        if title and note.title == title:
            assert note.extra == kept


# ---- A2: nothing is written that Slipbox couldn't read again ---------------------

def test_fields_called_content_and_handler_are_kept_under_another_name(client, tmp_path):
    src = _folder(tmp_path, {
        "a.md": "First.\n",
        "cms.md": "---\ntitle: From a CMS\ncontent: the teaser text\nhandler: page\nsource: export\n---\nBody of the CMS note.\n",
        "z.md": "Last.\n",
    })
    plan = importer.scan(str(src), "research")
    cms = next(i for i in plan.items if i.rel == "cms.md")
    assert cms.extra == {"source": "export", "source-content": "the teaser text", "source-handler": "page"}
    assert plan.renamed == {"content": 1, "handler": 1} and plan.yaml_problems == []
    preview, report = _import(client, src)
    assert "<code>source-content</code>" in preview and "Imported 3 notes" in report and "Stopped at" not in report
    read = {n.title: n for n in map(notes.parse_file, notes.NOTES_DIR.glob("*.md"))}
    assert set(read) == {"a", "From a CMS", "z"}
    assert read["From a CMS"].extra["source-content"] == "the teaser text" and read["From a CMS"].body.strip() == "Body of the CMS note."


def test_fields_slipbox_could_not_read_back_stay_in_the_text(client, tmp_path, monkeypatch):
    src = _folder(tmp_path, {"odd.md": "---\ntitle: Odd\nstrange: value\n---\nBody.\n", "fine.md": "---\ntitle: Fine\nok: 1\n---\nBody.\n"})
    monkeypatch.setattr(importer, "_storable", lambda extra: "strange" not in extra)
    plan = importer.scan(str(src), "research")
    odd = next(i for i in plan.items if i.rel == "odd.md")
    assert plan.yaml_problems == ["odd.md"] and odd.extra == {} and "strange: value" in odd.body and odd.title == "odd"
    assert next(i for i in plan.items if i.rel == "fine.md").extra == {"ok": 1}


def test_the_storable_check_itself(client):
    assert importer._storable({}) and importer._storable({"aliases": ["a", "b"], "date": "2020-01-01", "n": 3})
    assert not importer._storable({"content": "x"}) and not importer._storable({"handler": "y"})


def test_a_note_that_could_not_be_read_back_is_skipped_not_written(client, tmp_path, monkeypatch):
    src = _folder(tmp_path, {"a.md": "First.\n", "bad.md": "---\nmarker: unreadable-once-written\n---\nBody.\n", "z.md": "Last.\n"})
    plan = importer.scan(str(src), "research")
    real = importer.frontmatter.loads

    def loads(text, *a, **k):
        if "unreadable-once-written" in text:
            raise TypeError("as if the reader failed on this note")
        return real(text, *a, **k)
    monkeypatch.setattr(importer.frontmatter, "loads", loads)
    written, error = importer.run(plan)
    assert error == "" and sorted(i.rel for i in written) == ["a.md", "z.md"]
    assert [rel for rel, _ in plan.skipped] == ["bad.md"] and "can’t be kept in a Slipbox note" in plan.skipped[0][1]
    assert len(list(notes.NOTES_DIR.glob("*.md"))) == 2


def test_a_failed_write_leaves_no_half_made_note(client, tmp_path, monkeypatch):
    src = _folder(tmp_path, {"a.md": "First.\n", "b.md": "Second.\n"})
    plan = importer.scan(str(src), "research")
    real = notes.parse_file
    monkeypatch.setattr(notes, "parse_file", lambda p: (_ for _ in ()).throw(ValueError("can't read it")) if "-b.md" in str(p) else real(p))
    written, error = importer.run(plan)
    assert [i.rel for i in written] == ["a.md"] and error.startswith("Stopped at b.md")
    assert [p.name[15:] for p in notes.NOTES_DIR.glob("*.md")] == ["a.md"]


# ---- B1: a split interrupted partway ----------------------------------------------

def test_a_split_that_fails_partway_leaves_every_note_in_a_thread(client, monkeypatch):
    real = notes._write_atomically
    ids = None
    for fail_at in range(1, 9):
        with db.get_conn() as conn:
            conn.execute("DELETE FROM thread_membership")
        for p in notes.NOTES_DIR.glob("*.md"):
            p.unlink()
        ids = dict(zip("ABCDEX", _make(*"ABCDEX")))
        for t in "ABCDE":
            threads.add(ids[t], "main")
        threads.start_branch("aside", "main", ids["D"], ids["X"])
        count = {"n": 0}

        def flaky(path, text):
            count["n"] += 1
            if count["n"] == fail_at:
                raise OSError(28, "No space left on device")
            return real(path, text)
        monkeypatch.setattr(notes, "_write_atomically", flaky)
        try:
            threads.cut("main", ids["B"], "rest")
            failed = False
        except threads.ThreadError as e:
            failed = True
            assert "The split stopped partway" in str(e) and "No note has dropped out of a thread" in str(e)
        monkeypatch.setattr(notes, "_write_atomically", real)
        for t in "ABCDE":
            where = [name for name in ("main", "rest") if ids[t] in _chain(name)]
            assert where, "write %d failed and %s is in no thread" % (fail_at, t)
        if not failed:
            assert _chain("main") == [ids["A"], ids["B"]] and _chain("rest") == [ids["C"], ids["D"], ids["E"]]
            assert threads.parent_of("aside") == ("rest", ids["D"])


# ---- B2: the index after an older Slipbox has used it -----------------------------

def test_the_index_is_rebuilt_when_an_older_version_used_it_last(client):
    assert watcher.INDEX_VERSION == "5.4"
    a, b, x, y = _make("A", "B", "X", "Y")
    threads.add(a, "main")
    threads.add(b, "main")
    threads.start_branch("aside", "main", b, x)
    threads.add(y, "aside")
    watcher.full_resync()
    assert db.get_embed_meta("index_version") == "5.4"
    # What 5.2.0 does when it saves a note: its own version, and the row without the two new values.
    with db.get_conn() as conn:
        conn.execute("UPDATE thread_membership SET parent_thread = NULL, from_id = NULL")
    db.set_embed_meta("index_version", "5.2")
    assert threads.parent_of("aside") is None
    watcher.full_resync()
    assert threads.parent_of("aside") == ("main", b) and db.get_embed_meta("index_version") == "5.4"


# ---- B3: links that lead outside the folder ---------------------------------------

def test_a_link_to_a_file_elsewhere_is_not_pointed_at_a_namesake(client, tmp_path):
    src = _folder(tmp_path, {
        "Target.md": "The one inside.\n",
        "sub/Note.md": ("[outside](../../elsewhere/Target.md) [absolute](/Users/x/other/Target.md) [wrong folder](notes/Target.md) "
                        "[inside](../Target.md) [bare](Target.md) [bare, no ending](Target) [from the top](/Target.md)\n"),
    })
    plan = importer.scan(str(src), "research")
    t = next(i for i in plan.items if i.rel == "Target.md").id
    body = next(i for i in plan.items if i.rel == "sub/Note.md").body
    assert body.strip() == ("[outside](../../elsewhere/Target.md) [absolute](/Users/x/other/Target.md) [wrong folder](notes/Target.md) "
                            "[[%s|inside]] [[%s|bare]] [[%s|bare, no ending]] [[%s|from the top]]" % (t, t, t, t))
    assert [link for _, link in plan.unmatched] == ["[outside](../../elsewhere/Target.md)", "[absolute](/Users/x/other/Target.md)",
                                                    "[wrong folder](notes/Target.md)"]


# ---- C1, C7, C8 ---------------------------------------------------------------------

def test_a_thread_cannot_be_named_with_full_stops_alone(client):
    for name in (".", "..", "...", " .. "):
        with pytest.raises(threads.ThreadError, match="full stops alone"):
            threads.clean_name(name)
    assert threads.clean_name("v1.2") == "v1.2" and threads.clean_name(".hidden") == ".hidden" and threads.clean_name("et al.") == "et al."
    a, = _make("A")
    r = client.post("/threads/change", data={"action": "add", "note": a, "thread": "", "new_thread": ".."})
    assert r.status_code == 302 and "full stops alone" in flask_app._SWEEP["msg"] and db.all_threads() == []


def test_a_new_note_never_takes_the_id_of_a_trashed_one(client, monkeypatch):
    first = notes.create_note("First", body="one")
    db.upsert_note(first)
    first.body = "edited"
    notes.write_note(first)
    assert history.list_versions(first.id)
    notes.soft_delete(notes.parse_file(first.path))
    db.delete_note(first.id)
    monkeypatch.setattr(notes, "now_id", lambda: first.id)       # a note made in that very second
    second = notes.create_note("Second")
    assert second.id != first.id and int(second.id) == int(first.id) + 1 and not history.list_versions(second.id)
    # History left behind by a note deleted in Finder counts too.
    third_id = str(int(second.id) + 1)
    (history.history_dir() / third_id).mkdir(parents=True)
    monkeypatch.setattr(notes, "now_id", lambda: third_id)
    assert notes.create_note("Third").id == str(int(third_id) + 1)


def test_only_a_branch_can_be_made_a_thread_of_its_own(client):
    a, b, x = _make("A", "B", "X")
    threads.add(a, "main")
    threads.add(b, "main")
    with pytest.raises(threads.ThreadError, match="isn’t a branch"):
        threads.detach("main")
    client.post("/threads/change", data={"action": "detach", "thread": "main", "back": "thread"})
    assert flask_app._SWEEP["msg"] == "“main” isn’t a branch."
    threads.start_branch("aside", "main", a, x)
    threads.detach("aside")
    assert threads.parent_of("aside") is None


def test_the_branch_form_cannot_be_sent_twice(client):
    a, = _make("A")
    threads.add(a, "main")
    page = client.get("/thread/main").get_data(as_text=True)
    assert 'data-branch-form data-busy="Starting…"' in page
