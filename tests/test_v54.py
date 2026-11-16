"""5.4: threads that branch. A branch is a thread whose notes record the
thread it leaves and the note it leaves from; branches can branch again,
ten levels deep; they follow the note they leave, and the tree is shown on
the thread's page, the Threads page and in a note's side panel."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

_tmp = Path(tempfile.mkdtemp(prefix="z54-"))
os.environ.setdefault("ZETTEL_SETTINGS_PATH", str(_tmp / "settings.json"))
os.environ.setdefault("ZETTEL_NOTES_DIR", str(_tmp / "notes"))
os.environ.setdefault("ZETTEL_DB_PATH", str(_tmp / "zettel.db"))
os.environ["ZETTEL_VAULT_KDF"] = "light"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from Slipbox import db, embed, flask_app, history, notes, settings, threads, vault  # noqa: E402

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
    app.testing = True
    yield app.test_client()
    vault.lock()


def _make(*titles, kind="research"):
    out = []
    for t in titles:
        n = notes.create_note(t, body="Text of %s." % t, kind=kind)
        db.upsert_note(n)
        out.append(n.id)
    return out


def _note(note_id):
    return notes.parse_file(notes.NOTES_DIR / db.get_note_meta(note_id)["filename"])


def _chain(name):
    return [c["id"] for c in db.thread_chain(name)]


def _entry(note_id, name):
    hits = [t for t in _note(note_id).threads if t.get("name") == name]
    assert len(hits) == 1, hits
    return hits[0]


def _line(name, *ids):
    for i in ids:
        threads.add(i, name)


def _labels(node, out=None):
    """{label: title or branch name} for a whole tree."""
    out = {} if out is None else out
    for n in node["notes"]:
        out[n["label"]] = n["title"]
        for b in n["branches"]:
            out[b["label"]] = "⛓ " + b["name"]
            _labels(b, out)
    for b in node["loose"]:
        out[b["label"]] = "⛓ " + b["name"]
        _labels(b, out)
    return out


# ---- the model -----------------------------------------------------------------

def test_a_branch_is_a_thread_that_records_where_it_leaves(client):
    a, b, c, x, y = _make("A", "B", "C", "X", "Y")
    _line("main", a, b, c)
    threads.start_branch("objections", "main", b, x)
    threads.add(y, "objections")
    assert _chain("main") == [a, b, c] and _chain("objections") == [x, y]
    for nid in (x, y):                                   # every member carries the pair
        e = _entry(nid, "objections")
        assert e["parent"] == "main" and e["from"] == b
    assert "parent" not in _entry(a, "main") and "from" not in _entry(b, "main")
    assert threads.parent_of("objections") == ("main", b) and threads.parent_of("main") is None
    assert threads.children("main") == ["objections"] and threads.depth("objections") == 1
    assert threads.height("main") == 1 and threads.label("objections") == "2a"
    # A branch behaves as any thread: reorder, take out, and the pair stays.
    threads.move(y, "objections", -1)
    assert _chain("objections") == [y, x] and _entry(y, "objections")["from"] == b
    threads.remove(x, "objections")
    assert threads.parent_of("objections") == ("main", b)


def test_an_ordinary_thread_is_written_as_before(client):
    a, b = _make("A", "B")
    _line("plain", a, b)
    before = {i: _note(i).path.read_bytes() for i in (a, b)}
    assert b"parent" not in before[a] and b"from:" not in before[a]
    threads._write("plain", [a, b])                      # nothing to change, nothing written
    assert {i: _note(i).path.read_bytes() for i in (a, b)} == before


def test_labels_are_luhmanns(client):
    a, b, c, d, e, f, g, h = _make("A", "B", "C", "D", "E", "F", "G", "H")
    _line("main", a, b, c)
    threads.start_branch("beta", "main", b, d)           # second branch by name from B
    threads.start_branch("alpha", "main", b, e)          # first by name
    threads.add(f, "alpha")
    threads.start_branch("deep", "alpha", f, g)
    threads.start_branch("first", "main", a, h)
    got = _labels(threads.tree("main"))
    assert got == {"1": "A", "1a": "⛓ first", "1a1": "H", "2": "B", "2a": "⛓ alpha", "2a1": "E", "2a2": "F",
                   "2a2a": "⛓ deep", "2a2a1": "G", "2b": "⛓ beta", "2b1": "D", "3": "C"}
    assert threads.label("deep") == "2a2a" and threads.tree("deep")["notes"][0]["label"] == "2a2a1"
    assert [threads._letters(n) for n in (0, 25, 26, 27, 701, 702)] == ["a", "z", "aa", "ab", "zz", "aaa"]
    top = [(n["name"], [k["name"] for k in n["all"]]) for n in threads.forest()]
    assert top == [("main", ["first", "alpha", "beta"])]


def test_splitting_a_thread_moves_the_rest_and_its_branches(client):
    a, b, c, d, x = _make("A", "B", "C", "D", "X")
    _line("main", a, b, c, d)
    threads.start_branch("aside", "main", c, x)          # leaves from C, which is about to move
    assert threads.cut("main", b, "second half") == 2
    assert _chain("main") == [a, b] and _chain("second half") == [c, d]
    assert threads.parent_of("second half") == ("main", b)
    assert threads.parent_of("aside") == ("second half", c)        # went with its note
    assert not [t for t in _note(c).threads if t["name"] == "main"]
    assert _labels(threads.tree("main")) == {"1": "A", "2": "B", "2a": "⛓ second half", "2a1": "C",
                                             "2a1a": "⛓ aside", "2a1a1": "X", "2a2": "D"}
    with pytest.raises(threads.ThreadError, match="no notes after this one"):
        threads.cut("main", b, "nothing")
    with pytest.raises(threads.ThreadError, match="already a thread called"):
        threads.cut("main", a, "aside")


def test_an_existing_thread_can_become_a_branch_and_a_thread_of_its_own_again(client):
    a, b, x, y, z = _make("A", "B", "X", "Y", "Z")
    _line("main", a, b)
    _line("other", x, y)
    threads.start_branch("under other", "other", y, z)
    threads.attach("other", "main", a)
    assert threads.parent_of("other") == ("main", a) and threads.depth("under other") == 2
    threads.attach("other", "main", b)                   # moved to another note
    assert threads.label("other") == "2a"
    with pytest.raises(threads.ThreadError, match="can’t branch from itself"):
        threads.attach("main", "main", a)
    with pytest.raises(threads.ThreadError, match="other way round"):
        threads.attach("main", "under other", z)         # main is above it already
    with pytest.raises(threads.ThreadError, match="isn’t in “main” any more"):
        threads.attach("other", "main", x)
    threads.detach("other")
    assert threads.parent_of("other") is None and "parent" not in _entry(x, "other")
    assert threads.parent_of("under other") == ("other", y)        # its own branch stays


def test_branches_follow_the_note_they_leave(client):
    a, b, c, x, y = _make("A", "B", "C", "X", "Y")
    _line("main", a, b, c)
    threads.start_branch("from b", "main", b, x)
    threads.move(b, "main", 1)                           # B moves along: the branch goes with it
    assert _chain("main") == [a, c, b] and threads.label("from b") == "3a"
    threads.remove(b, "main")                            # B leaves: the branch moves to the note before
    assert threads.parent_of("from b") == ("main", c)
    threads.start_branch("from a", "main", a, y)
    threads.remove(a, "main")                            # the first note leaves: to the note after
    assert threads.parent_of("from a") == ("main", c)


def test_when_a_whole_thread_goes_its_branches_move_up(client):
    a, b, m, x, y = _make("A", "B", "M", "X", "Y")
    _line("main", a, b)
    threads.start_branch("middle", "main", b, m)
    threads.start_branch("leaf", "middle", m, x)
    threads.remove(m, "middle")                          # middle had one note: it is gone
    assert _chain("middle") == [] and threads.parent_of("leaf") == ("main", b)
    threads.start_branch("solo", "main", a, y)
    threads.remove(a, "main")
    threads.remove(b, "main")                            # main is gone: its branches stand alone
    assert threads.parent_of("leaf") is None and threads.parent_of("solo") is None


def test_a_branch_whose_note_was_deleted_is_still_shown(client):
    a, b, x = _make("A", "B", "X")
    _line("main", a, b)
    threads.start_branch("orphan", "main", b, x)
    note = _note(b)
    notes.soft_delete(note)                              # as the trash button does
    db.delete_note(b)
    node = threads.tree("main")
    assert [n["title"] for n in node["notes"]] == ["A"]
    assert [k["name"] for k in node["loose"]] == ["orphan"] and node["loose"][0]["label"] == "?a"
    threads.attach("orphan", "main", a)                  # and can be given a new place
    assert threads.label("orphan") == "1a"


def test_ten_levels_and_no_deeper(client):
    ids = _make(*["N%d" % i for i in range(13)])
    threads.add(ids[0], "level 0")
    for i in range(1, 11):
        threads.start_branch("level %d" % i, "level %d" % (i - 1), ids[i - 1], ids[i])
    assert threads.depth("level 10") == 10 and threads.height("level 0") == 10
    with pytest.raises(threads.ThreadError, match="10 levels deep"):
        threads.start_branch("level 11", "level 10", ids[10], ids[11])
    threads.add(ids[11], "level 10")
    with pytest.raises(threads.ThreadError, match="10 levels deep"):
        threads.cut("level 10", ids[10], "too deep")
    threads.add(ids[12], "separate")
    with pytest.raises(threads.ThreadError, match="10 levels deep"):
        threads.attach("separate", "level 10", ids[10])
    threads.attach("separate", "level 9", ids[9])        # at the tenth level it fits
    assert threads.depth("separate") == 10
    labels = _labels(threads.tree("level 0"))
    assert labels["1a1a1a1a1a1a1a1a1a1a1"] == "N10"      # ten letters deep


def test_a_loop_made_by_hand_does_not_hang(client):
    a, b = _make("A", "B")
    _line("one", a)
    _line("two", b)
    threads._write("one", [a], link=("two", b))
    threads._write("two", [b], link=("one", a))          # what attach() refuses; a hand edit could do it
    # The loop is cut at its first thread by name, which stands on its own.
    assert threads.depth("one") == 0 and threads.parent_of("two") == ("one", a) and threads.height("one") == 1
    assert [(n["name"], [k["name"] for k in n["all"]]) for n in threads.forest()] == [("one", ["two"])]
    assert threads.label("two") == "1a" and threads.tree("two")["label"] == "1a"
    assert client.get("/threads").status_code == 200 and client.get("/thread/one").status_code == 200


def test_a_change_across_threads_stops_before_writing_if_a_note_cannot_be_read(client):
    a, b, c, x = _make("A", "B", "C", "X")
    _line("main", a, b, c)
    threads.start_branch("aside", "main", c, x)
    bad = _note(x).path
    bad.write_text(bad.read_text().replace("title: X", 'title: "X'))
    before = {i: _note(i).path.read_bytes() for i in (a, b, c)}
    with pytest.raises(threads.ThreadError, match="can’t be read"):
        threads.cut("main", a, "rest")
    with pytest.raises(threads.ThreadError, match="can’t be read"):
        threads.remove(c, "main")
    assert {i: _note(i).path.read_bytes() for i in (a, b, c)} == before


def test_branching_does_not_count_as_editing(client, monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(notes, "now_iso", lambda: "2020-01-01T10:00:00")
        a, b, c, x = _make("A", "B", "C", "X")
    _line("main", a, b, c)
    threads.start_branch("aside", "main", a, x)
    threads.cut("main", b, "rest")
    threads.detach("aside")
    for i in (a, b, c, x):
        assert _note(i).modified == "2020-01-01T10:00:00" and not history.list_versions(i)


def test_a_locked_closed_note_can_be_in_a_branch(client):
    vault.setup(PW, kdf=vault.ARGON2_LIGHT)
    a, b = _make("A", "B")
    secret = notes.create_note("Secret", body="the hidden fieldnote", locked=True)
    db.upsert_note(secret)
    _line("main", a, b)
    vault.lock()
    threads.start_branch("private", "main", a, secret.id)
    after = _note(secret.id)
    assert after.unreadable and _entry(secret.id, "private")["parent"] == "main"
    vault.unlock(PW)
    assert _note(secret.id).body == "the hidden fieldnote"


def test_the_index_is_rebuilt_from_the_files(client):
    a, b, x = _make("A", "B", "X")
    _line("main", a, b)
    threads.start_branch("aside", "main", b, x)
    with db.get_conn() as conn:
        conn.execute("DELETE FROM thread_membership")
    assert threads.parent_of("aside") is None
    for p in notes.NOTES_DIR.glob("*.md"):
        db.upsert_note(notes.parse_file(p))
    assert threads.parent_of("aside") == ("main", b)


def test_a_database_from_5_3_gains_the_columns(client, tmp_path, monkeypatch):
    old = tmp_path / "old.db"
    monkeypatch.setattr(db, "DB_PATH", old)
    db.init_db()
    with db.get_conn() as conn:
        conn.execute("DROP TABLE thread_membership")
        conn.execute("CREATE TABLE thread_membership (thread_name TEXT NOT NULL, note_id TEXT NOT NULL, "
                     "prev_id TEXT, next_id TEXT, PRIMARY KEY (thread_name, note_id))")
        conn.execute("INSERT INTO thread_membership VALUES ('kept', '20200101000000', NULL, NULL)")
    db.init_db()
    db.init_db()                                         # and again: no error the second time
    with db.get_conn() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(thread_membership)")}
        assert {"parent_thread", "from_id"} <= cols
        assert conn.execute("SELECT COUNT(*) FROM thread_membership").fetchone()[0] == 1
    assert db.thread_links() == {}


# ---- the pages -------------------------------------------------------------------

def _post(client, **data):
    return client.post("/threads/change", data=dict({"back": "thread"}, **data))


def test_start_a_branch_with_a_note_already_written(client):
    a, b, c, x = _make("Alpha", "Beta", "Gamma", "Xi")
    _line("main", a, b, c)
    page = client.get("/thread/main?anchor=" + b).get_data(as_text=True)
    assert "Start a branch" in page and '<option value="%s" selected>2 · Beta</option>' % b in page
    assert "a thread I already have" not in page                 # there is no other thread yet
    r = _post(client, action="branch", thread="main", anchor=b, start="note", new_thread="objections", note="Xi · " + x)
    assert r.status_code == 302 and r.location.endswith("/thread/main#n-" + b)
    assert flask_app._SWEEP["msg"] == "Started the branch “objections” from “Beta”."
    assert threads.parent_of("objections") == ("main", b)
    page = client.get("/thread/main").get_data(as_text=True)
    assert '<span class="tl">2a</span>' in page and '<span class="tl">2a1</span>' in page and "1 branch" in page
    page = client.get("/thread/objections").get_data(as_text=True)
    assert "Branch of" in page and 'href="/thread/main#n-%s">main</a>, from “Beta”' % b in page
    assert '<span class="tl">2a1</span>' in page and "This branch" in page and "Make it a thread of its own" in page


def test_split_a_thread_from_its_page(client):
    a, b, c, d = _make("Alpha", "Beta", "Gamma", "Delta")
    _line("main", a, b, c, d)
    _post(client, action="branch", thread="main", anchor=b, start="rest", new_thread="later")
    assert flask_app._SWEEP["msg"] == "Moved 2 notes into the branch “later”, which leaves from “Beta”."
    assert _chain("main") == [a, b] and _chain("later") == [c, d]
    _post(client, action="branch", thread="main", anchor=b, start="rest", new_thread="again")
    assert "no notes after this one" in flask_app._SWEEP["msg"] and _chain("again") == []


def test_hang_an_existing_thread_on_a_note_move_it_and_free_it(client):
    a, b, x, y = _make("Alpha", "Beta", "Xi", "Ypsilon")
    _line("main", a, b)
    _line("other", x, y)
    page = client.get("/thread/main").get_data(as_text=True)
    assert "a thread I already have" in page and '<option value="other">other (2 notes)</option>' in page
    _post(client, action="branch", thread="main", anchor=a, start="thread", other="other")
    assert flask_app._SWEEP["msg"] == "“other” now branches from “Alpha”." and threads.label("other") == "1a"
    assert '<option value="main">' not in client.get("/thread/other").get_data(as_text=True)   # no loop on offer
    r = _post(client, action="reattach", thread="other", anchor=b)
    assert r.location.endswith("/thread/other") and threads.label("other") == "2a"
    assert flask_app._SWEEP["msg"] == "“other” now leaves from “Beta”."
    _post(client, action="detach", thread="other")
    assert threads.parent_of("other") is None and "now a thread of its own" in flask_app._SWEEP["msg"]
    _post(client, action="reattach", thread="other", anchor=a)
    assert "isn’t a branch" in flask_app._SWEEP["msg"]
    _post(client, action="branch", thread="main", anchor=a, start="thread", other="main")
    assert "can’t branch from itself" in flask_app._SWEEP["msg"]


def test_start_a_branch_with_a_new_note(client):
    a, b = _make("Alpha", "Beta")
    _line("main", a, b)
    r = _post(client, action="branch", thread="main", anchor=b, start="new", new_thread="  a  new line ")
    assert r.status_code == 302 and "/new?" in r.location and "branch=a+new+line" in r.location
    assert _chain("a new line") == []                              # nothing exists until the note is saved
    page = client.get(r.location).get_data(as_text=True)
    assert "This note starts the branch <strong>a new line</strong>" in page and "from “Beta”" in page
    assert 'name="thread_action" value="branch"' in page and 'name="branch_anchor" value="%s"' % b in page
    assert 'value="existing"' not in page                          # the ordinary thread choice is replaced
    r = client.post("/new", data={"title": "A fresh thought", "body": "x", "kind": "research", "thread_action": "branch",
                                  "branch_name": "a new line", "branch_parent": "main", "branch_anchor": b})
    assert r.status_code == 302
    new_id = _chain("a new line")[0]
    assert _note(new_id).title == "A fresh thought" and threads.parent_of("a new line") == ("main", b)
    # The same request again (a name now taken) saves the note and says why it isn't in a thread.
    client.post("/new", data={"title": "Another", "body": "x", "kind": "research", "thread_action": "branch",
                              "branch_name": "a new line", "branch_parent": "main", "branch_anchor": b})
    assert "isn’t in a thread" in flask_app._SWEEP["msg"] and "already a thread called" in flask_app._SWEEP["msg"]
    assert len(_chain("a new line")) == 1


def test_the_editor_refuses_a_branch_that_cannot_be(client):
    a, b = _make("Alpha", "Beta")
    _line("main", a)
    for query in ("branch=x/y&parent=main&anchor=" + a, "branch=main&parent=main&anchor=" + a,
                  "branch=ok&parent=main&anchor=" + b, "branch=ok&parent=nowhere&anchor=" + a):
        r = client.get("/new?" + query)
        assert r.status_code == 302 and flask_app._SWEEP["msg"], query
        flask_app._SWEEP["msg"] = ""
    assert client.get("/new?branch=ok&parent=main&anchor=" + a).status_code == 200


def test_bad_requests_are_explained_not_errors(client):
    a, b = _make("Alpha", "Beta")
    _line("main", a)
    for data, words in (
        (dict(start="note", new_thread="x", anchor=a, note="nothing picked"), "Choose a note"),
        (dict(start="note", new_thread="x", anchor=a, note="Alpha · " + a), "different note"),
        (dict(start="note", new_thread="a/b", anchor=a, note="Beta · " + b), "slash"),
        (dict(start="note", new_thread="main", anchor=a, note="Beta · " + b), "already a thread called"),
        (dict(start="note", new_thread="x", anchor=b, note="Beta · " + b), "isn’t in “main” any more"),
        (dict(start="new", new_thread="", anchor=a), "Give the new thread a name"),
        (dict(start="thread", other="gone", anchor=a), "can’t be found"),
    ):
        r = _post(client, action="branch", thread="main", **data)
        assert r.status_code == 302 and words in flask_app._SWEEP["msg"], (data, flask_app._SWEEP["msg"])
    assert threads.tree_links() == {}


def test_a_note_shows_where_its_thread_leaves_and_what_leaves_it(client):
    a, b, x, y = _make("Alpha", "Beta", "Xi", "Ypsilon")
    _line("main", a, b)
    threads.start_branch("objections", "main", b, x)
    threads.add(y, "objections")
    page = client.get("/note/" + b).get_data(as_text=True)
    assert "Branch from this note" in page and 'href="/thread/objections"' in page and "2 notes" in page
    assert 'href="/thread/main?anchor=%s#branch"' % b in page and "Branch from here" in page
    page = client.get("/note/" + y).get_data(as_text=True)
    assert "<span>Branch</span>" in page and 'Leaves <a href="/thread/main#n-%s">main</a>' % b in page
    assert ">Beta</a>" in page and "<strong title=" in page and ">2a2</strong>" in page
    plain = client.get("/note/" + a).get_data(as_text=True)
    assert "<span>Thread</span>" in plain and "Leaves <a" not in plain and "from this note" not in plain


def test_the_threads_page_nests_branches(client):
    a, b, x, y, z = _make("Alpha", "Beta", "Xi", "Ypsilon", "Zeta")
    _line("main", a, b)
    _line("zebra", z)
    threads.start_branch("objections", "main", b, x)
    threads.start_branch("replies", "objections", x, y)
    page = client.get("/threads").get_data(as_text=True)
    assert page.count('class="thread-forest"') == 3                # the list, and one level inside another
    assert page.index("main") < page.index("objections") < page.index("replies") < page.index("zebra")
    assert '<span class="tl">2a</span>' in page and '<span class="tl">2a1a</span>' in page and "1 branch" in page
    assert "a thread can branch" in page


def test_no_deeper_than_ten_is_said_on_the_page(client):
    ids = _make(*["N%d" % i for i in range(11)])
    threads.add(ids[0], "level 0")
    for i in range(1, 11):
        threads.start_branch("level %d" % i, "level %d" % (i - 1), ids[i - 1], ids[i])
    page = client.get("/thread/level 10").get_data(as_text=True)
    assert "as deep as branches go" in page and "Start a branch" not in page
    assert "Branch from here" not in client.get("/note/" + ids[10]).get_data(as_text=True)
    assert "Start a branch" in client.get("/thread/level 9").get_data(as_text=True)
    page = client.get("/thread/level 0").get_data(as_text=True)
    assert page.count('class="tb"') == 10 and "1a1a1a1a1a1a1a1a1a1a1" in page


def test_taking_a_note_out_from_the_page_moves_its_branch(client):
    a, b, c, x = _make("Alpha", "Beta", "Gamma", "Xi")
    _line("main", a, b, c)
    threads.start_branch("aside", "main", b, x)
    page = client.get("/thread/main").get_data(as_text=True)
    assert "The branch that leaves from it will move to the note before it." in page
    _post(client, action="remove", thread="main", note=b)
    assert threads.parent_of("aside") == ("main", a)
    note = _note(a)
    notes.soft_delete(note)
    db.delete_note(a)
    page = client.get("/thread/main").get_data(as_text=True)
    assert "left from a note that is no longer in this thread" in page
    page = client.get("/thread/aside").get_data(as_text=True)
    assert "is no longer in “main”" in page and "a note that has gone" in page
