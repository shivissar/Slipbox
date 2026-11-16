"""5.3 in the browser: putting existing notes into threads from the side
panel and from a thread's page, choosing the place, moving with ↑ ↓,
taking notes out, starting a thread from an existing note, and the Thread
section of the new-note form for every kind."""
import glob
import os
import sys
import threading
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _harness as H  # noqa: E402
D = H.prepare("z53thr")
os.environ.update(ZETTEL_SETTINGS_PATH=f"{D}/settings.json", ZETTEL_DB_PATH=f"{D}/zettel.db",
                  ZETTEL_NOTES_DIR=f"{D}/notes", ZETTEL_EXPORT_DIR=f"{D}/exports", ZETTEL_VAULT_KDF="light")
sys.path.insert(0, H.SRC_DIR)
from Slipbox import flask_app, embed, db, watcher, notes as N  # noqa: E402
embed.embed_note = lambda note: None
flask_app.embed.embed_note = embed.embed_note
watcher.full_resync()
from werkzeug.serving import make_server  # noqa: E402
srv = make_server("127.0.0.1", 0, flask_app.app, threaded=True)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_port}"
SH = H.SHOTS
T = "tethered-autonomy"
R1, R2, R3, R4 = "20261107184817", "20261107184818", "20261107184819", "20261107184820"
FOLGE, JOURNAL, GARDEN = "20261107184821", "20261107184823", "20261107184824"


def chain(name=T):
    return [c["id"] for c in db.thread_chain(name)]


def entry(nid, name=T):
    n = N.parse_file(glob.glob(f"{D}/notes/{nid}*.md")[0])
    hits = [t for t in n.threads if t.get("name") == name]
    return hits[0] if hits else None


results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))


from playwright.sync_api import sync_playwright  # noqa: E402
with sync_playwright() as p:
    b = H.launch(p)
    errs = []
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("dialog", lambda d: d.accept())
    asked = []
    pg.on("request", lambda r: asked.append(r.url) if "/api/thread" in r.url else None)

    # A note in no thread: Add to a thread, after a chosen note
    pg.goto(f"{BASE}/note/{FOLGE}"); pg.wait_for_timeout(400)
    check("side panel offers Add to a thread", pg.locator(".thread-add summary").inner_text().strip() == "Add to a thread")
    check("the thread's notes aren't fetched until the box is opened", asked == [], asked)
    pg.click(".thread-add summary"); pg.wait_for_timeout(500)
    check("opening the box fetches them, once", len(asked) == 1, asked)
    check("first thread chosen, its notes listed under Place it",
          pg.locator("#ta-after option").count() == 6, pg.locator("#ta-after option").all_inner_texts())
    check("New thread… is offered", pg.locator('#ta-thread option[value=""]').count() == 1)
    pg.screenshot(path=f"{SH}/v53_thread_add_panel.png")
    pg.select_option("#ta-after", R2)
    pg.click(".thread-add-form button[type=submit]"); pg.wait_for_timeout(800)
    check("placed after the chosen note", chain() == [R1, R2, FOLGE, R3, R4], chain())
    check("neighbours rewritten", entry(R2)["next"] == FOLGE and entry(R3)["prev"] == FOLGE and entry(FOLGE)["prev"] == R2)
    check("side panel shows its place", "3 of 5" in pg.inner_text("#threads"))
    check("toast confirms", "Added to “tethered-autonomy”, 3 of 5." in pg.inner_text("#toast"), pg.inner_text("#toast"))
    check("now offers another thread", pg.locator(".thread-add summary").inner_text().strip() == "Add to another thread")
    pg.screenshot(path=f"{SH}/v53_thread_added.png")

    # Take it out again from the side panel
    pg.click(".thread-out button"); pg.wait_for_timeout(800)
    check("taken out; neighbours joined", chain() == [R1, R2, R3, R4] and entry(R2)["next"] == R3 and entry(FOLGE) is None,
          chain())

    # The thread's page: move, add a note of another kind, take it out
    pg.goto(f"{BASE}/thread/{T}"); pg.wait_for_timeout(400)
    check("thread page lists the notes with ↑ ↓ ×", pg.locator(".thread-chain li").count() == 4
          and pg.locator('.thread-chain button[title="Move down"]').count() == 3)
    pg.locator(".thread-chain li").first.locator('button[title="Move down"]').click(); pg.wait_for_timeout(700)
    check("↓ moves it one place", chain() == [R2, R1, R3, R4], chain())
    check("moved note is marked", pg.url.endswith("#n-" + R1) and pg.locator(".thread-chain li:target").count() == 1, pg.url)
    pg.locator(f"#n-{R1}").locator('button[title="Move up"]').click(); pg.wait_for_timeout(700)
    check("↑ moves it back", chain() == [R1, R2, R3, R4], chain())
    pg.click("#thread-add-input"); pg.keyboard.type("Garden"); pg.wait_for_timeout(500)
    opts = pg.eval_on_selector_all("#thread-notes option", "els => els.map(e => e.value)")
    check("Add a note suggests by title", any(GARDEN in o for o in opts), opts)
    pg.fill("#thread-add-input", next((o for o in opts if GARDEN in o), ""))
    pg.select_option(".thread-add-row select", R3)
    pg.screenshot(path=f"{SH}/v53_thread_page.png")
    pg.click(".thread-add-row button[type=submit]"); pg.wait_for_timeout(800)
    check("a life note joins after the chosen note", chain() == [R1, R2, R3, GARDEN, R4], chain())
    pg.locator(f"#n-{GARDEN}").locator('button[title^="Take out"]').click(); pg.wait_for_timeout(800)
    check("× takes it out", chain() == [R1, R2, R3, R4] and entry(GARDEN) is None, chain())

    # Start a new thread from an existing journal note
    pg.goto(f"{BASE}/note/{JOURNAL}"); pg.wait_for_timeout(400)
    pg.click(".thread-add summary"); pg.wait_for_timeout(300)
    pg.select_option("#ta-thread", "")
    check("New thread asks for a name and hides Place it",
          pg.is_visible("#ta-new-name") and not pg.is_visible("#ta-after"))
    pg.fill("#ta-new-name", "diary line")
    pg.click(".thread-add-form button[type=submit]"); pg.wait_for_timeout(800)
    check("journal note starts a thread", chain("diary line") == [JOURNAL], chain("diary line"))
    check("side panel shows it", "1 of 1" in pg.inner_text("#threads"))
    pg.goto(f"{BASE}/threads"); pg.wait_for_timeout(300)
    check("Threads lists both", pg.locator(".thread-forest > li").count() == 2)

    # New-note form: Thread for every kind
    pg.goto(f"{BASE}/new?kind=journal"); pg.wait_for_timeout(400)
    check("Thread section shown for a journal note", pg.is_visible("#thread-fieldset"))
    pg.click('#thread-fieldset label:has-text("Existing")'); pg.wait_for_timeout(200)
    check("Existing lists threads", pg.is_visible("#existing-thread") and pg.locator("#existing-thread option").count() == 3)

    # ---- 5.4: branches ----------------------------------------------------
    from Slipbox import threads as TH
    pg.goto(f"{BASE}/note/{R2}"); pg.wait_for_timeout(400)
    link = pg.locator('.thread-acts a:has-text("Branch from here")')
    check("a note in a thread offers Branch from here", link.count() == 1)
    link.click(); pg.wait_for_timeout(500)
    check("it opens the thread's page with that note chosen", f"/thread/{T}" in pg.url and pg.input_value("#branch-anchor") == R2, pg.url)
    check("a new note is the default, so no picker shows",
          pg.input_value("#branch-start") == "new" and pg.is_visible("#branch-name") and not pg.is_visible("#branch-note"))
    pg.select_option("#branch-start", "thread"); pg.wait_for_timeout(150)
    check("an existing thread needs no new name", pg.is_visible("#branch-other") and not pg.is_visible("#branch-name"))
    pg.select_option("#branch-start", "note"); pg.wait_for_timeout(150)
    check("an existing note shows its picker", pg.is_visible("#branch-note") and pg.is_visible("#branch-name") and not pg.is_visible("#branch-other"))
    pg.fill("#branch-name", "objections")
    pg.click("#branch-note"); pg.keyboard.type("Garden"); pg.wait_for_timeout(500)
    opts = pg.eval_on_selector_all("#branch-notes option", "els => els.map(e => e.value)")
    pg.fill("#branch-note", next((o for o in opts if GARDEN in o), ""))
    pg.screenshot(path=f"{SH}/v54_branch_form.png", full_page=True)
    pg.click(".thread-branch-form button[type=submit]"); pg.wait_for_timeout(800)
    e = entry(GARDEN, "objections") or {}
    check("the branch is started and records where it leaves", chain("objections") == [GARDEN]
          and e.get("parent") == T and e.get("from") == R2, e)
    check("the thread itself is unchanged", chain() == [R1, R2, R3, R4] and "parent" not in entry(R2))
    check("it is drawn under its note, numbered as Luhmann numbered",
          pg.locator(f"#n-{R2} .tb-head .tl").inner_text() == "2a" and pg.locator(f"#n-{R2} .tb-notes .tl").inner_text() == "2a1")
    check("toast says so", "Started the branch “objections”" in pg.inner_text("#toast"), pg.inner_text("#toast"))

    # A branch from the branch, started with a new note
    pg.click(f"#n-{R2} .tb-head a"); pg.wait_for_timeout(500)
    check("the branch's page says where it leaves", "Branch of" in pg.inner_text(".thread-crumbs") and T in pg.inner_text(".thread-crumbs"))
    pg.fill("#branch-name", "replies")
    pg.click(".thread-branch-form button[type=submit]"); pg.wait_for_timeout(700)
    check("a new note opens in the editor, marked as starting the branch",
          "/new" in pg.url and "This note starts the branch" in pg.inner_text("#thread-fieldset"), pg.url)
    check("nothing exists until it is saved", chain("replies") == [])
    pg.fill("#title-input", "A reply to the objection"); pg.click("#body"); pg.keyboard.type("Because of this.")
    pg.wait_for_timeout(2300)
    pg.click('#edit-form button[type=submit]'); pg.wait_for_timeout(900)
    reply = chain("replies")
    check("saving the note begins the branch", len(reply) == 1 and TH.parent_of("replies") == ("objections", GARDEN), reply)
    side = pg.inner_text("#threads")
    check("its side panel says what it leaves and gives its number", "Leaves" in side and "objections" in side and "2a1a1" in side, side)
    pg.screenshot(path=f"{SH}/v54_branch_note.png")

    # Split the thread: the notes after the second go into a branch
    pg.goto(f"{BASE}/thread/{T}?anchor={R2}"); pg.wait_for_timeout(400)
    pg.select_option("#branch-start", "rest"); pg.fill("#branch-name", "second half")
    pg.click(".thread-branch-form button[type=submit]"); pg.wait_for_timeout(800)
    check("splitting moves the rest into a branch", chain() == [R1, R2] and chain("second half") == [R3, R4]
          and TH.parent_of("second half") == (T, R2), chain())
    check("both branches hang from the note, in name order",
          pg.locator(f"#n-{R2} > .tb > li > .tb-head .tl").all_inner_texts() == ["2a", "2b"])
    pg.screenshot(path=f"{SH}/v54_thread_tree.png", full_page=True)

    # Threads page nests them; a branch can stand on its own again
    pg.goto(f"{BASE}/threads"); pg.wait_for_timeout(300)
    check("Threads nests branches inside their thread", pg.locator(".thread-forest .thread-forest .thread-forest").count() == 1
          and pg.locator(".thread-forest > li").count() >= 5)
    pg.screenshot(path=f"{SH}/v54_threads_page.png")
    pg.goto(f"{BASE}/thread/objections"); pg.wait_for_timeout(300)
    pg.select_option("#branch-move", R1)
    pg.click('#this-branch button:has-text("Move the branch")'); pg.wait_for_timeout(700)
    check("a branch can be moved to another note", TH.parent_of("objections") == (T, R1) and TH.label("objections") == "1a")
    pg.click('#this-branch button:has-text("Make it a thread of its own")'); pg.wait_for_timeout(700)
    check("and made a thread of its own, keeping its own branch", TH.parent_of("objections") is None
          and TH.parent_of("replies") == ("objections", GARDEN) and pg.locator("#this-branch").count() == 0)

    check("no script errors", not errs, errs[:3])
    b.close()

srv.shutdown()
print(f"{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
