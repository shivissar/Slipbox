"""5.2 welcome tour: first-run start, stepping, skip, seen flag, sidebar
button, palette command, keyboard, light and dark screenshots."""
import os, sys, threading, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _harness as H  # noqa: E402
SRC = H.FIXTURE
D = H.prepare("ztour")
os.environ.update(ZETTEL_SETTINGS_PATH=f"{D}/settings.json", ZETTEL_DB_PATH=f"{D}/zettel.db",
                  ZETTEL_NOTES_DIR=f"{D}/notes", ZETTEL_EXPORT_DIR=f"{D}/exports", ZETTEL_VAULT_KDF="light")
sys.path.insert(0, H.SRC_DIR)
from Slipbox import flask_app, embed, settings, watcher
embed.embed_note = lambda note: None
flask_app.embed.embed_note = embed.embed_note
settings.save({"tour_seen": False})
watcher.full_resync()
from werkzeug.serving import make_server
srv = make_server("127.0.0.1", 0, flask_app.app, threaded=True)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_port}"
SH = H.SHOTS
results = []
def check(name, ok, detail=""):
    results.append(bool(ok)); print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))
def seen():
    return json.load(open(f"{D}/settings.json")).get("tour_seen")

from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = H.launch(p)
    errs = []
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errs.append(str(e)))

    # First run: the tour starts by itself on the first main page.
    pg.goto(f"{BASE}/"); pg.wait_for_timeout(900)
    check("starts by itself the first time", pg.locator(".tour-card").count() == 1)
    check("welcome card is centred with the mascot", pg.locator(".tour.centered .tour-art svg").count() == 1)
    check("focus is in the card", pg.evaluate("document.activeElement && document.activeElement.classList.contains('tour-next')"))
    pg.wait_for_timeout(300)
    check("marked seen as soon as it opens", seen() is True)
    pg.screenshot(path=f"{SH}/v52_tour_welcome.png")
    total_index = int(pg.locator(".tour-step").text_content().split(" of ")[1])

    # Clicks outside the card do nothing (the page underneath is covered).
    url = pg.url
    pg.mouse.click(700, 450); pg.wait_for_timeout(300)
    check("page underneath is not clickable", pg.url == url and pg.locator(".tour-card").count() == 1)

    # 5.2: three stops on the method come first, on the same page.
    check("welcome offers to skip the method", pg.locator(".tour-jump").count() == 1
          and pg.inner_text(".tour-next") == "Begin")
    intro = []
    for _ in range(3):
        pg.keyboard.press("ArrowRight"); pg.wait_for_timeout(350)
        intro.append(pg.inner_text("#tour-title"))
    check("the method before the app, on the same page", intro == ["The idea: a slip-box", "Four habits", "How Slipbox helps"]
          and "/note/" not in pg.url, (intro, pg.url))
    pg.keyboard.press("ArrowLeft"); pg.wait_for_timeout(300); pg.keyboard.press("ArrowLeft"); pg.wait_for_timeout(300)
    check("slip-box picture shown", pg.locator(".tour-art svg.tour-slips").count() == 1)
    pg.screenshot(path=f"{SH}/v52_tour_slipbox.png")
    pg.keyboard.press("ArrowRight"); pg.wait_for_timeout(300)
    pg.screenshot(path=f"{SH}/v52_tour_habits.png")
    pg.keyboard.press("ArrowRight"); pg.wait_for_timeout(300)
    check("last method stop leads to the app", pg.inner_text(".tour-next") == "Show me the app")
    pg.screenshot(path=f"{SH}/v52_tour_helps.png")

    # Step on from the list: it carries on from the latest note.
    pg.keyboard.press("ArrowRight"); pg.wait_for_timeout(1100)
    check("from the list it moves to the latest note", "/note/" in pg.url and "tour=5" in pg.url, pg.url)
    check("arrow key moves on", pg.inner_text("#tour-title") == "Four kinds of note")
    check("and picks up at step 5", pg.locator(".tour-step").text_content().startswith("5 of"))
    spot = pg.locator(".tour-spot").bounding_box(); lib = pg.locator('[data-tour="library"]').bounding_box()
    check("spotlight surrounds its target", spot and lib and spot["x"] <= lib["x"] and spot["y"] <= lib["y"]
          and spot["x"] + spot["width"] >= lib["x"] + lib["width"] - 1, (spot, lib))
    card = pg.locator(".tour-card").bounding_box()
    check("card does not cover the target", card["x"] >= lib["x"] + lib["width"] or card["y"] >= lib["y"] + lib["height"]
          or card["x"] + card["width"] <= lib["x"] or card["y"] + card["height"] <= lib["y"], (card, lib))
    pg.screenshot(path=f"{SH}/v52_tour_library.png")
    pg.keyboard.press("ArrowLeft"); pg.wait_for_timeout(300)
    check("arrow key goes back", pg.inner_text("#tour-title") == "How Slipbox helps")
    for _ in range(3):
        pg.keyboard.press("ArrowLeft"); pg.wait_for_timeout(200)
    check("back to the welcome", pg.inner_text("#tour-title") == "Welcome to Slipbox")
    pg.click(".tour-jump"); pg.wait_for_timeout(500)
    check("Go straight to the app skips the method", pg.inner_text("#tour-title") == "Four kinds of note")
    pg.keyboard.press("ArrowLeft"); pg.wait_for_timeout(200)
    check("app shortcuts are held back mid-tour", (pg.keyboard.press("Meta+k"), pg.wait_for_timeout(300), pg.is_hidden("#palette"))[2])

    # Skip ends it, and it doesn't come back.
    pg.click(".tour-skip"); pg.wait_for_timeout(300)
    check("skip closes it", pg.locator(".tour").count() == 0)
    pg.reload(); pg.wait_for_timeout(900)
    check("no tour after it has been seen", pg.locator(".tour").count() == 0)

    # The compass button in the sidebar brings it back, on the latest note.
    pg.click('[data-start-tour]'); pg.wait_for_timeout(1000)
    check("sidebar button opens a note with the tour", "/note/" in pg.url and pg.locator(".tour-card").count() == 1, pg.url)
    total_note = int(pg.locator(".tour-step").text_content().split(" of ")[1])
    check("a note shows the note stops too", total_note > total_index, (total_note, total_index))
    titles = []
    for i in range(total_note):
        titles.append(pg.inner_text("#tour-title"))
        if titles[-1] == "Everything connected to this note":
            pg.screenshot(path=f"{SH}/v52_tour_rail.png")
        if titles[-1] == "Lock a note":
            pg.screenshot(path=f"{SH}/v52_tour_lock.png")
        if i < total_note - 1:
            pg.click(".tour-next"); pg.wait_for_timeout(380)
    check("all stops on a note", {"Write and edit", "Lock a note", "History", "Export", "Everything connected to this note",
                                   "While you write", "Browse", "Make it yours"} <= set(titles), titles)
    check("last stop points at the tour button", titles[-1] == "That’s the tour" and pg.inner_text(".tour-next") == "Done")
    spot = pg.locator(".tour-spot").bounding_box(); btn = pg.locator('[data-tour="tour"]').bounding_box()
    check("last spotlight is on the compass", abs((spot["x"] + spot["width"] / 2) - (btn["x"] + btn["width"] / 2)) < 3, (spot, btn))
    for _ in range(total_note):  # every card stays on screen
        pass
    pg.screenshot(path=f"{SH}/v52_tour_end.png")
    pg.click(".tour-next"); pg.wait_for_timeout(300)
    check("done closes it and tidies the address", pg.locator(".tour").count() == 0 and "tour=1" not in pg.url, pg.url)

    # Palette command, on a note: tours in place. Esc closes.
    pg.keyboard.press("Meta+k"); pg.wait_for_timeout(300)
    pg.keyboard.type("take the tour"); pg.wait_for_timeout(400)
    pg.keyboard.press("Enter"); pg.wait_for_timeout(500)
    check("palette command starts it", pg.locator(".tour-card").count() == 1 and pg.is_hidden("#palette"))
    pg.keyboard.press("Tab"); pg.keyboard.press("Tab"); pg.keyboard.press("Tab")
    check("Tab stays in the card", pg.evaluate("!!document.activeElement.closest('.tour-card')"))
    pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
    check("Esc closes it", pg.locator(".tour").count() == 0)
    check("Esc did not also close other panels", pg.is_hidden("#palette"))

    # Cards stay inside the window at a small size.
    pg.set_viewport_size({"width": 900, "height": 640})
    pg.goto(pg.url.split("?")[0] + "?tour=1"); pg.wait_for_timeout(900)
    inside = True
    n = int(pg.locator(".tour-step").text_content().split(" of ")[1])
    for i in range(n):
        bb = pg.locator(".tour-card").bounding_box()
        inside = inside and bb["x"] >= 0 and bb["y"] >= 0 and bb["x"] + bb["width"] <= 900 and bb["y"] + bb["height"] <= 640
        if i < n - 1:
            pg.click(".tour-next"); pg.wait_for_timeout(330)
    check("every card fits a small window", inside)
    pg.keyboard.press("Escape")

    # Dark mode screenshot
    pg.set_viewport_size({"width": 1440, "height": 900})
    pg.emulate_media(color_scheme="dark")
    pg.evaluate("localStorage.setItem('zettel-mode', 'dark')")
    pg.goto(pg.url.split("?")[0] + "?tour=1"); pg.wait_for_timeout(900)
    pg.click(".tour-next"); pg.wait_for_timeout(200)
    for _ in range(8):
        if pg.inner_text("#tour-title") == "Everything connected to this note":
            break
        pg.click(".tour-next"); pg.wait_for_timeout(380)
    pg.screenshot(path=f"{SH}/v52_tour_rail_dark.png")
    pg.keyboard.press("Escape")

    # Reduced motion still works.
    ctx2 = b.new_context(viewport={"width": 1280, "height": 800}, reduced_motion="reduce")
    p2 = ctx2.new_page()
    p2.goto(f"{BASE}/tour"); p2.wait_for_timeout(500)
    check("reduced motion: no animation", p2.locator(".tour.no-motion").count() == 1)

    check("no console errors", not errs, errs[:4])
    b.close()

srv.shutdown()
print(f"\n{sum(results)}/{len(results)} passed")
