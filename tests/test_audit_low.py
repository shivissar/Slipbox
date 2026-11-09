"""5.2 audit, lower-priority fixes: ⇧⌘N writes nothing until you type (19),
unreadable note files are reported (20), an unreadable settings file is
restored from its backup and kept aside (25), and the trash and the
history of deleted notes can be emptied (28)."""
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

_tmp = Path(tempfile.mkdtemp(prefix="zaudit4-"))
os.environ.setdefault("ZETTEL_SETTINGS_PATH", str(_tmp / "settings.json"))
os.environ.setdefault("ZETTEL_NOTES_DIR", str(_tmp / "notes"))
os.environ.setdefault("ZETTEL_DB_PATH", str(_tmp / "zettel.db"))
os.environ["ZETTEL_VAULT_KDF"] = "light"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from Slipbox import db, embed, flask_app, history, notes, settings, watcher  # noqa: E402

app = flask_app.app


@pytest.fixture
def client(tmp_path, monkeypatch):
    d = tmp_path / "notes"
    d.mkdir()
    monkeypatch.setattr(notes, "NOTES_DIR", d)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "zettel.db")
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(embed, "embed_note_async", lambda *a, **k: None)
    settings.PROBLEM.clear()
    settings.RECOVERED.clear()
    watcher.PROBLEMS.clear()
    db.init_db()
    settings.save({"welcomed": True, "tour_seen": True, "update_check": False, "html_safety_applied": True})
    app.testing = True
    yield app.test_client()
    settings.PROBLEM.clear()
    settings.RECOVERED.clear()
    watcher.PROBLEMS.clear()


def _md():
    return sorted(p.name for p in notes.NOTES_DIR.glob("*.md"))


# 19 ------------------------------------------------------------------------

def test_new_journal_entry_writes_nothing_until_saved(client):
    before = _md()
    html = client.get("/journal/new").get_data(as_text=True)
    assert _md() == before
    assert 'data-new="1"' in html and 'value="journal" checked' in html and " autofocus" in html
    title = html.split('id="title-input"')[1].split('value="')[1].split('"')[0]
    assert len(title) == 16 and title[4] == "-"            # "2026-11-08 14:32"
    r = client.post("/new", data={"title": title, "body": "Dear diary", "kind": "journal",
                                  "_autosave": "1", "_draft": "j1", "_seq": "1"})
    assert r.status_code == 200 and r.get_json()["ok"]
    assert len(_md()) == len(before) + 1
    n = notes.parse_file(notes.NOTES_DIR / [m for m in _md() if m not in before][0])
    assert n.kind == "journal" and n.body == "Dear diary"


# 20 ------------------------------------------------------------------------

def test_unreadable_note_shown_in_sidebar_and_settings(client):
    (notes.NOTES_DIR / "20260101000000-broken.md").write_text("---\ntitle: [oops\n---\nx\n")
    watcher.full_resync()
    html = client.get("/").get_data(as_text=True)
    assert "1 note can’t be read" in html
    html = client.get("/settings").get_data(as_text=True)
    assert 'id="unreadable"' in html and "20260101000000-broken.md" in html


# 25 ------------------------------------------------------------------------

def test_unreadable_settings_restored_from_backup_and_kept(client, tmp_path):
    settings.save({"ai_chat_api_key": "sk-test-123", "notes_dir": "/Users/x/Zetts"})
    bak = tmp_path / "settings.json.bak"
    assert json.loads(bak.read_text())["ai_chat_api_key"] == "sk-test-123"
    (tmp_path / "settings.json").write_text('{"ai_chat_api_key": "sk-test-123",,,')   # damaged
    assert settings.get("ai_chat_api_key") == "sk-test-123"                     # from the backup
    assert settings.problem_notice()["from_backup"]
    html = client.get("/settings").get_data(as_text=True)
    assert 'id="settings-problem"' in html and "settings.json.bak" in html
    settings.save({"theme_mode": "dark"})                                       # any save
    kept = list(tmp_path.glob("settings.json.unreadable-*"))
    assert len(kept) == 1 and ",,," in kept[0].read_text()
    good = json.loads((tmp_path / "settings.json").read_text())
    assert good["ai_chat_api_key"] == "sk-test-123" and good["notes_dir"] == "/Users/x/Zetts" and good["theme_mode"] == "dark"
    notice = settings.problem_notice()
    assert notice["kept"] == kept[0].name
    assert kept[0].name in client.get("/settings").get_data(as_text=True)


def test_unreadable_settings_without_backup_uses_defaults(client, tmp_path):
    (tmp_path / "settings.json.bak").unlink()
    (tmp_path / "settings.json").write_text("not json")
    assert settings.get("autosave") is True and not settings.problem_notice()["from_backup"]
    settings.save({"autosave": False})
    assert list(tmp_path.glob("settings.json.unreadable-*"))


# 28 ------------------------------------------------------------------------

def test_empty_trash_and_prune_history(client):
    keep = notes.create_note("Keep", body="v1")
    gone = notes.create_note("Gone", body="v1")
    lost = notes.create_note("Deleted in Finder", body="v1")
    for n in (keep, gone, lost):
        history.snapshot(n.path, reason="edit", force=True)
        db.upsert_note(n)
    client.post(f"/note/{gone.id}/delete")
    lost.path.unlink()
    db.delete_note(lost.id)
    hk = flask_app._housekeeping()
    assert hk["trash"] == 1 and hk["orphans"] == 1
    html = client.get("/settings").get_data(as_text=True)
    assert 'id="housekeeping"' in html and "Empty trash" in html and "Delete their history" in html
    client.post("/history/prune")
    assert history.ids_with_history() == sorted([keep.id, gone.id])
    client.post("/trash/empty")
    assert notes.trashed() == [] and history.ids_with_history() == [keep.id]
    assert keep.path.exists()
    html = client.get("/settings#housekeeping").get_data(as_text=True)
    assert "It’s empty." in html


# 21 ------------------------------------------------------------------------

def test_each_thread_renders_with_its_own_converter(client):
    import threading
    bodies = [f"Note {i} says something[^a].\n\n[^a]: Footnote of note {i}." for i in range(8)]
    out, errors = {}, []

    def render(i):
        try:
            for _ in range(20):
                html = str(flask_app._render_markdown_html(bodies[i]))
                assert f"Footnote of note {i}" in html
                assert not any(f"Footnote of note {j}" in html for j in range(8) if j != i)
            out[i] = html
        except Exception as e:  # pragma: no cover - reported below
            errors.append(e)
    threads = [threading.Thread(target=render, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and len(out) == 8
    assert flask_app._md() is flask_app._md()


# 23 ------------------------------------------------------------------------

def test_outside_edit_kept_in_history_before_in_app_save(client):
    n = notes.create_note("Fieldnote", body="first")
    db.upsert_note(n)
    history.snapshot(n.path, force=True)                       # a recent version exists
    client.get(f"/note/{n.id}/edit")                           # editor opens it
    text = n.path.read_text().replace("first", "EDITED IN OBSIDIAN")
    n.path.write_text(text)
    os.utime(n.path, (n.path.stat().st_atime, n.path.stat().st_mtime + 2))
    r = client.post(f"/note/{n.id}/edit", data={"title": "Fieldnote", "body": "from the app",
                                                "_autosave": "1", "_draft": "d", "_seq": "1"})
    assert r.get_json()["outside_version_kept"] is True
    versions = history.list_versions(n.id)
    kept = [v for v in versions if v["name"].endswith("-external.md")]
    assert len(kept) == 1 and kept[0]["reason"] == "Before an outside change"
    kept = kept[0]
    assert "EDITED IN OBSIDIAN" in history.read_version(n.id, kept["name"]).body
    r = client.post(f"/note/{n.id}/edit", data={"title": "Fieldnote", "body": "again",
                                                "_autosave": "1", "_draft": "d", "_seq": "2"})
    assert r.get_json()["outside_version_kept"] is False          # nothing outside this time


# 26 ------------------------------------------------------------------------

def test_templates_work_in_a_read_only_notes_folder(client, monkeypatch):
    from Slipbox import note_templates
    real_mkdir = Path.mkdir

    def refuse(self, *a, **k):
        if self.name == ".templates":
            raise PermissionError(13, "Permission denied")
        return real_mkdir(self, *a, **k)
    monkeypatch.setattr(Path, "mkdir", refuse)
    assert not (notes.NOTES_DIR / ".templates").exists()
    assert {t["slug"] for t in note_templates.list_templates()} >= {"interview-memo", "fieldnote"}
    assert client.get("/new").status_code == 200
    assert client.get("/new?template=fieldnote").status_code == 200
    assert client.get("/settings").status_code == 200
    r = client.post("/templates/new", data={"name": "Mine", "kind": "research", "body": "x"})
    assert r.status_code == 302 and "Couldn" in r.headers["Location"]
    assert client.post("/templates/restore").status_code == 302


# 27 ------------------------------------------------------------------------

def test_logs_name_notes_by_id_only(client, capsys):
    bad = notes.NOTES_DIR / "20260101000000-my-secret-informant.md"
    bad.write_text("---\ntitle: [oops\n---\nx\n")
    watcher.full_resync()
    out = capsys.readouterr().out
    assert "note 20260101000000" in out and "informant" not in out
    assert notes.log_name("notes/whatever.md") == "a note file"


# Links to deleted notes ------------------------------------------------------

def test_link_to_deleted_note_opens_a_page_with_restore_and_unlink(client):
    gone = notes.create_note("Situated action", body="Suchman's point.")
    db.upsert_note(gone)
    a = notes.create_note("Draft", body=f"See [[{gone.id}]] and [[{gone.id}|her argument]].\n\n![[{gone.id}]]\n\nEnd.")
    b = notes.create_note("Memo", body=f"Also [[{gone.id}]].")
    for n in (a, b):
        db.upsert_note(n)
    client.post(f"/note/{gone.id}/delete")

    html = client.get(f"/note/{a.id}").get_data(as_text=True)
    assert 'class="wikilink broken"' in html and ">Situated action</a>" in html     # old title, not the ID
    r = client.get(f"/note/{gone.id}")
    page = r.get_data(as_text=True)
    assert r.status_code == 404 and "This note was deleted" in page and "Restore it" in page
    assert "2 notes still link to it" in page and "Draft" in page and "Memo" in page
    assert 'class="sidebar' in page or "side-link" in page                         # inside the app, not a bare page

    r = client.post(f"/note/{gone.id}/unlink")
    assert r.status_code == 302
    body = notes.parse_file(a.path).body
    assert "[[" not in body and "See Situated action and her argument." in body and "End." in body
    assert notes.parse_file(b.path).body == "Also Situated action."
    assert any(v["name"].endswith("-link.md") for v in history.list_versions(a.id))

    r = client.post(f"/note/{gone.id}/restore")
    assert r.status_code == 302 and client.get(f"/note/{gone.id}").status_code == 200
    assert notes.trashed() == []
    assert client.post(f"/note/{gone.id}/restore").status_code == 302                # nothing left: no error


def test_unknown_note_and_unknown_page_stay_inside_the_app(client):
    r = client.get("/note/20200101000000")
    page = r.get_data(as_text=True)
    assert r.status_code == 404 and "Note not found" in page and "data-history-back" in page
    r = client.get("/no/such/page")
    assert r.status_code == 404 and "Nothing here" in r.get_data(as_text=True)
    assert client.get("/api/nothing-here").get_json() == {"ok": False, "message": "Not found"}


def test_server_error_page_has_a_way_back(client, monkeypatch):
    def boom():
        raise RuntimeError("x")
    monkeypatch.setitem(app.view_functions, "stats_page", boom)
    app.testing = False
    try:
        r = client.get("/stats")
    finally:
        app.testing = True
    assert r.status_code == 500 and "Something went wrong" in r.get_data(as_text=True)


def test_citation_labels_name_all_authors():
    lab = flask_app._cite_label
    assert lab({"author": "Suchman, Lucy", "year": "1987"}) == "Suchman 1987"
    assert lab({"author": "Latour, Bruno; Woolgar, Steve", "year": "1979"}) == "Latour and Woolgar 1979"
    assert lab({"author": "Star, Susan Leigh; Bowker, Geoffrey; Neumann, Laura", "year": "1999"}) == "Star et al. 1999"
    assert lab({"author": "Bruno Latour and Steve Woolgar", "year": "1979"}) == "Latour and Woolgar 1979"
