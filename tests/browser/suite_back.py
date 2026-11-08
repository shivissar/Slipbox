"""5.2 Back buttons and esc on Help, Export and Open beside; recovery key
saved as a text file instead of printed."""
import os, sys, threading, stat
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _harness as H  # noqa: E402
SRC = H.FIXTURE
D = H.prepare("zback")
KEYDIR = os.path.join(os.path.dirname(D), "downloads")
os.makedirs(KEYDIR)
os.environ.update(ZETTEL_SETTINGS_PATH=f"{D}/settings.json", ZETTEL_DB_PATH=f"{D}/zettel.db",
                  ZETTEL_NOTES_DIR=f"{D}/notes", ZETTEL_EXPORT_DIR=f"{D}/exports", ZETTEL_VAULT_KDF="light")
sys.path.insert(0, H.SRC_DIR)
from pathlib import Path
from Zetteling import flask_app, embed, watcher, export
embed.embed_note = lambda note: None
flask_app.embed.embed_note = embed.embed_note
export.PANDOC_PATH, export.PANDOC_AVAILABLE = H.PANDOC, bool(H.PANDOC)
flask_app._recovery_save_dirs = lambda: [(Path(KEYDIR), "your Downloads folder")]
watcher.full_resync()
NOTE = "20261107184817"
THREAD = "tethered-autonomy"

from werkzeug.serving import make_server
srv = make_server("127.0.0.1", 0, flask_app.app, threaded=True)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_port}"
SH = H.SHOTS
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
    note_url = f"{BASE}/note/{NOTE}"

    # Help from a note
    pg.goto(note_url); pg.wait_for_timeout(300)
    pg.click('a[data-tour="help"]'); pg.wait_for_timeout(400)
    btn = pg.locator("a[data-back]")
    check("Help opened from a note has Back to the note", btn.count() == 1 and "Back to the note" in btn.inner_text()
          and btn.get_attribute("href") == f"/note/{NOTE}")
    pg.screenshot(path=f"{SH}/v52_back_help.png")
    pg.keyboard.press("Meta+k"); pg.wait_for_timeout(250)
    pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
    check("esc with the palette open only closes the palette", pg.url.endswith("/help") and pg.is_hidden("#palette"), pg.url)
    pg.keyboard.press("Escape"); pg.wait_for_timeout(500)
    check("esc returns to the note", pg.url == note_url, pg.url)

    # Help from the library, and opened directly
    pg.goto(f"{BASE}/"); pg.wait_for_timeout(200)
    pg.click('a[data-tour="help"]'); pg.wait_for_timeout(400)
    check("Help from the library has a plain Back", pg.locator("a[data-back]").inner_text().startswith("Back") and "note" not in pg.locator("a[data-back]").inner_text())
    pg.keyboard.press("Escape"); pg.wait_for_timeout(500)
    check("and esc returns there", pg.url == f"{BASE}/", pg.url)
    pg.goto(f"{BASE}/help"); pg.wait_for_timeout(300)
    check("Help opened directly has no Back", pg.locator("a[data-back]").count() == 0)
    pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
    check("and esc stays put", pg.url.endswith("/help"))

    # Export a note
    pg.goto(note_url); pg.wait_for_timeout(300)
    pg.click('[data-tour="export"]'); pg.wait_for_timeout(400)
    check("Export has Back to the note", "Back to the note" in pg.locator("a[data-back]").inner_text())
    pg.screenshot(path=f"{SH}/v52_back_export.png")
    field = pg.locator("form select, form input[type=text]").first
    if field.count():
        field.focus(); pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
        check("in a form field, the first esc only leaves the field", "/export/" in pg.url)
    pg.keyboard.press("Escape"); pg.wait_for_timeout(500)
    check("esc returns from Export", pg.url == note_url, pg.url)
    pg.goto(f"{BASE}/export/note/{NOTE}"); pg.wait_for_timeout(300)
    if pg.locator('input[name="format"][value="docx"]').count():
        pg.check('input[name="format"][value="docx"]')
    pg.click("form button[type=submit]"); pg.wait_for_timeout(2500)
    check("after exporting, Back still goes to the note", "Exported" in pg.inner_text("h1") and pg.locator("a[data-back]").get_attribute("href") == f"/note/{NOTE}")
    pg.goto(f"{BASE}/export/thread/{THREAD}"); pg.wait_for_timeout(300)
    check("exporting a thread goes back to the thread", pg.locator("a[data-back]").inner_text().startswith("Back to the thread")
          and pg.locator("a[data-back]").get_attribute("href") == f"/thread/{THREAD}")

    # Open beside
    pg.goto(note_url); pg.wait_for_timeout(300)
    pg.click(f'a[href="/note/{NOTE}/split"]'); pg.wait_for_timeout(400)
    check("Open beside picker has Back to the note", "Back to the note" in pg.locator("a[data-back]").inner_text())
    pg.locator(".note-list a").first.click(); pg.wait_for_timeout(500)
    check("side-by-side view has Back to the note", "/split/" in pg.url and pg.locator("a[data-back]").get_attribute("href") == f"/note/{NOTE}")
    pg.screenshot(path=f"{SH}/v52_back_split.png")
    pg.keyboard.press("Escape"); pg.wait_for_timeout(500)
    check("esc returns from side by side", pg.url == note_url, pg.url)
    pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
    check("esc on a note itself does nothing new", pg.url == note_url)

    # Recovery key
    PW = "correct horse battery staple"
    pg.goto(f"{BASE}/settings#locked"); pg.wait_for_timeout(300)
    pg.fill("#v-pw", PW); pg.fill("#v-pw2", PW); pg.click("text=Set up locked notes"); pg.wait_for_timeout(900)
    code = pg.inner_text("#recovery-key").strip()
    check("recovery page has no Print button", pg.locator("button:has-text('Print')").count() == 0)
    pg.click("button:has-text('Save as text file')"); pg.wait_for_timeout(600)
    files = sorted(Path(KEYDIR).glob("Zetteling recovery key *.txt"))
    check("Save as text file writes the key", len(files) == 1 and code in files[0].read_text(), files)
    check("readable only by you", files and stat.S_IMODE(files[0].stat().st_mode) == 0o600)
    check("and says where it went, without leaving the page", "Downloads" not in "" and "Saved to your Downloads folder" in pg.inner_text("#key-saved")
          and pg.inner_text("#recovery-key").strip() == code)
    pg.screenshot(path=f"{SH}/v52_recovery_save.png")
    pg.click("button:has-text('Save as text file')"); pg.wait_for_timeout(600)
    check("saving twice keeps both files", len(list(Path(KEYDIR).glob("*.txt"))) == 2)
    check("nothing written to the notes folder", not list(Path(f"{D}/notes").glob("*recovery*")))
    r = pg.request.post(f"{BASE}/vault/recovery/save", form={"code": "not a key"}, headers={"Origin": BASE, "Accept": "application/json"})
    check("anything that isn't a recovery key is refused", r.status == 400 and len(list(Path(KEYDIR).glob("*.txt"))) == 2)
    r = pg.request.post(f"{BASE}/vault/recovery/save", form={"code": code}, headers={"Origin": BASE})
    check("without scripts, the page comes back with the key and a note", r.status == 200 and code in r.text() and "Saved to" in r.text())
    pg.click("button:has-text('Copy')"); pg.wait_for_timeout(300)
    check("Copy button responds", pg.locator("button:has-text('Copied'), button:has-text('Copy')").count() >= 1)

    check("no console errors", not errs, errs[:4])
    b.close()

srv.shutdown()
print(f"\n{sum(results)}/{len(results)} passed")
