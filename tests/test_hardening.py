"""5.2 audit fixes: search snippets, note HTML sanitising, content-security
policy, Host checks, atomic note writes, the recovery-key file and the
one-time settings change."""
import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

_tmp = Path(tempfile.mkdtemp(prefix="zhard-"))
os.environ.setdefault("ZETTEL_SETTINGS_PATH", str(_tmp / "settings.json"))
os.environ.setdefault("ZETTEL_NOTES_DIR", str(_tmp / "notes"))
os.environ.setdefault("ZETTEL_DB_PATH", str(_tmp / "zettel.db"))
os.environ["ZETTEL_VAULT_KDF"] = "light"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from Verweis import db, flask_app, notes, settings  # noqa: E402

app = flask_app.app


@pytest.fixture
def client(tmp_path, monkeypatch):
    d = tmp_path / "notes"
    d.mkdir()
    monkeypatch.setattr(notes, "NOTES_DIR", d)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "zettel.db")
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    db.init_db()
    settings.save({"welcomed": True, "tour_seen": True, "update_check": False, "html_safety_applied": True})
    app.testing = True
    return app.test_client()


def _note(body, title="Probe"):
    n = notes.create_note(title, body=body)
    db.upsert_note(n)
    return n


def test_search_snippets_are_escaped_except_marks(client):
    _note('zebrafish <img src=x onerror="alert(1)"> zebrafish')
    for headers in ({}, {"HX-Request": "true"}):
        html = client.get("/search?q=zebrafish", headers=headers).get_data(as_text=True)
        assert "<img src=x" not in html and "&lt;img src=x" in html
        assert "<mark>zebrafish</mark>" in html


def test_marks_filter():
    out = str(flask_app._marks_only('a <mark>b</mark> <script>x</script> <mark onclick="y">'))
    assert out.startswith("a <mark>b</mark> &lt;script&gt;")
    assert "<mark onclick" not in out


def test_sanitiser_keeps_markdown_and_strips_scripts(client):
    body = ("Text[^1] and [[20990101000000]] and [a passage]{.trust}.\n\n"
            "| a | b |\n|:--|--:|\n| 1 | 2 |\n\n"
            '<script>alert(1)</script> <img src="x.png" onerror="alert(2)"> '
            '<a href="javascript:alert(3)">js</a> <span id="palette">clobber</span>\n\n'
            "[^1]: A footnote.\n")
    n = _note(body)
    html = client.get(f"/note/{n.id}").get_data(as_text=True)
    rendered = html.split('<div class="rendered">', 1)[1].split("</article>", 1)[0]
    assert "<script>" not in rendered and "alert(1)" not in rendered
    assert "onerror" not in rendered and "javascript:" not in rendered
    assert 'id="palette"' not in rendered
    assert 'id="fnref:1"' in rendered and 'id="fn:1"' in rendered and 'href="#fn:1"' in rendered
    assert 'align="left"' in rendered and 'align="right"' in rendered
    assert 'class="qcode"' in rendered and 'href="/codes/trust"' in rendered
    assert 'class="wikilink broken"' in rendered
    assert '<img src="x.png">' in rendered


def test_sanitising_can_be_turned_off(client):
    settings.save({"sanitize_note_html": False})
    n = _note('<span id="mine">kept</span>')
    assert 'id="mine"' in client.get(f"/note/{n.id}").get_data(as_text=True)


def test_csp_with_nonce_on_pages(client):
    r = client.get("/help")
    csp = r.headers["Content-Security-Policy"]
    nonce = re.search(r"'nonce-([^']+)'", csp).group(1)
    html = r.get_data(as_text=True)
    scripts = re.findall(r"<script([^>]*)>", html)
    inline = [a for a in scripts if "src=" not in a]
    assert inline and all(f'nonce="{nonce}"' in a for a in inline)
    assert not re.search(r"\son(?:submit|click|load)=", html)
    assert "Content-Security-Policy" not in client.get("/api/vault/status").headers
    assert client.get("/help").headers["Content-Security-Policy"] != csp  # fresh nonce each page


def test_templates_have_no_inline_handlers():
    tdir = Path(flask_app.__file__).parent / "templates"
    for p in tdir.glob("*.html"):
        text = p.read_text(encoding="utf-8")
        assert not re.search(r"\son[a-z]+=\"", text), p.name
        for tag in re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", text):
            assert "csp_nonce" in tag, p.name


def test_only_local_hosts_are_answered(client):
    assert client.get("/help", headers={"Host": "127.0.0.1:5005"}).status_code == 200
    assert client.get("/help", headers={"Host": "evil.example:5005"}).status_code == 400
    r = client.post("/api/tour/seen", headers={"Host": "127.0.0.1:5005", "Origin": "http://evil.example:5005",
                                               "X-Forwarded-Host": "evil.example:5005"})
    assert r.status_code == 403  # X-Forwarded-Host no longer rewrites the host


def test_note_write_is_all_or_nothing(client, monkeypatch):
    n = _note("first version")
    before = n.path.read_text(encoding="utf-8")
    n.body = "second version " * 1000

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(notes.os, "replace", boom)
    with pytest.raises(OSError):
        notes.write_note(n)
    assert n.path.read_text(encoding="utf-8") == before
    assert not [p for p in n.path.parent.iterdir() if p.name.endswith(".tmp")]


def test_note_write_keeps_permissions(client):
    n = _note("x")
    os.chmod(n.path, 0o600)
    n.body = "y"
    notes.write_note(n)
    assert (os.stat(n.path).st_mode & 0o777) == 0o600


def test_recovery_key_falls_back_then_reports(client, tmp_path, monkeypatch):
    code = "PO7Q-OVGV-YA52-6ASA-4GXU-Q76T-OU2B-L6AL-NQ4B-3MKA-X3HI-NGAP-2POQ"
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(flask_app, "_recovery_save_dirs",
                        lambda: [(tmp_path / "refused", "your Downloads folder"), (home, "your home folder")])
    monkeypatch.setattr(flask_app.platform, "system", lambda: "Linux")
    r = client.post("/vault/recovery/save", data={"code": code}, headers={"Accept": "application/json"})
    d = r.get_json()
    assert d["ok"] and "your home folder" in d["message"]
    assert code in next(home.glob("*.txt")).read_text()
    monkeypatch.setattr(flask_app, "_recovery_save_dirs", lambda: [(tmp_path / "refused", "your Downloads folder")])
    d = client.post("/vault/recovery/save", data={"code": code}, headers={"Accept": "application/json"}).get_json()
    assert d["ok"] is False and "Copy" in d["message"]
    html = client.post("/vault/recovery/save", data={"code": code}).get_data(as_text=True)
    assert code in html and "Copy instead" in html


def test_sanitising_switched_on_once(client):
    settings.save({"sanitize_note_html": False, "html_safety_applied": False})
    flask_app._apply_once_settings()
    assert settings.get("sanitize_note_html") is True
    settings.save({"sanitize_note_html": False})
    flask_app._apply_once_settings()
    assert settings.get("sanitize_note_html") is False
