"""Locked notes (5.1) in the browser: set-up, recovery key page, lock a
note, closed view, unlock, editor inline unlock, kind lock, idle lock."""
import os, sys, threading, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _harness as H  # noqa: E402
SRC = H.FIXTURE
D = H.prepare("zlockui")
os.environ.update(ZETTEL_SETTINGS_PATH=f"{D}/settings.json", ZETTEL_DB_PATH=f"{D}/zettel.db",
                  ZETTEL_NOTES_DIR=f"{D}/notes", ZETTEL_EXPORT_DIR=f"{D}/exports", ZETTEL_VAULT_KDF="light")
sys.path.insert(0, H.SRC_DIR)
from Slipbox import flask_app, embed, settings, watcher, vault, db, notes as N
embed.embed_note = lambda note: None
flask_app.embed.embed_note = embed.embed_note
settings.save({"autosave": True})
watcher.full_resync()
from werkzeug.serving import make_server
srv = make_server("127.0.0.1", 0, flask_app.app, threaded=True)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_port}"
P = open(f"{SRC}/plans_id.txt").read().strip()
PW = "correct horse battery"
SHOTS = H.SHOTS
def raw(nid):
    return open(glob.glob(f"{D}/notes/{nid}*.md")[0]).read()
results = []
def check(name, ok, detail=""):
    results.append(bool(ok)); print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))

from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = H.launch(p)
    errs = []
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("dialog", lambda d: d.accept())
    n423 = []
    pg.on("response", lambda r: n423.append(r.url) if r.status == 423 else None)

    # Set up from Settings
    pg.goto(f"{BASE}/settings#locked"); pg.wait_for_timeout(300)
    pg.locator("#locked").scroll_into_view_if_needed()
    pg.screenshot(path=f"{SHOTS}/v51_settings_setup.png")
    pg.fill("#v-pw", PW); pg.fill("#v-pw2", PW); pg.click("text=Set up locked notes"); pg.wait_for_timeout(800)
    key = pg.inner_text(".recovery-key").strip()
    check("recovery key shown", len(key.replace("-", "")) == 52, key)
    check("continue disabled until ticked", pg.get_attribute("#done-key", "aria-disabled") == "true")
    pg.screenshot(path=f"{SHOTS}/v51_recovery.png")
    pg.check("#saved-key"); pg.click("#done-key"); pg.wait_for_timeout(400)
    check("back on settings, open", "Lock now" in pg.inner_text("#locked"))

    # Lock a note from its page
    pg.goto(f"{BASE}/note/{P}"); pg.wait_for_timeout(300)
    pg.click('button[aria-label="Lock this note"]'); pg.wait_for_timeout(600)
    check("note file encrypted", vault.BEGIN in raw(P))
    check("Locked chip on note", pg.locator(".pane-bar .chip-lock").count() == 1)
    check("Interrogate disabled", pg.locator('span[aria-disabled="true"].seg-tab').count() == 1)
    # 5.2: while open, the note says so and the sidebar offers to close them
    check("chip says Locked · open", "Locked · open" in pg.inner_text(".pane-bar"))
    check("lock notice shown", pg.locator(".lock-flash").count() == 1)
    check("sidebar offers to close", pg.locator(".side-vault button").count() == 1)
    pg.screenshot(path=f"{SHOTS}/v52_locked_open.png")
    pg.click(".side-vault button"); pg.wait_for_timeout(600)
    check("sidebar button closed them, same page", not vault.is_unlocked() and pg.url.endswith(f"/note/{P}")
          and "Locked · closed" in pg.inner_text(".pane-bar"), pg.url)
    check("sidebar button gone when closed", pg.locator(".side-vault").count() == 0)
    vault.unlock(PW)
    pg.goto(f"{BASE}/note/{P}"); pg.wait_for_timeout(300)

    # Lock now via palette
    pg.keyboard.press("Meta+k"); pg.wait_for_timeout(300); pg.keyboard.type("lock notes now"); pg.wait_for_timeout(300)
    pg.keyboard.press("Enter"); pg.wait_for_timeout(700)
    check("palette closed the vault", not vault.is_unlocked())
    pg.goto(f"{BASE}/note/{P}"); pg.wait_for_timeout(300)
    check("closed view shows lock card", pg.locator(".locked-card").count() == 1)
    pg.screenshot(path=f"{SHOTS}/v51_locked_card.png")
    check("list shows Locked", pg.locator(f'a[href="/note/{P}"] .nc-locked').count() >= 1)
    pg.fill("#unlock-pw", "wrong password!!"); pg.click(".unlock-form button"); pg.wait_for_timeout(600)
    check("wrong password message", "isn" in pg.inner_text("body") and "right" in pg.inner_text("body"))
    pg.fill("#unlock-password", PW); pg.click(".unlock-form button"); pg.wait_for_timeout(800)
    check("unlocked and back on the note", pg.url.endswith(f"/note/{P}") and pg.locator(".rendered").count() == 1, pg.url)

    # Edit locked note: autosave keeps it encrypted
    pg.goto(f"{BASE}/note/{P}/edit"); pg.wait_for_timeout(300)
    pg.click("#body"); pg.keyboard.press("End"); pg.keyboard.type(" SECRETWORDX"); pg.wait_for_timeout(2300)
    check("autosaved locked note stays encrypted", vault.BEGIN in raw(P) and "SECRETWORDX" not in raw(P))
    vault.unlock(PW)
    check("autosaved text is inside", "SECRETWORDX" in N.parse_file(glob.glob(f"{D}/notes/{P}*.md")[0]).body)
    # Closed while editing -> inline unlock, then the save goes through
    vault.lock()
    pg.keyboard.type(" AFTERLOCK"); pg.wait_for_timeout(2300)
    check("inline unlock appears", pg.is_visible("#inline-unlock"))
    pg.wait_for_timeout(4000)
    check("chip asks to unlock", "unlock to save" in pg.inner_text("#dirty-chip"), pg.inner_text("#dirty-chip"))
    check("no retry loop while closed", len(n423) <= 2, len(n423))
    pg.screenshot(path=f"{SHOTS}/v51_inline_unlock.png")
    pg.fill("#inline-unlock-pw", PW); pg.click("#inline-unlock-btn"); pg.wait_for_timeout(1500)
    check("saved after inline unlock", vault.is_unlocked() and "AFTERLOCK" in N.parse_file(glob.glob(f"{D}/notes/{P}*.md")[0]).body)
    check("chip says Saved", pg.inner_text("#dirty-chip").strip() == "Saved", pg.inner_text("#dirty-chip"))
    pg.click("[data-cancel]"); pg.wait_for_timeout(500)

    # Kind lock from Settings, then a new journal entry is born locked
    pg.goto(f"{BASE}/settings#locked"); pg.wait_for_timeout(300)
    pg.check('input[name="locked_kinds"][value="journal"]')
    pg.click('#locked form[action="/vault/settings"] button'); pg.wait_for_timeout(700)
    check("kind lock saved", "journal" in settings.get("locked_kinds", []))
    pg.goto(f"{BASE}/journal/new"); pg.wait_for_timeout(500)
    pg.click("#body"); pg.keyboard.type("Dear diary, PRIVATEENTRY."); pg.wait_for_timeout(2300)
    nid = pg.url.split("/note/")[1].split("/")[0]
    check("new journal entry encrypted", vault.BEGIN in raw(nid) and "PRIVATEENTRY" not in raw(nid))
    check("editor shows Locked chip", pg.locator(".pane-bar .chip-lock").count() == 1)
    check("no tag suggestions on locked note", pg.locator("#suggest-tags-btn").count() == 0)

    # 5.2: a plain journal note arriving from elsewhere is locked while open
    synced = N.create_note("Synced entry", body="ARRIVEDPLAIN", kind="journal")
    db.upsert_note(synced)
    check("arrived in plain text", "ARRIVEDPLAIN" in raw(synced.id))
    flask_app._SWEEP["at"] = -1e9
    pg.goto(f"{BASE}/"); pg.wait_for_timeout(900)
    check("swept: now encrypted", vault.BEGIN in raw(synced.id) and "ARRIVEDPLAIN" not in raw(synced.id))
    check("toast says so", "Locked 1 note" in pg.inner_text("#toast"), pg.inner_text("#toast"))
    pg.screenshot(path=f"{SHOTS}/v52_sweep_toast.png")

    # Idle lock: 1 minute for the test
    settings.save({"lock_after_minutes": 1})
    pg.goto(f"{BASE}/note/{P}"); pg.wait_for_timeout(400)
    check("idle attr set", pg.get_attribute("body", "data-vault-idle") == "60")
    pg.wait_for_timeout(75000)
    check("idle lock closed and reloaded", not vault.is_unlocked() and pg.locator(".locked-card").count() == 1)

    # Settings while closed, export guard
    pg.goto(f"{BASE}/export/note/{P}"); pg.wait_for_timeout(300)
    check("export while closed asks to unlock", "/unlock" in pg.url, pg.url)
    pg.goto(f"{BASE}/settings#locked"); pg.wait_for_timeout(300)
    pg.locator("#locked").scroll_into_view_if_needed()
    pg.screenshot(path=f"{SHOTS}/v51_settings_closed.png")

    # dark theme look at the locked card
    pg.emulate_media(color_scheme="dark")
    pg.goto(f"{BASE}/note/{P}"); pg.wait_for_timeout(400)
    pg.screenshot(path=f"{SHOTS}/v51_locked_card_dark.png")
    check("no console errors except expected 423", all("423" in e for e in errs), errs)
    b.close()
srv.shutdown()
print(f"\n{sum(results)}/{len(results)} passed")
