"""5.2 audit fixes, second batch: locked notes that are open say so (4),
always-locked kinds are swept (5), vaults move to the 64 MiB setting (8),
the export warning counts embedded locked notes (16) and Interrogate
writes out embedded notes but never locked ones (17)."""
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

_tmp = Path(tempfile.mkdtemp(prefix="zaudit2-"))
os.environ.setdefault("ZETTEL_SETTINGS_PATH", str(_tmp / "settings.json"))
os.environ.setdefault("ZETTEL_NOTES_DIR", str(_tmp / "notes"))
os.environ.setdefault("ZETTEL_DB_PATH", str(_tmp / "zettel.db"))
os.environ["ZETTEL_VAULT_KDF"] = "light"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from Slipbox import chat, db, embed, export, flask_app, notes, settings, vault  # noqa: E402

app = flask_app.app
_REAL_EMBED_ASYNC = embed.embed_note_async
PW = "correct horse battery staple"
CHEAP = {"name": "argon2id", "memory_kib": 2 ** 13, "iterations": 1, "lanes": 1}


@pytest.fixture
def client(tmp_path, monkeypatch):
    d = tmp_path / "notes"
    d.mkdir()
    monkeypatch.setattr(notes, "NOTES_DIR", d)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "zettel.db")
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(embed, "embed_note", lambda *a, **k: None)
    monkeypatch.setattr(embed, "embed_note_async", lambda *a, **k: None, raising=False)
    db.init_db()
    settings.save({"welcomed": True, "tour_seen": True, "update_check": False, "html_safety_applied": True})
    vault.lock()
    flask_app._SWEEP.update(at=-1e9, msg="")
    app.testing = True
    yield app.test_client()
    vault.lock()


def _note(title, body="", **kw):
    n = notes.create_note(title, body=body, **kw)
    db.upsert_note(n)
    return n


# 4 -------------------------------------------------------------------------

def test_open_locked_note_says_it_is_open(client):
    vault.setup(PW, kdf=vault.ARGON2_LIGHT)
    n = _note("Field diary", body="Met the drone club in Pune.")
    r = client.post(f"/note/{n.id}/lock")
    assert r.status_code == 302 and "locked=1" in r.headers["Location"]
    html = client.get(f"/note/{n.id}?locked=1").get_data(as_text=True)
    assert "Locked · open" in html and "lock-flash" in html
    assert "Locked notes are open" in html            # sidebar button
    assert "drone club" not in n.path.read_text()
    client.post("/vault/lock", data={"next": f"/note/{n.id}"})
    html = client.get(f"/note/{n.id}").get_data(as_text=True)
    assert "Locked · closed" in html and "drone club" not in html
    assert "Locked notes are open" not in html


# 5 -------------------------------------------------------------------------

def test_always_locked_kind_is_swept_when_open(client):
    vault.setup(PW, kdf=vault.ARGON2_LIGHT)
    n = _note("Arrived in plain text", body="Synced from the other Mac.", kind="journal")
    settings.save({"locked_kinds": ["journal"]})
    assert flask_app._pending_kind_locks() == [n.id]
    html = client.get("/").get_data(as_text=True)
    assert "Synced from" not in n.path.read_text()
    assert notes.parse_file(n.path).locked
    assert 'data-flash="Locked 1 note' in html
    assert flask_app._pending_kind_locks() == []


def test_sweep_waits_for_the_password_and_spares_removed_locks(client):
    vault.setup(PW, kdf=vault.ARGON2_LIGHT)
    vault.lock()
    n = _note("Plain", body="still plain", kind="journal")
    settings.save({"locked_kinds": ["journal"]})
    client.get("/")
    assert "still plain" in n.path.read_text()          # closed: nothing to do
    client.post("/unlock", data={"password": PW})       # unlocking sweeps
    assert "still plain" not in n.path.read_text()
    client.post(f"/note/{n.id}/remove-lock")            # deliberate
    assert "still plain" in n.path.read_text()
    flask_app._SWEEP["at"] = -1e9
    client.get("/")
    assert "still plain" in n.path.read_text()          # not locked again
    client.post(f"/note/{n.id}/lock")
    assert n.id not in (settings.get("lock_exempt") or [])


# 8 -------------------------------------------------------------------------

def test_unlock_moves_vault_to_standard_setting(client):
    assert vault.ARGON2_DEFAULT == vault.ARGON2_LIGHT
    vault.setup(PW, kdf=CHEAP)
    n = notes.create_note("Interview 7", body="kept across the move", locked=True)
    vault.lock()
    vault.unlock(PW)
    header = json.loads((notes.NOTES_DIR / vault.KEYFILE_NAME).read_text())
    for k in ("memory_kib", "iterations", "lanes"):
        assert header["kdf"][k] == vault.ARGON2_DEFAULT[k]
    assert CHEAP["memory_kib"] != header["kdf"]["memory_kib"]
    carried = vault._parse_armour(n.path.read_text())["vault"]["kdf"]
    assert carried["memory_kib"] == vault.ARGON2_DEFAULT["memory_kib"]
    vault.lock()
    vault.unlock(PW)
    assert notes.parse_file(n.path).body == "kept across the move"
    with pytest.raises(vault.WrongPassword):
        vault.lock()
        vault.unlock("not the password at all")


# 16 ------------------------------------------------------------------------

def test_export_warning_counts_embedded_locked_notes(client):
    vault.setup(PW, kdf=vault.ARGON2_LIGHT)
    inner = notes.create_note("Consent forms", body="names here", locked=True)
    db.upsert_note(inner)
    outer = _note("Chapter 2", body=f"Intro.\n\n![[{inner.id}]]\n")
    assert export.locked_included([outer]) == ["Consent forms"]
    html = client.get(f"/export/note/{outer.id}").get_data(as_text=True)
    assert "This export includes 1 locked note" in html and "Consent forms" in html


# 17 ------------------------------------------------------------------------

def test_interrogate_writes_out_embeds_but_never_locked_text(client):
    vault.setup(PW, kdf=vault.ARGON2_LIGHT)
    plain = _note("Drone rules", body="DGCA permits are required.")
    secret = notes.create_note("Informant", body="real name: R.", locked=True)
    db.upsert_note(secret)
    outer = _note("Draft", body=f"See [[{plain.id}]].\n\n![[{plain.id}]]\n\n![[{secret.id}]]\n")
    msg = chat.build_note_context_message(outer)
    assert "See Drone rules." in msg
    assert "## Drone rules" in msg and "DGCA permits" in msg
    assert "real name" not in msg and "A locked note is embedded here" in msg
    assert "![[" not in msg


# 10 ------------------------------------------------------------------------

def test_embedding_runs_in_background_and_never_for_locked(client, monkeypatch):
    import threading
    seen, gate = [], threading.Event()

    def slow_embed(note):
        gate.wait(5)
        seen.append((note.id, note.body))
        db.store_embedding(note.id, [0.1, 0.2, 0.3], content_hash="h", provider_model="m")
        return True
    monkeypatch.setattr(embed, "embed_note", slow_embed)
    monkeypatch.setattr(embed, "embed_note_async", _REAL_EMBED_ASYNC)  # the fixture stubs it
    vault.setup(PW, kdf=vault.ARGON2_LIGHT)
    n = _note("Queued", body="first")
    r = client.post(f"/note/{n.id}/edit", data={"title": "Queued", "body": "second", "tags": ""})
    assert r.status_code == 302                       # returned before embedding
    assert seen == []
    gate.set()
    assert embed.wait_for_background(5)
    assert seen == [(n.id, "second")]                 # what was saved, read again

    seen.clear(); gate.clear()
    m = _note("Will be locked", body="text")
    embed.embed_note_async(m)
    client.post(f"/note/{m.id}/lock")                 # locked while queued/working
    gate.set()
    assert embed.wait_for_background(5)
    assert db.get_embedding_hash(m.id) is None
