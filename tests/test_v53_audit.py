"""5.3 audit (15 Nov 2026): the import keeps what it can't read as fields,
gives IDs in one pass, can't run twice, and says when a folder is closed to
it or its files are still in iCloud; a locked note's other fields are named
before locking; the history limit can't fall by itself; thread changes
don't count as edits; manuscripts are built on their own page."""
import errno
import os
import re
import sys
import tempfile
import time
import tomllib
from datetime import datetime
from pathlib import Path

import pytest

_tmp = Path(tempfile.mkdtemp(prefix="z53a-"))
os.environ.setdefault("ZETTEL_SETTINGS_PATH", str(_tmp / "settings.json"))
os.environ.setdefault("ZETTEL_NOTES_DIR", str(_tmp / "notes"))
os.environ.setdefault("ZETTEL_DB_PATH", str(_tmp / "zettel.db"))
os.environ["ZETTEL_VAULT_KDF"] = "light"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from Slipbox import (db, embed, flask_app, history, importer, notes,  # noqa: E402
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
    flask_app._SWEEP["msg"] = ""
    flask_app._IMPORT_PREVIEWS.clear()
    flask_app._IMPORT_REPORTS.clear()
    flask_app._USAGE["value"] = None
    app.testing = True
    yield app.test_client()
    vault.lock()


def _folder(tmp_path, files, name="vault"):
    src = tmp_path / name
    for rel, text in files.items():
        f = src / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    src.mkdir(exist_ok=True)
    return src


def _by_title(plan):
    return {it.title: it for it in plan.items}


def _make(*titles, kind="research", body=None):
    out = []
    for t in titles:
        n = notes.create_note(t, body=body or "Text of %s." % t, kind=kind)
        db.upsert_note(n)
        out.append(n.id)
    return out


def _note(note_id):
    return notes.parse_file(notes.NOTES_DIR / db.get_note_meta(note_id)["filename"])


def _chain(name):
    return [c["id"] for c in db.thread_chain(name)]


def _preview(client, src, kind="research"):
    r = client.post("/import/preview", data={"folder": str(src), "kind": kind})
    page = r.get_data(as_text=True)
    m = re.search(r'name="token" value="([^"]+)"', page)
    return page, (m.group(1) if m else "")


def _files_on_disk():
    return sorted(p.name for p in notes.NOTES_DIR.glob("*.md"))


# ---- A1: nothing at the top of a file is dropped ------------------------------

def test_opening_block_that_is_not_fields_stays_in_the_text(client, tmp_path):
    src = _folder(tmp_path, {
        "Prose.md": "---\nA line of prose between two rules.\n---\n\nThe body.\n",
        "Heading.md": "---\n# Only a heading\n---\nUnder it.\n",
        "List.md": "---\n- one\n- two\n---\nAfter the list.\n",
        "Numbers.md": "---\n1: one\n2: two\n---\nNumbered keys.\n",
        "Broken.md": '---\ntitle: "unclosed\n---\nStill here.\n',
        "Real.md": "---\ntitle: A real one\nsource: zotero\n---\nBody of the real one.\n",
        "Empty.md": "---\n---\nNothing in the block.\n",
        "Rule.md": "---\n\nJust a rule at the top, never closed.\n",
    })
    plan = importer.scan(str(src), "research")
    got = _by_title(plan)
    assert "A line of prose between two rules." in got["Prose"].body and got["Prose"].body.startswith("---")
    assert "# Only a heading" in got["Heading"].body
    assert "- one\n- two" in got["List"].body
    assert "1: one" in got["Numbers"].body
    assert 'title: "unclosed' in got["Broken"].body
    assert got["A real one"].body == "Body of the real one." and got["A real one"].extra == {"source": "zotero"}
    assert got["Empty"].body == "Nothing in the block."
    assert got["Rule"].body.startswith("---")
    assert sorted(plan.yaml_problems) == ["Broken.md", "Heading.md", "List.md", "Numbers.md", "Prose.md"]
    page, _ = _preview(client, src)
    assert "Opening lines kept as text" in page and "Nothing is taken out" in page


# ---- A2: IDs in one pass, one import per preview, 2,000 files -------------------

def test_ids_for_many_files_with_one_date_are_quick_and_distinct(client, tmp_path):
    files = {"n%04d.md" % i: "---\ndate: 2021-03-04\n---\nNote %d.\n" % i for i in range(1500)}
    src = _folder(tmp_path, files)
    plan = importer.Plan(folder=src, kind="research")
    items = [importer._read(src / rel, rel, plan) for rel in sorted(files)]
    start = time.perf_counter()
    importer._assign_ids(items)
    took = time.perf_counter() - start
    ids = [it.id for it in items]
    assert took < 2.0, "assigning 1,500 IDs took %.1f s" % took
    assert len(set(ids)) == 1500 and all(re.fullmatch(r"\d{14}", i) for i in ids)
    assert ids == sorted(ids) and ids[0] == "20210304000000" and ids[-1] == "20210304002459"


def test_ids_step_over_notes_already_there(client, tmp_path):
    taken = notes.NOTES_DIR / "20210304000001-there.md"
    taken.write_text("---\nid: '20210304000001'\ntitle: There\n---\nx\n")
    db.upsert_note(notes.parse_file(taken))
    src = _folder(tmp_path, {"a.md": "---\ndate: 2021-03-04\n---\nA\n", "b.md": "---\ndate: 2021-03-04\n---\nB\n",
                             "c.md": "---\ndate: 2021-03-04\n---\nC\n"})
    plan = importer.scan(str(src), "research")
    assert sorted(it.id for it in plan.items) == ["20210304000000", "20210304000002", "20210304000003"]


def test_import_pressed_twice_imports_once(client, tmp_path):
    src = _folder(tmp_path, {"One.md": "First.\n", "Two.md": "Second, to [[One]].\n"})
    page, token = _preview(client, src)
    assert token and 'data-busy="Importing…"' in page
    first = client.post("/import/run", data={"folder": str(src), "kind": "research", "token": token})
    again = client.post("/import/run", data={"folder": str(src), "kind": "research", "token": token})
    assert first.status_code == again.status_code == 302 and first.location == again.location
    assert "/import/done/" in first.location and len(_files_on_disk()) == 2
    assert "Imported 2 notes" in client.get(first.location).get_data(as_text=True)


def test_import_without_a_preview_shows_one_instead(client, tmp_path):
    src = _folder(tmp_path, {"One.md": "First.\n"})
    for token in ("", "made-up"):
        r = client.post("/import/run", data={"folder": str(src), "kind": "research", "token": token})
        page = r.get_data(as_text=True)
        assert r.status_code == 200 and "nothing was imported" in page and "Import 1 note" in page
        assert not _files_on_disk()
    token = re.search(r'name="token" value="([^"]+)"', page).group(1)
    assert client.post("/import/run", data={"folder": str(src), "kind": "research", "token": token}).status_code == 302
    assert len(_files_on_disk()) == 1


def test_one_import_takes_two_thousand_files(client, tmp_path, monkeypatch):
    assert importer.MAX_FILES == 2000
    assert "up to 2,000 files" in client.get("/settings").get_data(as_text=True)
    monkeypatch.setattr(importer, "MAX_FILES", 3)
    src = _folder(tmp_path, {"n%d.md" % i: "x\n" for i in range(4)})
    with pytest.raises(importer.ImportProblem, match="more than 3 Markdown files.*a part at a time"):
        importer.scan(str(src), "research")


def test_report_does_not_keep_the_notes_text(client, tmp_path):
    src = _folder(tmp_path, {"One.md": "---\nsource: x\n---\nA long body.\n"})
    _, token = _preview(client, src)
    client.post("/import/run", data={"folder": str(src), "kind": "research", "token": token})
    report = next(iter(flask_app._IMPORT_REPORTS.values()))
    assert all(it.body == "" and it.extra == {} for it in report["plan"].items)
    assert "A long body." in notes.parse_file(notes.NOTES_DIR / _files_on_disk()[0]).body


# ---- A3: a locked note's other fields are named ----------------------------------

def test_locking_names_the_fields_that_stay_readable(client, tmp_path):
    assert "so do any fields another app put at the top of the file" in client.get("/settings").get_data(as_text=True)
    vault.setup(PW, kdf=vault.ARGON2_LIGHT)
    plain, = _make("Plain")
    f = notes.NOTES_DIR / "20240101000000-carried.md"
    f.write_text("---\nid: '20240101000000'\ntitle: Carried\naliases: [C]\nsource: zotero\n---\nPrivate text.\n")
    db.upsert_note(notes.parse_file(f))
    page = client.get("/note/20240101000000").get_data(as_text=True)
    assert "Its fields from another app (aliases, source) stay readable in the file" in page
    assert "fields from another app" not in client.get("/note/" + plain).get_data(as_text=True)
    assert "any fields another app put at the top of its file stay readable" in client.get("/settings").get_data(as_text=True)
    assert flask_app._field_names(list("abcdefgh")) == "a, b, c, d, e, f and 2 more"


def test_sweep_names_the_fields_too(client):
    vault.setup(PW, kdf=vault.ARGON2_LIGHT)
    f = notes.NOTES_DIR / "20240101000000-arrived.md"
    f.write_text("---\nid: '20240101000000'\ntitle: Arrived\nkind: journal\naliases: [A]\n---\nPrivate text.\n")
    db.upsert_note(notes.parse_file(f))
    settings.save({"locked_kinds": ["journal"]})
    assert flask_app.sweep_locked_kinds(force=True) == 1
    assert "Fields from another app (aliases) stay readable" in flask_app._SWEEP["msg"]
    text = f.read_text()
    assert "Private text." not in text and "aliases" in text


def test_import_into_an_always_locked_kind(client, tmp_path):
    vault.setup(PW, kdf=vault.ARGON2_LIGHT)
    settings.save({"locked_kinds": ["journal"]})
    src = _folder(tmp_path, {"Diary.md": "---\naliases: [D]\n---\nWhat I really think.\n"})
    # Locked notes open: encrypted as it is written, never plain on disk.
    page, token = _preview(client, src, "journal")
    assert "each note is encrypted as it is imported" in page and "Fields from another app (aliases)" in page
    r = client.post("/import/run", data={"folder": str(src), "kind": "journal", "token": token})
    page = client.get(r.location).get_data(as_text=True)
    assert "encrypted as it was imported" in page
    text = (notes.NOTES_DIR / _files_on_disk()[0]).read_text()
    assert "What I really think." not in text and "locked: true" in text and "aliases" in text
    note = notes.parse_file(notes.NOTES_DIR / _files_on_disk()[0])
    assert note.locked and note.body.strip() == "What I really think."
    assert not history.list_versions(note.id)
    # Locked notes closed: said beforehand, plain for now, locked by the sweep later.
    vault.lock()
    page, token = _preview(client, src, "journal")
    assert "locked notes are closed" in page and "open locked notes</a> first" in page
    r = client.post("/import/run", data={"folder": str(src), "kind": "journal", "token": token})
    assert "in plain text for now" in client.get(r.location).get_data(as_text=True)
    # A kind that isn't locked says nothing about locking.
    page, _ = _preview(client, src, "research")
    assert "always locked" not in page


# ---- A4: folders that can't be read, files still in iCloud --------------------------

def _refuse(monkeypatch, refused, code=errno.EPERM):
    real = os.scandir

    def scandir(path="."):
        if Path(path) == refused:
            raise PermissionError(code, os.strerror(code), str(path))
        return real(path)
    monkeypatch.setattr(os, "scandir", scandir)


def test_a_folder_macos_keeps_closed_is_said_to_be_closed(client, tmp_path, monkeypatch):
    src = _folder(tmp_path, {"One.md": "x\n", "Sub/Two.md": "y\n"})
    _refuse(monkeypatch, src)
    with pytest.raises(importer.ImportProblem, match="macOS didn’t let Slipbox read .* Files and Folders"):
        importer.scan(str(src), "research")
    r = client.post("/import/preview", data={"folder": str(src), "kind": "research"})
    assert r.status_code == 302
    page = client.get(r.location).get_data(as_text=True)
    assert "System Settings → Privacy &amp; Security → Files and Folders → Slipbox" in page
    _refuse(monkeypatch, src, errno.EACCES)
    with pytest.raises(importer.ImportProblem, match="doesn’t have permission .* Sharing & Permissions"):
        importer.scan(str(src), "research")


def test_a_closed_subfolder_is_listed_and_the_rest_imported(client, tmp_path, monkeypatch):
    src = _folder(tmp_path, {"One.md": "x\n", "Sub/Two.md": "y\n"})
    _refuse(monkeypatch, src / "Sub")
    plan = importer.scan(str(src), "research")
    assert [it.rel for it in plan.items] == ["One.md"] and plan.unreadable == [("Sub", errno.EPERM)]
    page, _ = _preview(client, src)
    assert "Folders that couldn’t be opened" in page and "<li>Sub</li>" in page


def test_permission_advice_appears_only_when_needed(client, tmp_path):
    src = _folder(tmp_path, {"One.md": "x\n"})
    page, _ = _preview(client, src)
    for words in ("System Settings", "iCloud", "couldn’t be opened", "Don’t Allow"):
        assert words not in page
    settings_page = client.get("/settings").get_data(as_text=True)
    assert "Files and Folders" not in settings_page and "Don’t Allow" not in settings_page


def test_a_folder_that_cannot_be_looked_at_is_not_called_missing(client, tmp_path, monkeypatch):
    src = _folder(tmp_path, {"One.md": "x\n"})
    real = os.stat

    def stat(path, *a, **k):
        if Path(path) == src:
            raise PermissionError(errno.EPERM, "Operation not permitted", str(path))
        return real(path, *a, **k)
    monkeypatch.setattr(os, "stat", stat)
    with pytest.raises(importer.ImportProblem, match="macOS didn’t let Slipbox read"):
        importer.resolve_folder(str(src))


def test_files_still_in_icloud_are_counted(client, tmp_path):
    src = _folder(tmp_path, {"Here.md": "x\n"})
    (src / ".Away.md.icloud").write_bytes(b"stub")
    (src / "Sub").mkdir()
    (src / "Sub" / ".Also away.markdown.icloud").write_bytes(b"stub")
    (src / ".photo.png.icloud").write_bytes(b"stub")     # not a note
    plan = importer.scan(str(src), "research")
    assert plan.cloud_only == 2 and plan.cloud_examples == ["Away.md", "Sub/Also away.markdown"]
    page, _ = _preview(client, src)
    assert "Files still in iCloud" in page and "Download Now" in page and "Away.md" in page
    only = _folder(tmp_path, {}, name="only-cloud")
    (only / ".Away.md.icloud").write_bytes(b"stub")
    page, _ = _preview(client, only)
    assert "nothing to import from this folder yet" in page and "Files still in iCloud" in page


# ---- A5: the history limit can't fall by itself ------------------------------------

def test_unreadable_settings_at_first_start_never_lower_the_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "zettel.db")
    path = tmp_path / "settings.json"
    monkeypatch.setattr(settings, "SETTINGS_PATH", path)
    assert settings.DEFAULTS["history_max"] == 200
    path.write_text("{ this is not JSON")
    flask_app._history_limit_once()
    assert history.max_versions() == 200
    settings.save({"update_checked_at": "today"})   # any later save, such as the update check's
    assert settings.stored()["history_max"] == 200 and history.max_versions() == 200
    settings.PROBLEM.clear()
    settings.RECOVERED.clear()


def test_new_install_still_starts_at_fifty(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "zettel.db")
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    flask_app._history_limit_once()
    assert settings.stored()["history_max"] == 50 and history.max_versions() == 50


# ---- B1, B2: thread changes ------------------------------------------------------

def _dates_and_versions(ids):
    return {i: (_note(i).modified, len(history.list_versions(i))) for i in ids}


def test_thread_changes_are_not_edits(client, monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(notes, "now_iso", lambda: "2020-01-01T10:00:00")
        ids = _make("A", "B", "C", "D")
    before = _dates_and_versions(ids)
    assert all(d == "2020-01-01T10:00:00" for d, _ in before.values())
    for i in ids:
        threads.add(i, "line")
    threads.move(ids[0], "line", 1)
    threads.move(ids[3], "line", -1)
    threads.remove(ids[1], "line")
    assert _chain("line") == [ids[0], ids[3], ids[2]]
    assert _dates_and_versions(ids) == before
    n = _note(ids[0])                      # an edit still counts as one
    n.body = "changed"
    notes.write_note(n)
    assert _note(ids[0]).modified != "2020-01-01T10:00:00" and len(history.list_versions(ids[0])) == 1


def test_unreadable_member_stops_the_change_before_any_write(client):
    a, b, c = _make("Alpha", "Beta", "Gamma")
    for i in (a, b, c):
        threads.add(i, "line")
    bad = _note(b).path
    bad.write_text(bad.read_text().replace("title: Beta", 'title: "Beta'))   # an unclosed quotation mark
    before = {i: _note(i).path.read_bytes() for i in (a, c)}
    with pytest.raises(threads.ThreadError, match="“Beta” can’t be read, so the thread wasn’t changed"):
        threads.move(c, "line", -1)
    assert {i: _note(i).path.read_bytes() for i in (a, c)} == before
    r = client.post("/threads/change", data={"action": "up", "note": c, "thread": "line", "back": "thread"})
    assert r.status_code == 302 and "can’t be read" in flask_app._SWEEP["msg"]


# ---- B3: dates before 1900 ---------------------------------------------------------

def test_an_unset_date_does_not_make_a_short_id(client, tmp_path):
    src = _folder(tmp_path, {"Hugo.md": "---\ntitle: Hugo post\ndate: 0001-01-01T00:00:00Z\ncreated: 0001-01-01\n---\nx\n"})
    plan = importer.scan(str(src), "research")
    it = plan.items[0]
    assert re.fullmatch(r"\d{14}", it.id) and it.id.startswith(str(datetime.now().year))
    assert it.created.year == datetime.now().year
    assert it.extra["source-created"].year == 1 and it.extra["date"].year == 1     # kept, not used
    assert importer._stamp(datetime(987, 6, 5, 4, 3, 2)) == "09870605040302"


# ---- B4: the release-notes cleaner --------------------------------------------------

@pytest.mark.parametrize("line", ["<!-- a -->" * 60 + " text", "<br />" * 60 + "x", "<p a='1'>" * 60 + "x",
                                  "<!-- " * 2000, "<a " * 2000])
def test_release_notes_cleaner_cannot_hang(line):
    start = time.perf_counter()
    assert updates.clean_notes(line + "\nkept") == (line + "\nkept").strip()
    assert time.perf_counter() - start < 0.5


def test_release_notes_cleaner_still_drops_markup_lines():
    text = ('<img src="a.png" width="90" alt="Slipbox">\n\nHello <b>x</b>\n<!-- c --> <!-- d -->\n![logo](a.png)\n'
            '<https://x.org>\n<br/>\n<p align="center"><img src="x"/></p>\n\nEnd')
    assert updates.clean_notes(text) == "Hello <b>x</b>\n<https://x.org>\n\nEnd"


# ---- B5, C7: link forms -----------------------------------------------------------

def test_link_forms(client, tmp_path):
    src = _folder(tmp_path, {
        "Hub.md": ("In a sentence ![[Target]] goes on.\n\n![[Target]]\n\n![[Target#Part two]]\n\n"
                   "| a | b |\n|---|---|\n| [[Target\\|the words]] | [[Target\\|Target]] |\n\n"
                   "[[Chapter 1.2]] [[pic.png]] [[#Heading here]]\n\n"
                   "[spaces](<Sub folder/Deep note.md>) [bare](Target) [gone](Nowhere) "
                   "[web](https://example.org/a#b) [file](doc.pdf) [titled](Target.md 'a title')\n"),
        "Target.md": "The target.\n",
        "Sub folder/Deep note.md": "[up](../Hub.md) [from the top](Target.md) [rooted](/Hub.md) [[Hub]]\n",
        "Sub folder/Other/Far.md": "[by name](Deep%20note.md)\n",
    })
    plan = importer.scan(str(src), "research")
    got = _by_title(plan)
    hub, target, deep = got["Hub"], got["Target"], got["Deep note"]
    t, h, d = target.id, hub.id, deep.id
    lines = hub.body.split("\n")
    assert lines[0] == "In a sentence [[%s]] goes on." % t          # not "!" and a link
    assert lines[2] == "![[%s]]" % t                                 # a whole note on its own line
    assert lines[4] == "[[%s|Target › Part two]]" % t                # part of a note: a link
    assert lines[8] == "| [[%s]] | [[%s]] |" % (t, t)                # no | inside a table cell
    assert lines[10] == "[[Chapter 1.2]] [[pic.png]] [[#Heading here]]"
    assert lines[12] == ("[[%s|spaces]] [[%s|bare]] [gone](Nowhere) [web](https://example.org/a#b) "
                         "[file](doc.pdf) [[%s|titled]]" % (d, t, t))
    assert deep.body == "[[%s|up]] [[%s|from the top]] [[%s|rooted]] [[%s]]" % (h, t, h, h)
    assert got["Far"].body == "[[%s|by name]]" % d
    assert plan.unmatched == [("Hub.md", "[[Chapter 1.2]]"), ("Hub.md", "[gone](Nowhere)")]
    assert plan.attachment_examples == [("Hub.md", "[[pic.png]]"), ("Hub.md", "[file](doc.pdf)")]
    assert plan.embeds_as_links == 2 and plan.table_links == 1
    page, _ = _preview(client, src)
    assert "Links written differently" in page and "2 embeds" in page and "1 link inside a table shows" in page


# ---- B6, B7: fields and tags --------------------------------------------------------

def test_fields_slipbox_uses_itself_are_kept_under_another_name(client, tmp_path):
    src = _folder(tmp_path, {
        "Lit.md": ("---\ntitle: Lit note\ntype: literature\nid: zk-0042\nkind: permanent\nlocked: false\n"
                   "threads: [old]\nmodified: last Tuesday\nrefs: {a: b}\nsource-type: already here\n---\nBody #idea.\n"),
        "Own.md": "---\ntitle: Slipbox's own\ntype: zettel\nkind: research\n---\nBody.\n",
    })
    plan = importer.scan(str(src), "research")
    lit, own = _by_title(plan)["Lit note"], _by_title(plan)["Slipbox's own"]
    assert lit.extra == {"source-type": "already here", "source-type-imported": "literature", "source-id": "zk-0042",
                         "source-kind": "permanent", "source-locked": False, "source-threads": ["old"],
                         "source-modified": "last Tuesday", "source-refs": {"a": "b"}}
    assert own.extra == {} and lit.type == "zettel"
    assert plan.renamed == {"type": 1, "id": 1, "kind": 1, "locked": 1, "threads": 1, "modified": 1, "refs": 1}
    page, token = _preview(client, src)
    assert "Fields kept under another name" in page and "<code>source-type</code>" in page
    assert "1 file has tags written in the text" in page and plan.inline_tags == 1
    client.post("/import/run", data={"folder": str(src), "kind": "research", "token": token})
    note = next(n for n in map(notes.parse_file, notes.NOTES_DIR.glob("*.md")) if n.title == "Lit note")
    assert note.extra["source-id"] == "zk-0042" and note.extra["source-kind"] == "permanent"
    assert note.tags == ["imported"] and "#idea" in note.body and note.threads == []


def test_inline_tags_are_not_confused_with_other_hashes(client, tmp_path):
    src = _folder(tmp_path, {
        "No.md": "## Heading\n\nColour #fff and #a1b2c3, [here](#part), [[#Part]], https://x.org/p#frag, C# and `#code`.\n",
        "Yes.md": "Tagged #field/site at the end.\n",
    })
    plan = importer.scan(str(src), "research")
    assert plan.inline_tags == 1


# ---- B8, B9, C8: thread names and the note picker -----------------------------------

def test_new_note_form_checks_the_thread_name(client):
    r = client.post("/new", data={"title": "Slashed", "body": "x", "kind": "research",
                                  "thread_action": "new", "new_thread_name": "a/b"})
    assert r.status_code == 302 and db.all_threads() == []
    assert "The note is saved, but it isn’t in a thread" in flask_app._SWEEP["msg"] and "slash" in flask_app._SWEEP["msg"]
    assert len(_files_on_disk()) == 1


def test_new_with_the_name_of_an_existing_thread_joins_it(client):
    a, b = _make("A", "B")
    threads.add(a, "line")
    threads.add(b, "line")
    client.post("/new", data={"title": "Third", "body": "x", "kind": "life",
                              "thread_action": "new", "new_thread_name": "line"})
    chain = _chain("line")
    assert len(chain) == 3 and chain[:2] == [a, b]
    heads = [i for i in chain if not next(t for t in _note(i).threads if t["name"] == "line")["prev"]]
    assert heads == [a]
    client.post("/new", data={"title": "Fourth", "body": "x", "kind": "research",
                              "thread_action": "existing", "existing_thread_name": "line"})
    assert len(_chain("line")) == 4 and _note(_chain("line")[3]).title == "Fourth"


def test_a_thread_with_a_slash_in_its_name_can_be_opened(client):
    a, = _make("A")
    threads._write("old/name", [a])          # made before the name was checked, or by hand
    r = client.get("/thread/old/name")
    assert r.status_code == 200 and "old/name" in r.get_data(as_text=True)


def test_picker_reads_the_id_not_a_number_in_the_title(client):
    a, = _make("A")
    odd, = _make("Call log 20190101123456 transcript")
    threads.add(a, "line")
    client.post("/threads/change", data={"action": "add", "thread": "line",
                                         "note": "Call log 20190101123456 transcript · " + odd})
    assert _chain("line") == [a, odd]
    assert flask_app._picked_id("x 123456789012345678 · " + odd) == odd
    assert flask_app._picked_id("no number") == ""


# ---- C1, C3: which folder --------------------------------------------------------

def test_importing_a_folder_that_holds_the_notes_folder(client, tmp_path):
    inside = notes.NOTES_DIR / "attachments" / "deep"
    inside.mkdir(parents=True)
    (inside / "Stray.md").write_text("inside the notes folder\n")
    (notes.NOTES_DIR / "20240101000000-own.md").write_text("---\ntitle: Own\n---\nx\n")
    (tmp_path / "Outside.md").write_text("outside\n")
    plan = importer.scan(str(tmp_path), "research")
    assert [it.rel for it in plan.items] == ["Outside.md"]


def test_paths_as_people_paste_them(client, tmp_path):
    src = _folder(tmp_path, {"One.md": "x\n"}, name="My Notes")
    want = src.resolve()
    assert importer.resolve_folder('"%s"' % src) == want
    assert importer.resolve_folder("'%s'" % src) == want
    assert importer.resolve_folder("“%s”" % src) == want
    assert importer.resolve_folder(str(src).replace(" ", "\\ ")) == want
    assert importer.resolve_folder("file://" + str(src).replace(" ", "%20")) == want
    assert importer.resolve_folder("  %s/  " % src) == want
    with pytest.raises(importer.ImportProblem, match="is a file, not a folder"):
        importer.resolve_folder(str(src / "One.md"))
    with pytest.raises(importer.ImportProblem, match="can’t find a folder"):
        importer.resolve_folder(str(src) + "-missing")


# ---- C5: counting the history ------------------------------------------------------

def test_a_slow_history_count_is_remembered_briefly(client, monkeypatch):
    calls = []

    def slow(limit):
        calls.append(limit)
        time.sleep(0.3)
        return {"notes": 1, "versions": 1, "bytes": 1, "excess_versions": 0, "excess_bytes": 0, "excess_notes": 0}
    monkeypatch.setattr(history, "usage_all", slow)
    flask_app._history_usage(200)
    flask_app._history_usage(200)
    assert calls == [200]                       # the second came from memory
    flask_app._history_usage(50)
    assert calls == [200, 50]                   # another limit is another count
    client.post("/history/trim")
    flask_app._history_usage(50)
    assert calls == [200, 50, 50]               # trimming forgets it, so it is counted again


def test_a_quick_history_count_is_always_fresh(client):
    a, = _make("A")
    assert flask_app._history_usage(200)["versions"] == 0
    n = _note(a)
    n.body = "edit"
    notes.write_note(n)
    assert flask_app._history_usage(200)["versions"] == 1


# ---- C9: wording for macOS's folder prompts -----------------------------------------

def test_pyproject_gives_macos_slipboxs_own_wording():
    info = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["briefcase"]["app"]["Slipbox"]["macOS"]["info"]
    for key in ("NSDesktopFolderUsageDescription", "NSDocumentsFolderUsageDescription",
                "NSDownloadsFolderUsageDescription", "NSRemovableVolumesUsageDescription",
                "NSNetworkVolumesUsageDescription", "NSFileProviderDomainUsageDescription"):
        assert info[key].startswith("Slipbox reads")


# ---- Manuscripts: built on their own page ---------------------------------------------

def _new_manuscript(client):
    r = client.post("/manuscripts/new")
    assert r.status_code == 302 and "/note/" in r.location and r.location.endswith("#build")
    return re.search(r"/note/(\d{14})", r.location).group(1)


def test_a_new_manuscript_opens_on_its_own_page(client):
    ms = _new_manuscript(client)
    page = client.get("/note/" + ms).get_data(as_text=True)
    assert "Build this manuscript" in page and "It’s empty so far." in page
    for label in ("Name it", "Add a note", "Add a thread", "Add a heading"):
        assert ">%s<" % label in page
    assert "You have no threads yet" in page
    r = client.post("/note/%s/outline" % ms, data={"action": "rename", "title": "  Chapter  two ", "from": "page"})
    assert r.location.endswith("#build") and _note(ms).title == "Chapter two"
    page = client.get("/note/" + ms).get_data(as_text=True)
    assert "Name it" not in page and "Chapter two" in page
    plain, = _make("An ordinary note")
    assert "Build this manuscript" not in client.get("/note/" + plain).get_data(as_text=True)


def test_notes_threads_and_headings_are_added_from_the_page(client):
    ms = _new_manuscript(client)
    a, b, c = _make("Alpha", "Beta", "Gamma")
    odd, = _make("Call log 20190101123456 transcript")
    for i in (a, b, c):
        threads.add(i, "argument")
    post = lambda **data: client.post("/note/%s/outline" % ms, data=dict(data, **{"from": "page"}))  # noqa: E731
    post(action="add_heading", heading="## Part one")
    post(action="add_note", note="Beta · " + b)
    assert "Added “Beta”." == flask_app._SWEEP["msg"]
    post(action="add_note", note="Beta · " + b)
    assert "already in this manuscript" in flask_app._SWEEP["msg"]
    post(action="add_thread", thread="argument")
    assert flask_app._SWEEP["msg"] == "Added 2 notes from “argument”, in the thread’s order. 1 was already here."
    post(action="add_thread", thread="argument")
    assert "Every note of “argument” is already in this manuscript." == flask_app._SWEEP["msg"]
    post(action="add_note", note="Call log 20190101123456 transcript · " + odd)
    post(action="add_note", note="nothing chosen")
    assert "Choose a note" in flask_app._SWEEP["msg"]
    assert _note(ms).body.split("\n") == ["## Part one", "", "![[%s]]" % b, "", "![[%s]]" % a, "![[%s]]" % c,
                                          "", "![[%s]]" % odd]
    page = client.get("/note/" + ms).get_data(as_text=True)
    assert '<option value="argument">argument (3 notes)</option>' in page and "It’s empty so far." not in page
    assert "Text of Alpha." in page and "Text of Gamma." in page        # reads as one piece
    r = client.post("/note/%s/outline" % ms, data={"action": "add_heading", "heading": "From the panel"})
    assert r.location.endswith("#outline")


def test_the_editor_says_what_a_manuscript_is(client):
    ms = _new_manuscript(client)
    plain, = _make("An ordinary note")
    assert "This is a manuscript" in client.get("/note/%s/edit" % ms).get_data(as_text=True)
    assert "This is a manuscript" not in client.get("/note/%s/edit" % plain).get_data(as_text=True)
    page = client.get("/manuscripts").get_data(as_text=True)
    assert "assembles notes and whole threads into one text" in page
