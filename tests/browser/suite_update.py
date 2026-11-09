"""5.2 update check in the browser: background check, sidebar notice,
update page, download/skip, Settings → Updates, welcome choice, palette,
help and privacy text."""
import os, sys, threading, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _harness as H  # noqa: E402
SRC = H.FIXTURE
D = H.prepare("zupd")
os.environ.update(ZETTEL_SETTINGS_PATH=f"{D}/settings.json", ZETTEL_DB_PATH=f"{D}/zettel.db",
                  ZETTEL_NOTES_DIR=f"{D}/notes", ZETTEL_EXPORT_DIR=f"{D}/exports", ZETTEL_VAULT_KDF="light")
sys.path.insert(0, H.SRC_DIR)
from Slipbox import flask_app, embed, settings, watcher, updates
embed.embed_note = lambda note: None
flask_app.embed.embed_note = embed.embed_note
settings.save({"tour_seen": True, "update_check": True, "update_next_check": 0, "update_latest": {},
               "update_skip": "", "update_error": ""})
watcher.full_resync()

REL = {"version": "5.3.0", "tag": "v5.3.0", "name": "Slipbox 5.3.0", "published": "2026-12-01",
       "notes": "Faster search.\n\n<script>window.__pwned = 1</script> **Bold?**",
       "url": "https://github.com/shivissar/Slipbox/releases/tag/v5.3.0",
       "dmg_url": "https://github.com/shivissar/Slipbox/releases/download/v5.3.0/Slipbox-5.3.0.dmg"}
state = {"mode": "ok", "calls": 0}
def fake_fetch(current):
    state["calls"] += 1
    time.sleep(1.5)  # a slow network must not hold up the page
    if state["mode"] == "offline":
        raise updates.UpdateError("Couldn’t reach GitHub. Check that you’re online; Slipbox will try again later.")
    return dict(REL)
updates.fetch_latest = fake_fetch
opened = []
updates.open_external = lambda url: (opened.append(url), True)[1] if updates.safe_url(url) else False

from werkzeug.serving import make_server
srv = make_server("127.0.0.1", 0, flask_app.app, threaded=True)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_port}"
SH = H.SHOTS
results = []
def check(name, ok, detail=""):
    results.append(bool(ok)); print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))
def cfg():
    return json.load(open(f"{D}/settings.json"))

from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = H.launch(p)
    errs = []
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("dialog", lambda d: d.accept())

    t0 = time.time(); pg.goto(f"{BASE}/"); dt = time.time() - t0
    check("the check doesn't hold up the page", dt < 1.2, dt)
    check("no notice before the answer arrives", pg.locator(".side-update").count() == 0)
    pg.wait_for_timeout(2000)
    check("checked once in the background", state["calls"] == 1 and cfg()["update_latest"].get("version") == "5.3.0", (state, cfg().get("update_error")))
    pg.reload(); pg.wait_for_timeout(300)
    check("sidebar notice appears", pg.locator(".side-update").count() == 1 and "5.3.0" in pg.inner_text(".side-update"))
    pg.screenshot(path=f"{SH}/v52_update_notice.png")
    pg.reload(); pg.wait_for_timeout(1800)
    check("not checked again the same day", state["calls"] == 1, state)

    pg.click(".side-update"); pg.wait_for_timeout(400)
    check("update page", "Slipbox 5.3.0 is available" in pg.inner_text("h1") and "You have 5.2.0" in pg.inner_text(".lead"))
    check("release notes shown as text, not run", "<script>" in pg.inner_text(".release-notes") and not pg.evaluate("window.__pwned"))
    check("install steps no longer need Terminal or Open Anyway", pg.locator("#xattr-line").count() == 0 and "Open Anyway" not in pg.locator(".update-steps").inner_text() and "downloaded from the internet" in pg.locator(".update-steps").inner_text())
    pg.screenshot(path=f"{SH}/v52_update_page.png", full_page=True)
    pg.click("button:has-text('Download Slipbox 5.3.0')"); pg.wait_for_timeout(400)
    check("download opens the DMG link outside the app", opened[-1:] == [REL["dmg_url"]], opened)
    check("and says what happens next", "downloading Slipbox 5.3.0" in pg.inner_text(".flash.success"))
    pg.click("button:has-text('Open the release page')"); pg.wait_for_timeout(300)
    check("release page opens outside the app", opened[-1] == REL["url"])

    # A forged form can't open anything else.
    r = pg.request.post(f"{BASE}/update/open", form={"target": "download", "url": "https://evil.example/"},
                        headers={"Origin": BASE}, max_redirects=0)
    check("only the stored GitHub links can be opened", opened[-1] == REL["dmg_url"] and "evil" not in "".join(opened), opened)

    pg.click("button:has-text('Skip this version')"); pg.wait_for_timeout(400)
    check("skip hides the sidebar notice", pg.locator(".side-update").count() == 0 and cfg()["update_skip"] == "5.3.0")
    check("page still offers it, marked skipped", "chose to skip" in pg.inner_text(".lead"))
    settings.save({"update_skip": ""})

    # Settings → Updates
    pg.goto(f"{BASE}/settings#updates"); pg.wait_for_timeout(400)
    check("settings section shows the new version", "5.3.0 is available" in pg.inner_text("#updates"))
    pg.locator("#updates").screenshot(path=f"{SH}/v52_update_settings.png")
    pg.click("label[for=update-check]"); pg.wait_for_timeout(700)
    check("switch saves by itself", cfg()["update_check"] is False and "Update checks are off" in pg.inner_text("#updates"), cfg()["update_check"])
    check("no notice while checks are off", pg.locator(".side-update").count() == 0)
    state["mode"] = "offline"
    pg.click("#updates button:has-text('Check now')"); pg.wait_for_timeout(2200)
    check("Check now works with checks off, and explains failures", state["calls"] == 2 and "online" in pg.inner_text("#updates"), pg.inner_text("#updates")[:300])
    check("a failed check keeps the last answer", cfg()["update_latest"].get("version") == "5.3.0")
    pg.click("label[for=update-check]"); pg.wait_for_timeout(700)
    check("turning it back on schedules a check", cfg()["update_check"] is True and cfg()["update_next_check"] == 0)
    state["mode"] = "ok"

    # Palette
    pg.goto(f"{BASE}/"); pg.wait_for_timeout(2200)
    pg.keyboard.press("Meta+k"); pg.wait_for_timeout(200)
    pg.keyboard.type("check for updates"); pg.wait_for_timeout(400)
    pg.keyboard.press("Enter"); pg.wait_for_timeout(500)
    check("palette command", pg.url.endswith("/update"))

    # Up to date
    flask_app.__version__ = "5.3.0"
    pg.goto(f"{BASE}/update"); pg.wait_for_timeout(300)
    check("up-to-date page", "latest version" in pg.inner_text("h1") and pg.locator(".side-update").count() == 0)
    flask_app.__version__ = "5.2.0"

    # Help and privacy
    pg.goto(f"{BASE}/help#updates"); pg.wait_for_timeout(400)
    check("help has an Updates section in its contents", pg.locator('.help-toc a[href="#updates"]').count() == 1 and "downloaded from the internet" in pg.inner_text("#updates ~ ol") and "xattr" not in pg.inner_text("#updates ~ ol"))
    pg.goto(f"{BASE}/privacy"); pg.wait_for_timeout(200)
    txt = pg.inner_text("article")
    check("privacy page explains the check", "api.github.com" in txt and "no update check" not in txt)

    # Welcome: the choice is recorded
    pg.goto(f"{BASE}/welcome"); pg.wait_for_timeout(300)
    check("welcome offers the choice, ticked", pg.is_checked("#w-updates"))
    pg.screenshot(path=f"{SH}/v52_update_welcome.png")
    pg.uncheck("#w-updates"); pg.click("button:has-text('Start writing')"); pg.wait_for_timeout(500)
    check("unticking on the welcome screen turns checks off", cfg()["update_check"] is False)

    # Dark
    settings.save({"update_check": True})
    pg.emulate_media(color_scheme="dark")
    pg.goto(f"{BASE}/update"); pg.wait_for_timeout(400)
    pg.screenshot(path=f"{SH}/v52_update_page_dark.png")

    check("no console errors", not errs, errs[:4])
    b.close()

srv.shutdown()
print(f"\n{sum(results)}/{len(results)} passed")
