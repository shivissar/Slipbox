"""Tests for the 5.2 update check: version parsing, what is trusted from
GitHub's answer, throttling, errors, skipping, and that only this
project's GitHub pages can be opened."""
import io
import json
import os
import sys
import tempfile
import urllib.error
from pathlib import Path

import pytest

_tmp = Path(tempfile.mkdtemp(prefix="zupd-"))
os.environ.setdefault("ZETTEL_SETTINGS_PATH", str(_tmp / "settings.json"))
os.environ.setdefault("ZETTEL_NOTES_DIR", str(_tmp / "notes"))
os.environ.setdefault("ZETTEL_DB_PATH", str(_tmp / "zettel.db"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from Slipbox import settings, updates  # noqa: E402

RELEASE = {
    "tag_name": "v5.3.0",
    "name": "Slipbox 5.3.0",
    "published_at": "2026-12-01T10:00:00Z",
    "body": "Fixes.\r\n\r\n<script>alert(1)</script>",
    "html_url": "https://github.com/shivissar/Slipbox/releases/tag/v5.3.0",
    "assets": [
        {"name": "pandoc-3.1.13.tar.gz", "browser_download_url": "https://github.com/shivissar/Slipbox/releases/download/v5.3.0/pandoc-3.1.13.tar.gz"},
        {"name": "Slipbox-5.3.0.dmg", "browser_download_url": "https://github.com/shivissar/Slipbox/releases/download/v5.3.0/Slipbox-5.3.0.dmg"},
    ],
}


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    calls = []

    def answer(payload=RELEASE, error=None):
        def fake_urlopen(req, timeout=None, context=None):
            calls.append(req)
            if error:
                raise error
            return _Resp(json.dumps(payload).encode())
        monkeypatch.setattr(updates.urllib.request, "urlopen", fake_urlopen)

    answer()
    yield answer, calls


def test_parse_version():
    assert updates.parse_version("v5.2.0") == (5, 2, 0)
    assert updates.parse_version("5.10") == (5, 10, 0)
    assert updates.parse_version("v5.3.0-beta.1") is None
    assert updates.parse_version("latest") is None
    assert updates.parse_version("v5.10.0") > updates.parse_version("v5.9.9")


def test_only_this_projects_github_urls_are_trusted():
    ok = "https://github.com/shivissar/Slipbox/releases/tag/v5.3.0"
    assert updates.safe_url(ok) == ok
    assert updates.safe_url("https://github.com/someone/Slipbox/releases") is None
    assert updates.safe_url("https://github.com.evil.example/shivissar/Slipbox/") is None
    assert updates.safe_url("http://github.com/shivissar/Slipbox/releases") is None
    assert updates.safe_url("https://github.com/shivissar/Slipbox/x\" onclick=") is None
    assert updates.safe_url(None) is None


def test_check_finds_newer_release(fresh):
    _, calls = fresh
    st = updates.check("5.2.0", force=True)
    assert st["newer"] and st["notice"] and not st["error"]
    assert st["latest"]["version"] == "5.3.0"
    assert st["latest"]["dmg_url"].endswith("Slipbox-5.3.0.dmg")
    assert st["latest"]["notes"].startswith("Fixes.\n\n")  # stored as text; the page escapes it
    req = calls[0]
    assert req.full_url == updates.API_URL
    assert req.get_header("User-agent") == "Slipbox/5.2.0"
    assert not req.get_header("Authorization")


def test_same_or_older_release_is_not_offered(fresh):
    assert not updates.check("5.3.0", force=True)["newer"]
    assert not updates.check("5.4.1", force=True)["newer"]


def test_untrusted_links_in_the_answer_are_dropped(fresh):
    answer, _ = fresh
    bad = dict(RELEASE, html_url="https://evil.example/slipbox",
               assets=[{"name": "Slipbox-5.3.0.dmg", "browser_download_url": "https://evil.example/z.dmg"}])
    answer(bad)
    st = updates.check("5.2.0", force=True)
    assert st["latest"]["url"] == updates.RELEASES_PAGE
    assert st["latest"]["dmg_url"] == ""


def test_checks_are_throttled_and_can_be_turned_off(fresh):
    _, calls = fresh
    updates.check("5.2.0")
    updates.check("5.2.0")
    assert len(calls) == 1  # once a day
    updates.check("5.2.0", force=True)
    assert len(calls) == 2  # Check now always asks
    settings.save({"update_check": False, "update_next_check": 0})
    st = updates.check("5.2.0")
    assert len(calls) == 2 and not st["notice"]
    assert not updates.check_in_background("5.2.0")


def test_errors_are_explained_and_retried_sooner(fresh):
    answer, _ = fresh
    answer(error=urllib.error.HTTPError(updates.API_URL, 404, "Not Found", {}, None))
    st = updates.check("5.2.0", force=True)
    assert "no published release" in st["error"]
    answer(error=urllib.error.URLError("offline"))
    st = updates.check("5.2.0", force=True)
    assert "online" in st["error"]
    wait = settings.load()["update_next_check"] - updates._now()
    assert 0 < wait <= updates.RETRY + 5
    answer(dict(RELEASE, tag_name="nightly"))
    assert "version number" in updates.check("5.2.0", force=True)["error"]


def test_skip_hides_the_notice_until_a_later_version(fresh):
    answer, _ = fresh
    updates.check("5.2.0", force=True)
    settings.save({"update_skip": "5.3.0"})
    st = updates.status("5.2.0")
    assert st["newer"] and st["skipped"] and not st["notice"]
    answer(dict(RELEASE, tag_name="v5.3.1"))
    assert updates.check("5.2.0", force=True)["notice"]


def test_open_external_refuses_other_sites(monkeypatch):
    opened = []
    monkeypatch.setattr(updates.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(updates.subprocess, "run", lambda args, check=False: opened.append(args))
    assert updates.open_external("https://github.com/shivissar/Slipbox/releases")
    assert not updates.open_external("https://evil.example/")
    assert not updates.open_external("file:///etc/passwd")
    assert opened == [["open", "https://github.com/shivissar/Slipbox/releases"]]


def test_settings_writes_are_whole(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "settings.json")
    settings.save({"autosave": False})
    settings.save({"update_latest": {"version": "5.3.0"}})
    s = json.loads((tmp_path / "settings.json").read_text())
    assert s["autosave"] is False and s["update_latest"]["version"] == "5.3.0"
    assert not (tmp_path / "settings.json.tmp").exists()


def test_outside_links():
    ok = ["https://en.wikipedia.org/wiki/A._Aneesh", "http://example.com/a?b=c#d", "mailto:someone@example.com"]
    for u in ok:
        assert updates.outside_link(u) == u
    for u in ["javascript:alert(1)", "file:///etc/passwd", "zotero://select/items/1", "https://", "mailto:",
              "/note/1", "https://example.com/a b", "https://example.com/\nx", "x" * 5000, None, 3]:
        assert updates.outside_link(u) is None, u


def test_open_in_browser_uses_open_on_mac(monkeypatch):
    opened = []
    monkeypatch.setattr(updates.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(updates.subprocess, "run", lambda args, check=False: opened.append(args))
    assert updates.open_in_browser("https://www.shivissar.com")
    assert not updates.open_in_browser("javascript:alert(1)")
    assert opened == [["open", "https://www.shivissar.com"]]
