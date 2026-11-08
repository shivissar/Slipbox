"""Autosave checks for 5.0.1. Runs Flask in-process (embedding stubbed and
counted) against a throwaway copy of the test data, drives it with Chrome."""
import os, sys, threading, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _harness as H  # noqa: E402
SRC = H.FIXTURE
D = H.prepare("zauto")
os.environ.update(ZETTEL_SETTINGS_PATH=f"{D}/settings.json", ZETTEL_DB_PATH=f"{D}/zettel.db",
                  ZETTEL_NOTES_DIR=f"{D}/notes", ZETTEL_EXPORT_DIR=f"{D}/exports")
sys.path.insert(0, H.SRC_DIR)
from Verweis import flask_app, embed, settings, watcher

calls = []
embed.embed_note = lambda note: calls.append(note.id)
flask_app.embed.embed_note = embed.embed_note
settings.save({"autosave": True})
watcher.full_resync()
from werkzeug.serving import make_server
srv = make_server("127.0.0.1", 0, flask_app.app, threaded=True)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_port}"
P = open(f"{SRC}/plans_id.txt").read().strip()

def md_files():
    return sorted(glob.glob(f"{D}/notes/*.md"))
def file_of(nid):
    m = [f for f in md_files() if os.path.basename(f).startswith(nid)]
    return open(m[0]).read() if m else ""

results = []
def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))

from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = H.launch(p)
    errs = []
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errs.append(str(e)))

    # 1. Existing note: autosave writes the file, no embedding.
    pg.goto(f"{BASE}/note/{P}/edit"); pg.wait_for_timeout(300)
    check("Done label with autosave on", pg.inner_text("[data-cancel]").strip() == "Done")
    pg.click("#body"); pg.keyboard.press("End"); pg.keyboard.type(" AUTOSAVE-ONE")
    pg.wait_for_timeout(2500)
    check("existing note written after pause", "AUTOSAVE-ONE" in file_of(P))
    check("chip says Saved", pg.inner_text("#dirty-chip").strip() == "Saved", pg.inner_text("#dirty-chip"))
    check("no embedding while autosaving", calls.count(P) == 0, calls)
    # 2. Leaving via Done: final save embeds once.
    pg.keyboard.type(" TAIL")
    pg.click("[data-cancel]"); pg.wait_for_timeout(1200)
    check("Done goes to the note", pg.url.endswith(f"/note/{P}"), pg.url)
    check("leave flushes last words", "AUTOSAVE-ONE TAIL" in file_of(P))
    embed.wait_for_background(5)
    check("leave embeds once", calls.count(P) == 1, calls)

    # 3. New note: nothing created while the title has focus.
    before = len(md_files())
    pg.goto(f"{BASE}/new?kind=research"); pg.wait_for_timeout(300)
    pg.click("#title-input"); pg.keyboard.type("Autosave makes a note")
    pg.wait_for_timeout(2500)
    check("no note while typing the title", len(md_files()) == before)
    pg.click("#body"); pg.keyboard.type("First line of the body.")
    pg.wait_for_timeout(2500)
    new_files = sorted(set(md_files()) - set(glob.glob("/nonexistent")))
    created = [f for f in md_files() if "autosave-makes-a-note" in f]
    check("one note created on first save", len(md_files()) == before + 1 and len(created) == 1, [os.path.basename(f) for f in created])
    check("address switches to edit URL", "/edit" in pg.url and "/new" not in pg.url, pg.url)
    nid = os.path.basename(created[0]).split("-")[0] if created else ""
    pg.keyboard.type(" Second sentence.")
    pg.wait_for_timeout(2500)
    check("later saves update the same note", len(md_files()) == before + 1 and "Second sentence." in file_of(nid))
    # thread chosen after creation is applied by the explicit save
    pg.click('label:has(input[name="thread_action"][value="new"])')
    pg.fill("#new-thread-name", "autosave-thread")
    pg.wait_for_timeout(2000)
    check("autosave does not apply thread", "autosave-thread" not in file_of(nid))
    pg.keyboard.press("Meta+s"); pg.wait_for_timeout(1200)
    check("Cmd-S goes to the note", pg.url.endswith(f"/note/{nid}"), pg.url)
    check("Cmd-S applies the thread", "autosave-thread" in file_of(nid))
    embed.wait_for_background(5)
    check("Cmd-S embeds", calls.count(nid) >= 1, calls)
    check("still one new note", len(md_files()) == before + 1)

    # 4. New note without a title: never created, guard stays.
    pg.goto(f"{BASE}/new"); pg.wait_for_timeout(300)
    pg.click("#body"); pg.keyboard.type("Body with no title")
    pg.wait_for_timeout(2200)
    check("untitled draft not created", len(md_files()) == before + 1)
    check("chip asks for a title", "title" in pg.inner_text("#dirty-chip"), pg.inner_text("#dirty-chip"))
    check("quit: untitled draft reported", pg.evaluate("window.__zettelQuit()") == "untitled")
    pg.on("dialog", lambda d: d.accept())

    # 4b. 5.2: quitting the app saves what was typed a moment ago
    pg.goto(f"{BASE}/note/{P}/edit"); pg.wait_for_timeout(300)
    check("quit: nothing typed is clean", pg.evaluate("window.__zettelQuit()") == "clean")
    pg.click("#body"); pg.keyboard.press("End"); pg.keyboard.type(" QUIT-TAIL")
    n_before = calls.count(P)
    state = pg.evaluate("window.__zettelQuit()")
    check("quit: saves at once", state == "saved" and "QUIT-TAIL" in file_of(P), state)
    check("quit: chip says Saved", pg.inner_text("#dirty-chip").strip() == "Saved")
    embed.wait_for_background(5)
    check("quit: a leave save (refreshes related notes)", calls.count(P) == n_before + 1, calls)
    check("quit: then clean", pg.evaluate("window.__zettelQuit()") == "clean")
    pg.goto(f"{BASE}/help"); pg.wait_for_timeout(300)
    check("quit: other pages are clean", pg.evaluate("window.__zettelQuit()") == "clean")
    pg.goto(f"{BASE}/help"); pg.wait_for_timeout(300)

    # 5. Autosave off: Cancel label, nothing written.
    settings.save({"autosave": False})
    pg.goto(f"{BASE}/note/{P}/edit"); pg.wait_for_timeout(300)
    check("Cancel label with autosave off", pg.inner_text("[data-cancel]").strip() == "Cancel")
    pg.click("#body"); pg.keyboard.press("End"); pg.keyboard.type(" OFF-MODE")
    pg.wait_for_timeout(2500)
    check("autosave off writes nothing", "OFF-MODE" not in file_of(P))
    check("chip says Not saved yet", pg.inner_text("#dirty-chip").strip() == "Not saved yet")
    check("quit: autosave off reports unsaved", pg.evaluate("window.__zettelQuit()") == "unsaved")
    pg.goto(f"{BASE}/help"); pg.wait_for_timeout(300)
    settings.save({"autosave": True})

    # 6. Settings page shows and saves the switch.
    pg.goto(f"{BASE}/settings"); pg.wait_for_timeout(300)
    check("settings switch present and on", pg.is_checked("#autosave"))
    pg.close()
    check("no console errors", not errs, errs)
    b.close()

# 7. Server-side ordering and dedupe, via the test client.
c = flask_app.app.test_client()
r1 = c.post(f"/note/{P}/edit", data={"title": "x", "body": "SEQ-NEWER", "_autosave": "1", "_draft": "tokA", "_seq": "5"})
r2 = c.post(f"/note/{P}/edit", data={"title": "x", "body": "SEQ-OLDER", "_autosave": "1", "_draft": "tokA", "_seq": "3"})
check("older request cannot overwrite newer", r1.status_code == 200 and r2.status_code == 200 and "SEQ-NEWER" in file_of(P) and "SEQ-OLDER" not in file_of(P))
n0 = len(md_files())
for i in (1, 2):
    c.post("/new", data={"title": "Race note", "body": "b", "_autosave": "1", "_draft": "tokB", "_seq": str(i)})
check("same draft token creates one note", len(md_files()) == n0 + 1)
r = c.post("/new", data={"title": "", "body": "b", "_autosave": "1", "_draft": "tokC", "_seq": "1"})
check("autosave without title refused", r.status_code == 400 and len(md_files()) == n0 + 1)
r = c.post("/new", data={"title": "Plain form save", "body": "b"})
check("plain form save still redirects", r.status_code == 302)
srv.shutdown()
print(f"\n{sum(ok for _, ok in results)}/{len(results)} passed")
