"""5.2: the rename from Zetteling to Verweis. The old app-data folder moves
into the new one once; the hidden notes-folder files are renamed (and read
under their old names until then); locked notes made before the rename
still open."""
import json
import os
import sys
import tempfile
from pathlib import Path


_tmp = Path(tempfile.mkdtemp(prefix="zrename-"))
os.environ.setdefault("ZETTEL_SETTINGS_PATH", str(_tmp / "settings.json"))
os.environ.setdefault("ZETTEL_NOTES_DIR", str(_tmp / "notes"))
os.environ.setdefault("ZETTEL_DB_PATH", str(_tmp / "zettel.db"))
os.environ["ZETTEL_VAULT_KDF"] = "light"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from Verweis import codebook, migrate, notes, vault  # noqa: E402

PW = "correct horse battery staple"


def test_old_app_data_moves_once(tmp_path):
    support = tmp_path / "Application Support"
    old = support / "com.shivissar.zetteling"
    new = support / "com.shivissar.verweis"
    (old / "history" / "20260101000000").mkdir(parents=True)
    (old / "history" / "20260101000000" / "v1.md").write_text("old version")
    (old / "notes").mkdir()
    (old / "notes" / "20260101000000-a.md").write_text("---\ntitle: A\n---\nbody")
    (old / "zettel.db").write_bytes(b"db")
    (old / "settings.json").write_text(json.dumps({
        "notes_dir": str(old / "notes"), "bib_path": "/Users/x/library.bib", "ai_chat_api_key": "k"}))
    new.mkdir(parents=True)
    (new / "notes").mkdir()                                  # made by an earlier launch: empty
    assert migrate.move_old_app_data(new) == old
    assert not old.exists()
    s = json.loads((new / "settings.json").read_text())
    assert s["notes_dir"] == str(new / "notes") and s["bib_path"] == "/Users/x/library.bib" and s["ai_chat_api_key"] == "k"
    assert (new / "history" / "20260101000000" / "v1.md").read_text() == "old version"
    assert (new / "notes" / "20260101000000-a.md").exists() and (new / "zettel.db").read_bytes() == b"db"
    old.mkdir()
    (old / "settings.json").write_text("{}")
    assert migrate.move_old_app_data(new) is None             # only once: new folder has data
    assert (old / "settings.json").exists()


def test_nothing_to_move(tmp_path):
    new = tmp_path / "com.shivissar.verweis"
    new.mkdir()
    assert migrate.move_old_app_data(new) is None


def test_locked_notes_and_codebook_survive_the_rename(tmp_path, monkeypatch):
    monkeypatch.setattr(notes, "NOTES_DIR", tmp_path)
    vault.lock()
    vault.setup(PW, kdf=vault.ARGON2_LIGHT)
    n = notes.create_note("Before the rename", body="secret words", locked=True)
    assert "BEGIN ZETTELING LOCKED NOTE" in n.path.read_text()   # labels unchanged
    (tmp_path / ".verweis-vault.json").rename(tmp_path / ".zetteling-vault.json")
    (tmp_path / ".zetteling-codebook.json").write_text(json.dumps({"trust": {"definition": "d"}}))
    vault.lock()
    assert vault.keyfile_path().name == ".zetteling-vault.json"   # old name still read
    assert codebook._file().name == ".zetteling-codebook.json"
    vault.unlock(PW)
    assert notes.parse_file(n.path).body == "secret words"
    vault.lock()
    assert sorted(vault.rename_legacy_files()) == [".zetteling-codebook.json", ".zetteling-vault.json"]
    assert vault.keyfile_path().name == ".verweis-vault.json" and codebook._file().name == ".verweis-codebook.json"
    assert not (tmp_path / ".zetteling-vault.json").exists()
    vault.unlock(PW)
    assert notes.parse_file(n.path).body == "secret words"
    assert vault.rename_legacy_files() == []
    vault.lock()
