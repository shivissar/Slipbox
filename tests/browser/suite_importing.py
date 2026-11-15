"""5.3 in the browser: Settings → Import notes (check, import, report,
the imported notes and their links), Settings → Trash and history
(versions kept per note, Trim now), and the bibliography's status."""
import glob
import os
import sys
import threading
import time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _harness as H  # noqa: E402
D = H.prepare("z53imp")
os.environ.update(ZETTEL_SETTINGS_PATH=f"{D}/settings.json", ZETTEL_DB_PATH=f"{D}/zettel.db",
                  ZETTEL_NOTES_DIR=f"{D}/notes", ZETTEL_EXPORT_DIR=f"{D}/exports", ZETTEL_VAULT_KDF="light")
sys.path.insert(0, H.SRC_DIR)
from Slipbox import flask_app, embed, db, history, settings, watcher, notes as N  # noqa: E402
embed.embed_note = lambda note: None
flask_app.embed.embed_note = embed.embed_note
history.INTERVAL = 0
watcher.full_resync()
from werkzeug.serving import make_server  # noqa: E402
srv = make_server("127.0.0.1", 0, flask_app.app, threaded=True)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_port}"
SH = H.SHOTS

# A small folder from "another app"
SRC = os.path.join(os.path.dirname(D), "Obsidian Vault")
os.makedirs(os.path.join(SRC, "Reading"), exist_ok=True)
os.makedirs(os.path.join(SRC, ".obsidian"), exist_ok=True)
open(os.path.join(SRC, ".obsidian", "app.md"), "w").write("settings")
open(os.path.join(SRC, "Fieldwork day one.md"), "w").write(
    "---\ntags: [fieldwork, drones]\ncreated: 2025-02-03T09:15:00\naliases: [Day one]\n---\n"
    "# Fieldwork day one\n\nMet the pilots. Compare [[Suchman on plans]] and [[Reading/Latour notes|Latour]].\n"
    "A picture: ![[launch.jpg]]. Still to write: [[Day three]].\n")
open(os.path.join(SRC, "Reading", "Suchman on plans.md"), "w").write(
    "Plans are resources for action. Back to [[Day one]].\n")
open(os.path.join(SRC, "Reading", "Latour notes.md"), "w").write(
    "# Latour notes\n\nInscriptions travel. See [the fieldwork](../Fieldwork%20day%20one.md).\n")

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))


before = len(glob.glob(f"{D}/notes/*.md"))
from playwright.sync_api import sync_playwright  # noqa: E402
with sync_playwright() as p:
    b = H.launch(p)
    errs = []
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("dialog", lambda d: d.accept())

    # Import: a folder that isn't there
    pg.goto(f"{BASE}/settings#import"); pg.wait_for_timeout(400)
    check("Settings lists Import notes", pg.locator('.settings-nav a[href="#import"]').count() == 1)
    pg.fill("#import-folder", "~/no/such/folder")
    pg.click("#import button[type=submit]"); pg.wait_for_timeout(500)
    check("missing folder explained, path kept", "can’t find a folder" in pg.inner_text("#import .flash")
          and pg.input_value("#import-folder") == "~/no/such/folder", pg.inner_text("#import"))

    # Check, then import
    pg.fill("#import-folder", SRC)
    pg.select_option("#import-kind", "teaching")
    pg.screenshot(path=f"{SH}/v53_import_settings.png")
    pg.click("#import button[type=submit]"); pg.wait_for_timeout(600)
    body = pg.inner_text("main")
    check("preview: nothing imported yet", "Nothing has been imported yet" in body
          and len(glob.glob(f"{D}/notes/*.md")) == before)
    check("preview counts", "Import 3 notes" in body and "3\nMarkdown files found" in body, body[:600])
    check("preview lists the unmatched link", "[[Day three]]" in body)
    check("preview counts the picture", "Pictures and other files" in body and "launch.jpg" in body)
    pg.screenshot(path=f"{SH}/v53_import_preview.png", full_page=True)
    pg.click("button:has-text('Import 3 notes')"); pg.wait_for_timeout(900)
    body = pg.inner_text("main")
    check("report", "Imported 3 notes" in body and "as Teaching notes" in body, body[:300])
    check("three notes written", len(glob.glob(f"{D}/notes/*.md")) == before + 3)
    check("originals untouched", sorted(os.listdir(SRC)) == [".obsidian", "Fieldwork day one.md", "Reading"])
    pg.screenshot(path=f"{SH}/v53_import_report.png", full_page=True)
    pg.reload(); pg.wait_for_timeout(500)
    check("reloading the report imports nothing", len(glob.glob(f"{D}/notes/*.md")) == before + 3)
    pg.click("a:has-text('See the imported notes')"); pg.wait_for_timeout(500)
    check("tag page lists them", "Fieldwork day one" in pg.inner_text("main") and "Latour notes" in pg.inner_text("main"))

    # An imported note and its links
    fid = next(n for n in db.notes_by_tag("imported") if n["title"] == "Fieldwork day one")["id"]
    check("ID from the note's own date", fid == "20250203091500", fid)
    pg.goto(f"{BASE}/note/{fid}"); pg.wait_for_timeout(500)
    check("title shown once", pg.locator(".rendered h1").count() == 0)
    check("links became Slipbox links", pg.locator(".rendered a.wikilink").count() == 2
          and "Latour" in pg.inner_text(".rendered"))
    check("tags and kind", "imported" in N.parse_file(glob.glob(f"{D}/notes/{fid}*.md")[0]).tags
          and N.parse_file(glob.glob(f"{D}/notes/{fid}*.md")[0]).kind == "teaching")
    pg.click(".rendered a.wikilink >> text=Suchman on plans"); pg.wait_for_timeout(500)
    check("a converted link opens the other note", "Plans are resources for action" in pg.inner_text(".rendered"))
    check("its backlink goes to the first note", "Fieldwork day one" in pg.inner_text(".rail"))

    # Versions kept per note
    pg.goto(f"{BASE}/settings#housekeeping"); pg.wait_for_timeout(400)
    check("install from before 5.3 keeps 200", pg.input_value("#history-max") == "200")
    note = N.parse_file(glob.glob(f"{D}/notes/{fid}*.md")[0])
    for i in range(12):
        note.body += "\nMore %d." % i
        N.write_note(note)
        time.sleep(0.003)
    pg.select_option("#history-max", "10"); pg.wait_for_timeout(700)
    check("choosing saves at once", settings.get("history_max") == 10 and pg.input_value("#history-max") == "10")
    msg = pg.inner_text("#housekeeping .flash")
    check("says what is beyond the limit", "now keeps up to 10 versions" in msg and "2 older versions of 1 note" in msg, msg)
    check("disk space is mentioned", "More versions use more disk space" in pg.inner_text("#housekeeping"))
    pg.screenshot(path=f"{SH}/v53_history_setting.png")
    pg.click("button:has-text('Trim now')"); pg.wait_for_timeout(600)
    check("Trim now deletes the excess", len(history.list_versions(fid)) == 10
          and "Deleted 2 older versions" in pg.inner_text("#housekeeping .flash"))
    pg.goto(f"{BASE}/note/{fid}/history"); pg.wait_for_timeout(400)
    check("history page states the limit", "keeps up to 10 of each note" in pg.inner_text("main"))

    # Bibliography status
    pg.goto(f"{BASE}/settings#bibliography"); pg.wait_for_timeout(300)
    check("references in use", "In use now:" in pg.inner_text("#bibliography"))
    os.rename(f"{D}/library.bib", f"{D}/library-renamed.bib")
    pg.reload(); pg.wait_for_timeout(300)
    check("renamed file reported", "can’t find this file" in pg.inner_text("#bibliography"))

    check("no script errors", not errs, errs[:3])
    b.close()

srv.shutdown()
print(f"{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
