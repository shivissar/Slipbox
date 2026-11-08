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

from Zetteling import db, embed, flask_app, history, notes, settings, watcher  # noqa: E402

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
