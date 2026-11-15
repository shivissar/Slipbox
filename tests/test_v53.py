"""5.3: versions kept per note (a setting), existing notes in threads,
Markdown import, other apps' front-matter fields kept, release-notes
markup lines hidden, and a replaced or missing .bib file."""
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

_tmp = Path(tempfile.mkdtemp(prefix="z53-"))
os.environ.setdefault("ZETTEL_SETTINGS_PATH", str(_tmp / "settings.json"))
os.environ.setdefault("ZETTEL_NOTES_DIR", str(_tmp / "notes"))
os.environ.setdefault("ZETTEL_DB_PATH", str(_tmp / "zettel.db"))
os.environ["ZETTEL_VAULT_KDF"] = "light"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from Slipbox import (bib, db, embed, flask_app, history, importer, notes,  # noqa: E402
                     settings, threads, updates, vault)

app = flask_app.app
PW = "correct horse battery staple"


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
    app.testing = True
    yield app.test_client()
    vault.lock()


def _versions(note):
    return history.list_versions(note.id)


def _save_times(note, n):
    for i in range(n):
        note.body = "edit %d" % i
        notes.write_note(note)
        time.sleep(0.003)  # version names are stamped to the millisecond
    db.upsert_note(note)


# ---- versions kept per note ------------------------------------------------

def test_history_limit_comes_from_settings(client):
    settings.save({"history_max": 10})
    n = notes.create_note("Limited", body="start")
    _save_times(n, 14)
    assert len(_versions(n)) == 10
    assert history.max_versions() == 10


def test_history_limit_keeps_200_when_unsure(client, monkeypatch):
    settings.save({"history_max": 30})            # not one of the choices
    assert history.max_versions() == 200
    settings.save({"history_max": "nonsense"})
    assert history.max_versions() == 200
    settings.save({"history_max": 25})
    assert history.max_versions() == 25
    good = settings.SETTINGS_PATH.read_text()
    settings.SETTINGS_PATH.write_text("{not json")  # unreadable: the backup copy is used
    try:
        assert settings.load()["history_max"] == 25 and settings.PROBLEM
        assert history.max_versions() == 200      # while it can't be read, delete nothing
    finally:
        settings.SETTINGS_PATH.write_text(good)
        settings.load()


def test_history_limit_decided_once(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "zettel.db")
    path = tmp_path / "settings.json"
    monkeypatch.setattr(settings, "SETTINGS_PATH", path)
    flask_app._history_limit_once()                # a new install
    assert settings.stored()["history_max"] == 50
    path.unlink()
    path.write_text('{"welcomed": true}')         # in use before 5.3
    flask_app._history_limit_once()
    assert settings.stored()["history_max"] == 200
    settings.save({"history_max": 25})             # the person's choice stays
    flask_app._history_limit_once()
    assert settings.stored()["history_max"] == 25
    path.unlink()
    (tmp_path / "history" / "20260101000000").mkdir(parents=True)  # history but no settings
    flask_app._history_limit_once()
    assert settings.stored()["history_max"] == 200


def test_history_setting_and_trim_now(client):
    n = notes.create_note("Busy note", body="start")
    _save_times(n, 12)
    assert len(_versions(n)) == 12
    r = client.post("/history/settings", data={"history_max": "10"})
    assert r.status_code == 302 and "housekeeping" in r.location
    assert settings.get("history_max") == 10
    assert len(_versions(n)) == 12                 # nothing deleted by choosing
    page = client.get("/settings").get_data(as_text=True)
    assert "Versions kept per note" in page and "More versions use more disk space" in page
    assert '<option value="10" selected>' in page
    assert "Trim now" in page and "2 older versions of 1 note are beyond the limit" in page
    r = client.post("/history/trim")
    assert "Deleted+2+older+versions" in r.location or "Deleted%202%20older%20versions" in r.location
    assert len(_versions(n)) == 10
    assert "Trim now" not in client.get("/settings").get_data(as_text=True)
    assert client.post("/history/settings", data={"history_max": "7"}).status_code == 302
    assert settings.get("history_max") == 10       # not a choice: ignored


def test_history_page_states_the_limit(client):
    settings.save({"history_max": 25})
    n = notes.create_note("With history", body="one")
    _save_times(n, 2)
    page = client.get("/note/%s/history" % n.id).get_data(as_text=True)
    assert "keeps up to 25 of each note" in page


# ---- other apps' front-matter fields -----------------------------------------

def test_other_front_matter_fields_survive_a_save(client):
    p = notes.NOTES_DIR / "20260301101500-from-obsidian.md"
    p.write_text("---\ntitle: From Obsidian\naliases: [Obs, The vault note]\nsource: field diary\n"
                 "date: 2026-03-01\ntags: [a]\n---\n\nBody.\n")
    n = notes.parse_file(p)
    assert n.extra["aliases"] == ["Obs", "The vault note"] and n.extra["source"] == "field diary"
    n.body = "Body, edited."
    notes.write_note(n)
    again = notes.parse_file(p)
    assert again.extra["aliases"] == ["Obs", "The vault note"]
    assert str(again.extra["date"]) == "2026-03-01"
    assert again.body.strip() == "Body, edited." and again.title == "From Obsidian"
    assert "id" not in again.extra and "locked" not in again.extra


# ---- release notes on the update page ----------------------------------------

def test_release_notes_markup_lines_left_out():
    raw = ('<img src="https://raw.githubusercontent.com/x/y/main/logo.png" width="128" alt="logo">\n\n'
           "Slipbox 5.3.0 adds import.\n<!-- note to self -->\n![screenshot](shot.png)\n"
           "Press <kbd>⌘K</kbd> to search.\n<https://shivissar.com/slipbox>\n\n\n\nEnd.")
    out = updates.clean_notes(raw)
    assert out.startswith("Slipbox 5.3.0 adds import.")
    assert "<img" not in out and "note to self" not in out and "screenshot" not in out
    assert "Press <kbd>⌘K</kbd> to search." in out and "<https://shivissar.com/slipbox>" in out
    assert "\n\n\n" not in out


def test_stored_release_notes_cleaned(client):
    settings.save({"update_latest": {"version": "9.0.0", "notes": '<img src="x.png">\nNew things.'}})
    st = updates.status("5.3.0")
    assert st["latest"]["notes"] == "New things."


# ---- the bibliography file ------------------------------------------------------

def test_bib_reloads_when_replaced_by_a_rename(tmp_path, monkeypatch):
    target = tmp_path / "library.bib"
    h = bib.BibHandler(str(target))
    fired = []
    monkeypatch.setattr(h, "_trigger", lambda: fired.append(1))
    h.on_moved(SimpleNamespace(is_directory=False, src_path=str(tmp_path / ".library.bib.tmp"),
                               dest_path=str(target)))
    assert fired == [1]
    h.on_moved(SimpleNamespace(is_directory=False, src_path=str(target),
                               dest_path=str(tmp_path / "library.bib.bak")))
    assert fired == [1]                            # renamed away: not followed


def test_settings_says_when_bib_is_missing(client, tmp_path):
    bibfile = tmp_path / "refs.bib"
    bibfile.write_text("@book{suchman1987,\n  title = {Plans},\n  author = {Suchman, Lucy},\n  year = {1987}\n}\n")
    settings.save({"bib_path": str(bibfile)})
    bib.reload_bib()
    page = client.get("/settings").get_data(as_text=True)
    assert "In use now: 1 reference from" in page
    bibfile.rename(tmp_path / "renamed.bib")
    page = client.get("/settings").get_data(as_text=True)
    assert "can’t find this file" in page and "the 1 reference it last read is still used" in page


# ---- existing notes in threads ---------------------------------------------------

def _chain(name):
    return [c["id"] for c in db.thread_chain(name)]


def _entry(note_id, name):
    meta = db.get_note_meta(note_id)
    n = notes.parse_file(notes.NOTES_DIR / meta["filename"])
    hits = [t for t in n.threads if t.get("name") == name]
    assert len(hits) <= 1
    return hits[0] if hits else None


def _make(*titles, kind="research"):
    out = []
    for t in titles:
        n = notes.create_note(t, body="Text of %s." % t, kind=kind)
        db.upsert_note(n)
        out.append(n.id)
    return out


def _consistent(name):
    ids = _chain(name)
    for i, nid in enumerate(ids):
        e = _entry(nid, name)
        assert str(e["prev"] or "") == (ids[i - 1] if i else "")
        assert str(e["next"] or "") == (ids[i + 1] if i + 1 < len(ids) else "")
    return ids


def test_add_existing_notes_where_chosen(client):
    a, b, c, d, e, f = _make("A", "B", "C", "D", "E", "F")
    for nid in (a, b, c):
        threads.add(nid, "line")
    assert _consistent("line") == [a, b, c]
    assert threads.add(d, "line", after=a) == (2, 4)
    assert threads.add(e, "line", after="start") == (1, 5)
    threads.add(f, "line")
    assert _consistent("line") == [e, a, d, b, c, f]
    with pytest.raises(threads.ThreadError):
        threads.add(d, "line")


def test_remove_and_move(client):
    a, b, c, d = _make("A", "B", "C", "D")
    for nid in (a, b, c, d):
        threads.add(nid, "line")
    assert threads.remove(b, "line") == 3
    assert _consistent("line") == [a, c, d] and _entry(b, "line") is None
    threads.move(d, "line", -1)
    assert _consistent("line") == [a, d, c]
    threads.move(a, "line", -1)                    # already first: no change
    threads.move(a, "line", +1)
    assert _consistent("line") == [d, a, c]
    threads.remove(d, "line")
    threads.remove(a, "line")
    assert threads.remove(c, "line") == 0
    assert db.thread_chain("line") == [] and ("line", 1) not in db.all_threads()


def test_several_threads_any_kind_and_names(client):
    a, b = _make("A", "B", kind="journal")
    threads.add(a, "first")
    threads.add(a, "second")
    threads.add(b, "second", after=a)
    assert {t["name"] for t in notes.parse_file(next(notes.NOTES_DIR.glob(a + "*.md"))).threads} == {"first", "second"}
    assert _consistent("second") == [a, b]
    for bad in ("", "   ", "a/b", "x" * 81):
        with pytest.raises(threads.ThreadError):
            threads.clean_name(bad)
    assert threads.clean_name("  tethered   autonomy ") == "tethered autonomy"


def test_broken_chain_is_mended(client):
    a, b, c = _make("A", "B", "C")
    for nid in (a, b, c):
        threads.add(nid, "line")
    gone = notes.NOTES_DIR / db.get_note_meta(b)["filename"]
    gone.unlink()                                  # deleted in Finder; index not told yet
    threads.move(c, "line", -1)
    assert threads.order("line") == [c, a]
    assert _entry(c, "line") == {"name": "line", "prev": None, "next": a}
    assert _entry(a, "line") == {"name": "line", "prev": c, "next": None}


def test_locked_closed_note_moves_without_touching_its_text(client):
    vault.setup(PW, kdf=vault.ARGON2_LIGHT)
    a, = _make("A")
    secret = notes.create_note("Secret", body="the hidden fieldnote", locked=True)
    db.upsert_note(secret)
    threads.add(a, "line")
    threads.add(secret.id, "line")
    before = notes.parse_file(secret.path).cipher
    vault.lock()
    threads.move(secret.id, "line", -1)
    after = notes.parse_file(secret.path)
    assert after.unreadable and after.cipher == before
    assert _consistent("line") == [secret.id, a]
    vault.unlock(PW)
    assert notes.parse_file(secret.path).body == "the hidden fieldnote"


def test_thread_pages_and_routes(client):
    a, b, c, d = _make("Alpha", "Beta", "Gamma", "Delta")
    for nid in (a, b, c):
        threads.add(nid, "tethered autonomy")
    page = client.get("/note/" + d).get_data(as_text=True)
    assert "Add to a thread" in page and '<option value="tethered autonomy">' in page
    r = client.post("/threads/change", data={"action": "add", "note": d, "thread": "tethered autonomy", "after": a})
    assert r.status_code == 302 and r.location.endswith("/note/%s#threads" % d)
    assert _consistent("tethered autonomy") == [a, d, b, c]
    assert "Added to “tethered autonomy”, 2 of 4." == flask_app._SWEEP["msg"]
    page = client.get("/note/" + d).get_data(as_text=True)
    assert "Add to another thread" in page and "Take out of this thread" in page
    assert '<option value="tethered autonomy">' not in page    # already in it
    assert client.get("/api/thread?name=tethered%20autonomy").get_json()[1]["title"] == "Delta"
    page = client.get("/thread/tethered autonomy").get_data(as_text=True)
    assert page.count('aria-label="Move “') == 6 and "Add a note" in page
    r = client.post("/threads/change", data={"action": "down", "note": a, "thread": "tethered autonomy", "back": "thread"})
    assert "/thread/tethered" in r.location and r.location.endswith("#n-" + a)
    assert _chain("tethered autonomy") == [d, a, b, c]
    client.post("/threads/change", data={"action": "add", "note": "Delta · " + d, "thread": "", "new_thread": "second line"})
    assert _chain("second line") == [d]
    r = client.post("/threads/change", data={"action": "remove", "note": d, "thread": "second line", "back": "thread"})
    assert r.location.endswith("/threads") and "the thread is gone" in flask_app._SWEEP["msg"]
    client.post("/threads/change", data={"action": "add", "note": "no id here", "thread": "x"})
    assert "Choose a note" in flask_app._SWEEP["msg"]
    assert client.get("/thread/no such thread").status_code == 302


def test_new_note_of_any_kind_can_start_a_thread(client):
    r = client.post("/new", data={"title": "A journal entry", "body": "Today.", "kind": "journal",
                                  "thread_action": "new", "new_thread_name": "diary line"})
    assert r.status_code == 302
    assert len(_chain("diary line")) == 1
    assert "Choose <strong>Add to a thread</strong>" not in client.get("/threads").get_data(as_text=True)


# ---- importing Markdown ---------------------------------------------------------

def _source(tmp_path):
    src = tmp_path / "vault"
    (src / "sub").mkdir(parents=True)
    (src / ".obsidian").mkdir()
    (src / ".obsidian" / "workspace.md").write_text("hidden")
    (src / "Alpha.md").write_text(
        "---\ntitle: Alpha note\ntags: 'x, #y'\ncreated: 2021-03-04T10:00:00\naliases: [First]\n---\n"
        "# Alpha note\n\nSee [[Beta]], [[sub/Gamma|the third]], [[Beta#Part two]], [[Missing]] and "
        "![[pic.png]].\nNot in code: `[[Beta]]`.\n")
    (src / "Beta.md").write_text(
        "# Beta\n\nBack to [the first](Alpha.md), a [site](https://example.com) and "
        "![shot](img/shot.png).\n\n```\n[[Alpha]]\n```\n\n![[Gamma]]\n")
    (src / "sub" / "Gamma.md").write_text("---\ntags: ['#z', 'two words']\n---\nNo heading here; links [[First]].\n")
    (src / "20200101120000 Zettel.md").write_text("Points to [[20200101120001]].\n")
    (src / "20200101120001 Other.md").write_text("# Other\n\nAnd back to [[20200101120000]].\n")
    (src / "empty.md").write_text("  \n")
    (src / "latin.md").write_bytes("caf\xe9".encode("latin-1"))
    (src / "broken.md").write_text("---\ntitle: [unclosed\n---\nText.\n")
    (src / "pic.png").write_bytes(b"\x89PNG")
    (src / "notes.txt").write_text("not markdown")
    return src


def test_import_scan_and_run(client, tmp_path):
    src = _source(tmp_path)
    originals = {p: p.read_bytes() for p in src.rglob("*") if p.is_file()}
    existing = notes.create_note("Alpha note", body="already here")
    db.upsert_note(existing)

    plan = importer.scan(str(src), "teaching")
    titles = sorted(it.title for it in plan.items)
    assert titles == ["Alpha note", "Beta", "Gamma", "Other", "Zettel", "broken"]
    assert plan.found == 8                       # .md files outside hidden folders
    assert sorted(r for r, _ in plan.skipped) == ["empty.md", "latin.md"]
    assert plan.yaml_problems == ["broken.md"] and plan.clashes == ["Alpha note"]
    assert [l for _, l in plan.unmatched] == ["[[Missing]]"]
    assert plan.attachments == 2                # ![[pic.png]] and ![shot](img/shot.png)
    by = {it.title: it for it in plan.items}
    assert by["Alpha note"].id == "20210304100000"           # from its own date
    assert by["Zettel"].id == "20200101120000" and by["Other"].id == "20200101120001"
    assert by["Alpha note"].tags == ["x", "y", "imported"]
    assert by["Gamma"].tags == ["z", "two-words", "imported"]

    written, error = importer.run(plan)
    assert error == "" and len(written) == 6
    assert {p: p.read_bytes() for p in src.rglob("*") if p.is_file()} == originals

    def note(title):
        return notes.parse_file(notes.NOTES_DIR / by[title].filename)
    alpha, beta, gamma = note("Alpha note"), note("Beta"), note("Gamma")
    assert alpha.kind == "teaching" and "imported" in alpha.tags
    assert not alpha.body.lstrip().startswith("# Alpha note")      # repeated title left out
    assert "[[%s]]" % beta.id in alpha.body
    assert "[[%s|the third]]" % gamma.id in alpha.body
    assert "[[%s|Beta › Part two]]" % beta.id in alpha.body
    assert "[[Missing]]" in alpha.body and "![[pic.png]]" in alpha.body
    assert "`[[Beta]]`" in alpha.body                              # code untouched
    assert alpha.extra["aliases"] == ["First"] and alpha.created.startswith("2021-03-04T10:00:00")
    assert "[[%s|the first]]" % alpha.id in beta.body and "https://example.com" in beta.body
    assert "```\n[[Alpha]]\n```" in beta.body and "![[%s]]" % gamma.id in beta.body
    assert "[[%s|First]]" % alpha.id in gamma.body                 # matched by alias
    assert "[[20200101120001]]" in note("Zettel").body and "[[20200101120000]]" in note("Other").body
    assert note("broken").body.startswith("---\ntitle: [unclosed")
    assert len(db.notes_by_tag("imported")) == 6
    assert alpha.id in {b["id"] for b in db.get_backlinks(beta.id)}


def test_import_ids_avoid_notes_already_there(client, tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    for name in ("one.md", "two.md"):
        (src / name).write_text("---\ncreated: 2022-05-06T07:08:09\n---\nText.\n")
    there = notes.NOTES_DIR / "20220506070809-there.md"
    there.write_text("---\ntitle: There\n---\nx\n")
    db.upsert_note(notes.parse_file(there))
    plan = importer.scan(str(src), "research")
    assert sorted(it.id for it in plan.items) == ["20220506070810", "20220506070811"]


def test_import_refuses_unsuitable_folders(client, tmp_path):
    for raw in ("", "relative/path", str(tmp_path / "nowhere"), str(notes.NOTES_DIR)):
        with pytest.raises(importer.ImportProblem):
            importer.scan(raw, "research")
    big = tmp_path / "big"
    big.mkdir()
    for i in range(5):
        (big / ("%d.md" % i)).write_text("x")
    with pytest.raises(importer.ImportProblem):
        old = importer.MAX_FILES
        importer.MAX_FILES = 3
        try:
            importer.scan(str(big), "research")
        finally:
            importer.MAX_FILES = old


def test_import_pages(client, tmp_path):
    src = tmp_path / "two"
    src.mkdir()
    (src / "First.md").write_text("Links to [[Second]].\n")
    (src / "Second.md").write_text("# Second\n\nText.\n")
    page = client.get("/settings").get_data(as_text=True)
    assert 'id="import"' in page and "Check the folder" in page
    r = client.post("/import/preview", data={"folder": str(src), "kind": "life"})
    page = r.get_data(as_text=True)
    assert r.status_code == 200 and "Import 2 notes" in page and "Nothing has been imported yet" in page
    assert not list(notes.NOTES_DIR.glob("*.md"))
    r = client.post("/import/run", data={"folder": str(src), "kind": "life"})
    assert r.status_code == 302 and "/import/done/" in r.location
    page = client.get(r.location).get_data(as_text=True)
    assert "Imported 2 notes" in page and "as Life notes" in page
    assert len(list(notes.NOTES_DIR.glob("*.md"))) == 2
    client.get(r.location)                                         # reloading imports nothing
    assert len(list(notes.NOTES_DIR.glob("*.md"))) == 2
    r = client.post("/import/preview", data={"folder": "~/no/such/folder/here", "kind": "life"})
    assert r.status_code == 302 and "import_msg=" in r.location and r.location.endswith("#import")
