"""5.2 audit fixes, third batch: start-up indexing skips unchanged notes
and reports unreadable ones (9, 20), the codebook CSV is saved to the
export folder (12) with formula-safe cells (22), and outbound HTTPS uses
certifi's certificates (13)."""
import csv
import io
import os
import sys
import tempfile
from pathlib import Path

import pytest

_tmp = Path(tempfile.mkdtemp(prefix="zaudit3-"))
os.environ.setdefault("ZETTEL_SETTINGS_PATH", str(_tmp / "settings.json"))
os.environ.setdefault("ZETTEL_NOTES_DIR", str(_tmp / "notes"))
os.environ.setdefault("ZETTEL_DB_PATH", str(_tmp / "zettel.db"))
os.environ["ZETTEL_VAULT_KDF"] = "light"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from Verweis import ai, codebook, db, embed, flask_app, net, notes, settings, updates, watcher  # noqa: E402

app = flask_app.app


@pytest.fixture
def client(tmp_path, monkeypatch):
    d = tmp_path / "notes"
    d.mkdir()
    monkeypatch.setattr(notes, "NOTES_DIR", d)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "zettel.db")
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    monkeypatch.setattr(embed, "embed_note_async", lambda *a, **k: None)
    db.init_db()
    settings.save({"welcomed": True, "tour_seen": True, "update_check": False,
                   "html_safety_applied": True, "export_dir": str(tmp_path / "exports")})
    app.testing = True
    return app.test_client()


# 9 -------------------------------------------------------------------------

def test_resync_skips_unchanged_notes(client, monkeypatch):
    a = notes.create_note("Alpha", body="one")
    b = notes.create_note("Beta", body="two")
    real, parsed = notes.parse_file, []

    def counting(path):
        parsed.append(path.name)
        return real(path)
    monkeypatch.setattr(notes, "parse_file", counting)
    watcher.full_resync()
    assert sorted(parsed) == sorted([a.path.name, b.path.name])   # first run reads all
    parsed.clear()
    watcher.full_resync()
    assert parsed == []                                         # nothing changed
    os.utime(b.path, (b.path.stat().st_atime, b.path.stat().st_mtime + 5))
    watcher.full_resync()
    assert parsed == [b.path.name]                              # only the changed one
    parsed.clear()
    db.set_embed_meta("index_version", "5.1")
    watcher.full_resync()
    assert len(parsed) == 2                                     # older index: read all
    a.path.unlink()
    watcher.full_resync()
    assert db.get_note_meta(a.id) is None and db.get_note_meta(b.id)


def test_unreadable_note_is_reported(client):
    good = notes.create_note("Fine", body="ok")
    bad = notes.NOTES_DIR / "20260101000000-broken.md"
    bad.write_text("---\ntitle: [unclosed\ntags: {\n---\nBody\n")
    watcher.full_resync()
    assert bad.name in watcher.PROBLEMS and good.path.name not in watcher.PROBLEMS
    bad.unlink()
    watcher.full_resync()
    assert watcher.PROBLEMS == {}


# 12, 22 ---------------------------------------------------------------------

def test_codebook_csv_saved_to_export_folder(client, tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr(flask_app, "_reveal", lambda p: (shown.append(p), (True, "ok"))[1])
    n = notes.create_note("Coded", body="Pilots said [@smith2020 argues trust]{.trust} and [=SUM(1,2)]{.trust}.")
    db.upsert_note(n)
    r = client.post("/codes/export")
    assert r.status_code == 302 and "/codes" in r.headers["Location"]
    files = list((tmp_path / "exports").glob("*-verweis-codebook.csv"))
    assert len(files) == 1 and shown == [files[0]]
    rows = list(csv.reader(io.StringIO(files[0].read_text())))
    excerpts = [row[-1] for row in rows[1:]]
    assert "'@smith2020 argues trust" in excerpts and "'=SUM(1,2)" in excerpts
    html = client.get(r.headers["Location"]).get_data(as_text=True)
    assert files[0].name in html and "shown in Finder" in html
    r = client.post("/codes/export", data={"code": "trust"})
    assert "/codes/trust" in r.headers["Location"]
    assert len(list((tmp_path / "exports").glob("*-verweis-trust.csv"))) == 1
    for page in ("/codes", "/codes/trust"):
        assert 'action="/codes/export"' in client.get(page).get_data(as_text=True)


def test_formula_cells():
    assert [codebook._cell(v) for v in ("=1+1", "+44", "-x", "@a", "plain", "", None, 3)] == \
        ["'=1+1", "'+44", "'-x", "'@a", "plain", "", "", "3"]


# 13 ------------------------------------------------------------------------

def test_https_calls_use_certifi(monkeypatch):
    import certifi
    seen = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"data": [{"embedding": [0.1, 0.2]}], "embedding": [0.1, 0.2]}'

    def fake(req, timeout=None, context=None):
        seen["context"] = context
        return Resp()
    monkeypatch.setattr(ai, "_urlopen", fake)
    with ai.urlopen(ai.Request("https://api.voyageai.com/v1/embeddings"), timeout=5):
        pass
    ctx = seen["context"]
    assert ctx is net.ssl_context() and updates._ssl_context() is ctx
    assert ctx.cert_store_stats()["x509_ca"] > 50 and certifi.where()
