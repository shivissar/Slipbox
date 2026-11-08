"""Tests for locked-note encryption (vault.py). Uses RFC 9106's second
recommended Argon2id setting so the suite runs quickly."""
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

_tmp = Path(tempfile.mkdtemp(prefix="zvault-"))
os.environ.setdefault("ZETTEL_SETTINGS_PATH", str(_tmp / "settings.json"))
os.environ.setdefault("ZETTEL_NOTES_DIR", str(_tmp / "notes"))
os.environ.setdefault("ZETTEL_DB_PATH", str(_tmp / "zettel.db"))
os.environ["ZETTEL_VAULT_KDF"] = "light"  # RFC 9106 second recommended, for speed
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from Verweis import notes, vault  # noqa: E402

PW = "correct horse battery staple"


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    d = tmp_path / "notes"
    d.mkdir()
    monkeypatch.setattr(notes, "NOTES_DIR", d)
    vault.lock()
    yield d
    vault.lock()


def test_setup_unlock_roundtrip(fresh):
    code = vault.setup(PW, kdf=vault.ARGON2_LIGHT)
    assert len(code.replace("-", "")) == 52
    header = json.loads((fresh / vault.KEYFILE_NAME).read_text())
    assert PW not in json.dumps(header)
    assert header["kdf"]["name"] == "argon2id"
    arm = vault.encrypt_body("20260101000000", "secret fieldnote")
    assert "secret" not in arm and arm.startswith(vault.BEGIN)
    vault.lock()
    with pytest.raises(vault.VaultLocked):
        vault.decrypt_body("20260101000000", arm)
    vault.unlock(PW)
    assert vault.decrypt_body("20260101000000", arm) == "secret fieldnote"


def test_wrong_password(fresh):
    vault.setup(PW, kdf=vault.ARGON2_LIGHT)
    vault.lock()
    with pytest.raises(vault.WrongPassword):
        vault.unlock("not the password at all")
    assert not vault.is_unlocked()


def test_short_password_refused(fresh):
    with pytest.raises(vault.VaultError):
        vault.setup("short", kdf=vault.ARGON2_LIGHT)


def test_fresh_salt_and_nonce_each_time(fresh):
    vault.setup(PW, kdf=vault.ARGON2_LIGHT)
    a = vault.encrypt_body("20260101000000", "same text")
    b = vault.encrypt_body("20260101000000", "same text")
    assert a != b


def test_tamper_detected(fresh):
    vault.setup(PW, kdf=vault.ARGON2_LIGHT)
    arm = vault.encrypt_body("20260101000000", "integrity matters")
    payload = vault._parse_armour(arm)
    ct = bytearray(vault._unb64(payload["ct"]))
    ct[0] ^= 1
    payload["ct"] = vault._b64(bytes(ct))
    with pytest.raises(vault.Damaged):
        vault.decrypt_body("20260101000000", vault._armour(payload))


def test_body_bound_to_note_id(fresh):
    vault.setup(PW, kdf=vault.ARGON2_LIGHT)
    arm = vault.encrypt_body("20260101000000", "belongs to one note")
    with pytest.raises(vault.Damaged):
        vault.decrypt_body("20260101000001", arm)


def test_recovery_and_password_change(fresh):
    code = vault.setup(PW, kdf=vault.ARGON2_LIGHT)
    (fresh / "20260101000000-x.md").write_text("---\nlocked: true\n---\n" + vault.encrypt_body("20260101000000", "kept"))
    vault.lock()
    with pytest.raises(vault.WrongPassword):
        vault.recover("AAAA-" * 12 + "AAAA", "a brand new passphrase")
    vault.recover(code.lower(), "a brand new passphrase")
    vault.lock()
    with pytest.raises(vault.WrongPassword):
        vault.unlock(PW)
    vault.unlock("a brand new passphrase")
    vault.change_password("a brand new passphrase", "third passphrase here")
    vault.lock()
    vault.unlock("third passphrase here")
    text = (fresh / "20260101000000-x.md").read_text()
    assert vault.decrypt_body("20260101000000", text) == "kept"


def test_keyfile_lost_header_recovered_from_notes(fresh):
    from Verweis import db
    db.init_db()
    vault.setup(PW, kdf=vault.ARGON2_LIGHT)
    (fresh / "20260101000000-x.md").write_text("---\nlocked: true\n---\n" + vault.encrypt_body("20260101000000", "survives"))
    vault.lock()
    db.upsert_note(notes.parse_file(fresh / "20260101000000-x.md"))
    (fresh / vault.KEYFILE_NAME).unlink()
    assert vault.is_set_up()
    vault.unlock(PW)
    assert (fresh / vault.KEYFILE_NAME).exists()
    text = (fresh / "20260101000000-x.md").read_text()
    assert vault.decrypt_body("20260101000000", text) == "survives"


def test_note_file_roundtrip_and_metadata_only_write(fresh):
    vault.setup(PW, kdf=vault.ARGON2_LIGHT)
    n = notes.create_note("Interview 4", body="Participant said X.", kind="research", locked=True)
    raw = n.path.read_text()
    assert "Participant" not in raw and "Interview 4" in raw and "locked: true" in raw
    vault.lock()
    closed = notes.parse_file(n.path)
    assert closed.locked and closed.unreadable and closed.body == ""
    closed.threads = [{"name": "t", "prev": None, "next": None}]
    notes.write_note(closed)          # metadata-only write while closed
    vault.unlock(PW)
    reopened = notes.parse_file(n.path)
    assert reopened.body == "Participant said X." and reopened.threads[0]["name"] == "t"


def test_write_refuses_plaintext_when_closed(fresh):
    vault.setup(PW, kdf=vault.ARGON2_LIGHT)
    n = notes.create_note("Diary", body="private", kind="journal", locked=True)
    vault.lock()
    n.body = "changed while closed"
    n.unreadable = False
    with pytest.raises(vault.VaultLocked):
        notes.write_note(n)
    assert "changed while closed" not in n.path.read_text()


def test_recovery_code_format():
    raw = os.urandom(32)
    assert vault.parse_recovery(vault.format_recovery(raw)) == raw
