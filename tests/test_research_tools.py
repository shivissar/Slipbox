"""Tests for 5.2: coding syntax, search-filter parsing, mentions, history,
templates and manuscript export assembly."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

_tmp = Path(tempfile.mkdtemp(prefix="z52-"))
os.environ.setdefault("ZETTEL_SETTINGS_PATH", str(_tmp / "settings.json"))
os.environ.setdefault("ZETTEL_NOTES_DIR", str(_tmp / "notes"))
os.environ.setdefault("ZETTEL_DB_PATH", str(_tmp / "zettel.db"))
os.environ["ZETTEL_VAULT_KDF"] = "light"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from Zetteling import codebook, db, export, history, note_templates, notes  # noqa: E402


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    d = tmp_path / "notes"
    d.mkdir()
    monkeypatch.setattr(notes, "NOTES_DIR", d)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "zettel.db")
    db.init_db()
    yield d


# ---- coding -------------------------------------------------------------

def test_extract_codes_with_links_and_citations():
    body = "She said [I trust it, like [[20260101000000]] and [@smith2020, p. 3]]{.trust .automation} then left."
    (codes, excerpt, pos), = codebook.extract(body)
    assert codes == ["trust", "automation"]
    assert "[[20260101000000]]" in excerpt and "[@smith2020, p. 3]" in excerpt


def test_extract_ignores_links_and_other_spans():
    body = "A [link](http://x.y), a [[20260101000000]] link, [an id]{#x} and [[20260101000000|alias]]{.no}"
    assert codebook.extract(body) == []


def test_rename_merge_and_remove():
    body = "[a]{.trust .automation} and [b]{.trust}"
    assert codebook.apply_rename(body, "trust", "reliance") == "[a]{.reliance .automation} and [b]{.reliance}"
    assert codebook.apply_rename(body, "trust", "automation") == "[a]{.automation} and [b]{.automation}"
    assert codebook.apply_rename(body, "trust", None) == "[a]{.automation} and b"


def test_normalize_code():
    assert codebook.normalize("Human–machine Trust!") == "human-machine-trust"
    assert codebook.CODE_NAME_RE.match(codebook.normalize("Work arounds"))


# ---- search filters ------------------------------------------------------

def test_parse_query():
    p = db.parse_query('kind:journal tag:Drones -tag:old thread:"tethered autonomy" code:trust '
                       'after:2025-06 before:junk is:locked "exact words" -noise plain')
    assert p["kind"] == ["journal"] and p["tag"] == ["drones"] and p["not_tag"] == ["old"]
    assert p["thread"] == ["tethered autonomy"] and p["code"] == ["trust"]
    assert p["after"] == "2025-06" and p["before"] is None and p["bad"] == ["before:junk"]
    assert p["is"] == ["locked"] and p["phrases"] == ["exact words"]
    assert p["not_words"] == ["noise"] and p["words"] == ["plain"]
    assert db.has_filters(p)
    assert not db.has_filters(db.parse_query("just words here"))


def test_filtered_search(fresh):
    a = notes.create_note("Drone pilots", body="Pilots trust [the autopilot]{.trust}.", tags=["drones"], kind="research")
    b = notes.create_note("Diary", body="Garden today.", tags=["home"], kind="journal")
    for n in (a, b):
        db.upsert_note(n)
    ids = lambda q: {r["id"] for r in db.filtered_search(q)}
    assert ids("kind:journal") == {b.id}
    assert ids("tag:drones") == {a.id}
    assert ids("-tag:drones") == {b.id}
    assert ids("code:trust") == {a.id}
    assert ids('"trust the autopilot"') == {a.id}  # code markup doesn't break phrase search
    assert ids("pilots -garden") == {a.id}
    assert a.id != b.id  # created in the same second, distinct IDs


# ---- mentions ------------------------------------------------------------

def test_find_mention_skips_links_and_code():
    body = "See [[20260101000000|tethered autonomy]] and `tethered autonomy`, then Tethered  autonomy proper."
    s, e = db.find_mention(body, "Tethered autonomy")
    assert body[s:e] == "Tethered  autonomy"


def test_title_variants():
    assert db.title_variants("Tethered autonomy — working definition") == [
        "Tethered autonomy — working definition", "Tethered autonomy"]
    assert db.title_variants("Week 9: automation") == ["Week 9: automation", "Week 9"]


# ---- history -------------------------------------------------------------

def test_history_snapshot_and_throttle(fresh):
    n = notes.create_note("History", body="one")
    n.body = "two"
    notes.write_note(n)            # first overwrite: version of "one"
    n.body = "three"
    notes.write_note(n)            # within 10 minutes: no new version
    vs = history.list_versions(n.id)
    assert len(vs) == 1
    old = history.read_version(n.id, vs[0]["name"])
    assert old.body == "one"
    assert history.snapshot(n.path, "restore", force=True) is not None
    assert len(history.list_versions(n.id)) == 2
    assert history.version_path(n.id, "../../etc/passwd") is None


def test_diff_lines():
    d = history.diff_lines("a\nb\nc", "a\nB\nc")
    assert ("del", "b") in d and ("add", "B") in d


# ---- templates -----------------------------------------------------------

def test_templates_seed_render_and_save(fresh):
    names = {t["slug"] for t in note_templates.list_templates()}
    assert {"interview-memo", "fieldnote", "reading-note", "lecture-plan"} <= names
    r = note_templates.render("lecture-plan")
    assert r["kind"] == "teaching" and "## Plan" in r["body"]
    slug = note_templates.save(None, "Seminar notes", "d", "teaching", "a, b", "Seminar {{date}}", "x")
    assert note_templates.get(slug)["tags"] == ["a", "b"]
    assert note_templates.delete(slug) and note_templates.get(slug) is None
    assert note_templates.get("../settings") is None


# ---- manuscripts ---------------------------------------------------------

def test_manuscript_export_expands_embeds_and_links(fresh):
    a = notes.create_note("Part A", body="Alpha text links [[%s|elsewhere]].")
    b = notes.create_note("Part B", body="Beta text.")
    a.body = a.body % b.id
    notes.write_note(a)
    m = notes.create_note("Draft", body="## Intro\n\n![[%s]]\n![[%s]]\n" % (a.id, b.id), type_="manuscript")
    for n in (a, b, m):
        db.upsert_note(n)
    md = export.build_combined_markdown([notes.parse_file(m.path)], title="Draft", single=True)
    assert "Alpha text links elsewhere." in md and "Beta text." in md
    assert "[[" not in md and "![[" not in md
    md2 = export.build_combined_markdown([notes.parse_file(m.path)], title="Draft", embed_titles=True, single=True)
    assert "## Part A" in md2
