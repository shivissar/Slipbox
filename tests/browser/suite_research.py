"""5.2 in the browser: help contents, templates, coding, codebook, history,
mentions, manuscripts and export, search filters."""
import os, sys, threading, glob, zipfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _harness as H  # noqa: E402
SRC = H.FIXTURE
D = H.prepare("z52ui")
os.environ.update(ZETTEL_SETTINGS_PATH=f"{D}/settings.json", ZETTEL_DB_PATH=f"{D}/zettel.db",
                  ZETTEL_NOTES_DIR=f"{D}/notes", ZETTEL_EXPORT_DIR=f"{D}/exports", ZETTEL_VAULT_KDF="light")
sys.path.insert(0, H.SRC_DIR)
from Slipbox import flask_app, embed, settings, watcher, history, export, notes as N
export.PANDOC_PATH, export.PANDOC_AVAILABLE = H.PANDOC, bool(H.PANDOC)  # the repo's pandoc is the macOS binary
embed.embed_note = lambda note: None
flask_app.embed.embed_note = embed.embed_note
history.INTERVAL = 0  # a version on every save, for the test
settings.save({"autosave": True})
watcher.full_resync()
from werkzeug.serving import make_server
srv = make_server("127.0.0.1", 0, flask_app.app, threaded=True)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_port}"
P = open(f"{SRC}/plans_id.txt").read().strip()
T = "20261107184818"  # Tethered autonomy — working definition
SH = H.SHOTS
def body(nid):
    return N.parse_file(glob.glob(f"{D}/notes/{nid}*.md")[0]).body
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

    # Help contents
    pg.goto(f"{BASE}/help"); pg.wait_for_timeout(400)
    check("help contents sidebar", pg.locator(".help-toc a").count() > 30)
    pg.hover('.help-toc a[href="#research-tools"]'); pg.wait_for_timeout(200)
    pg.click('.help-toc a[href="#coding"]'); pg.wait_for_timeout(600)
    pg.mouse.move(900, 500); pg.wait_for_timeout(200)
    check("contents follows your place", pg.locator('.help-toc a.on[href="#coding"]').count() == 1)
    check("only that section's sub-items open", pg.locator(".help-toc li.open").count() == 1)
    pg.screenshot(path=f"{SH}/v52_help.png")

    # Template
    pg.goto(f"{BASE}/new"); pg.wait_for_timeout(300)
    pg.click('.template-row a:has-text("Interview memo")'); pg.wait_for_timeout(400)
    check("template fills title", pg.input_value("#title-input").startswith("Interview memo:"))
    check("template fills body", "## Context" in pg.input_value("#body"))
    pg.fill("#title-input", "Interview memo: Priya, ground station")
    pg.click("#body"); pg.keyboard.press("Control+End"); pg.keyboard.press("Meta+ArrowDown")
    pg.evaluate("""() => { const t = document.querySelector('#body'); t.value += '\\nShe said she would rather trust the autopilot than her own eyes at dusk.'; t.dispatchEvent(new Event('input', {bubbles: true})); }""")
    pg.wait_for_timeout(2300)
    nid = pg.url.split("/note/")[1].split("/")[0] if "/note/" in pg.url else ""
    check("templated note saved", nid and "interview" in N.parse_file(glob.glob(f"{D}/notes/{nid}*.md")[0]).tags, pg.url)

    # Coding with ⇧⌘C
    pg.evaluate("""() => { const t = document.querySelector('#body'); const s = t.value.indexOf('trust the autopilot');
                     t.focus(); t.setSelectionRange(s, s + 'trust the autopilot'.length); }""")
    pg.keyboard.press("Meta+Shift+c"); pg.wait_for_timeout(400)
    check("code picker opens", pg.is_visible("#code-picker"))
    pg.keyboard.type("trust, Automation"); pg.wait_for_timeout(300)
    pg.screenshot(path=f"{SH}/v52_code_picker.png")
    pg.keyboard.press("Enter"); pg.wait_for_timeout(2300)
    check("passage coded in file", "[trust the autopilot]{.trust .automation}" in body(nid), body(nid)[-120:])
    pg.click("[data-cancel]"); pg.wait_for_timeout(600)
    check("coded passage highlighted", pg.locator(".qcode mark").count() == 1 and pg.locator(".qcode-tag").count() == 2)
    pg.goto(f"{BASE}/codes"); pg.wait_for_timeout(300)
    check("codebook lists codes", pg.locator(".codebook-table a.qcode-tag").count() == 2)
    pg.click('.codebook-table a:has-text("trust")'); pg.wait_for_timeout(300)
    pg.fill("#def", "Relying on the system rather than checking it."); pg.fill("#cat", "Relations to automation")
    pg.click('form.code-def button'); pg.wait_for_timeout(400)
    check("definition saved", "Relying on the system" in pg.input_value("#def") and "saved" in pg.inner_text(".flash").lower())
    check("co-occurrence shown", "automation" in pg.inner_text(".cooc"))
    pg.screenshot(path=f"{SH}/v52_code_page.png", full_page=False)
    pg.fill('input[name="new_name"]', "reliance"); pg.click('form[action$="/rename"] button'); pg.wait_for_timeout(600)
    check("rename rewrites notes", "{.reliance .automation}" in body(nid) and "/codes/reliance" in pg.url)
    pg.goto(f"{BASE}/codes"); pg.wait_for_timeout(300)
    pg.screenshot(path=f"{SH}/v52_codebook.png")
    # 5.2 audit: the CSV is saved to the export folder (the app window can't download)
    flask_app._reveal = lambda path: (True, "ok")
    pg.click('button:has-text("Export passages (CSV)")'); pg.wait_for_timeout(500)
    csvs = glob.glob(f"{D}/exports/*-slipbox-codebook.csv")
    check("codebook CSV saved to export folder", len(csvs) == 1 and "/codes" in pg.url
          and os.path.basename(csvs[0]) in pg.inner_text(".flash.success"), (csvs, pg.url))

    # History
    pg.goto(f"{BASE}/note/{nid}/history"); pg.wait_for_timeout(400)
    n_versions = pg.locator(".history-item").count()
    check("history lists versions", n_versions >= 2, n_versions)
    pg.locator(".history-item").last.click(); pg.wait_for_timeout(400)
    check("diff shown", pg.locator(".dl-add, .dl-del").count() > 0)
    pg.screenshot(path=f"{SH}/v52_history.png")
    pg.click('.history-actions button'); pg.wait_for_timeout(600)
    check("restore brings back old text", "{.reliance" not in body(nid) and "restored" in pg.url)

    # Unlinked mentions
    pg.goto(f"{BASE}/new"); pg.wait_for_timeout(300)
    pg.fill("#title-input", "Mention probe"); pg.click("#body")
    pg.keyboard.type("Operators describe tethered autonomy as a bargain.")
    pg.wait_for_timeout(2300)
    probe = pg.url.split("/note/")[1].split("/")[0]
    pg.goto(f"{BASE}/note/{T}"); pg.wait_for_timeout(400)
    check("mention listed", pg.locator("#mentions .mention").count() >= 1)
    check("backlink context shown", pg.locator(".rl-context").count() >= 1)
    pg.locator(f'#mentions form:has(input[value="{probe}"]) button').click(); pg.wait_for_timeout(500)
    check("mention linked", f"[[{T}|tethered autonomy]]" in body(probe), body(probe))

    # Manuscript
    pg.goto(f"{BASE}/manuscripts"); pg.wait_for_timeout(300)
    pg.click('form[action="/manuscripts/new"] button'); pg.wait_for_timeout(600)
    mid = pg.url.split("/note/")[1].split("/")[0]
    pg.fill("#title-input", "Tethered autonomy: draft"); pg.wait_for_timeout(200)
    pg.click("[data-cancel]"); pg.wait_for_timeout(700)
    pg.click('#outline form:has(input[value="add_heading"]) input[name="heading"]'); pg.keyboard.type("Argument")
    pg.keyboard.press("Enter"); pg.wait_for_timeout(500)
    pg.select_option('#outline select[name="thread"]', "tethered-autonomy")
    pg.click('#outline form:has(input[value="add_thread"]) button'); pg.wait_for_timeout(600)
    check("thread added to manuscript", pg.locator(".embed").count() == 4, pg.locator(".embed").count())
    check("outline lists notes", pg.locator(".outline .ol-note").count() == 4)
    pg.locator(".outline .ol-note").nth(2).locator('button[aria-label="Move up"]').click(); pg.wait_for_timeout(500)
    check("reorder works", pg.locator(".outline .ol-note").count() == 4)
    pg.screenshot(path=f"{SH}/v52_manuscript.png")
    pg.click('#outline a:has-text("Export manuscript")'); pg.wait_for_timeout(400)
    check("export offers heading option", pg.locator('input[name="embed_titles"]').count() == 1)
    pg.check('input[name="format"][value="docx"]') if pg.locator('input[name="format"][value="docx"]').count() else pg.select_option('select[name="format"]', "docx")
    pg.click('button:has-text("Export to file")'); pg.wait_for_timeout(2500)
    files = sorted(glob.glob(f"{D}/exports/*.docx"), key=os.path.getmtime)
    ok = False
    if files:
        xml = zipfile.ZipFile(files[-1]).read("word/document.xml").decode("utf-8", "ignore")
        ok = "[[" not in xml and "Suchman" in xml and "Autonomy as a negotiated distance" in xml
    check("manuscript exports as one docx without raw links", ok, files)

    # Search filters
    pg.goto(f"{BASE}/search?q=kind%3Ajournal"); pg.wait_for_timeout(400)
    check("filter description shown", "kind is journal" in pg.inner_text("body"))
    pg.keyboard.press("Meta+k"); pg.wait_for_timeout(300); pg.keyboard.type("tag:situated-action"); pg.wait_for_timeout(700)
    pal = pg.inner_text("#palette") if pg.locator("#palette").count() else pg.inner_text("body")
    check("palette honours filters", "Plans are resources" in pal and "Garden" not in pal)
    pg.keyboard.press("Escape")

    check("no console errors", not errs, errs)
    b.close()
srv.shutdown()
print(f"\n{sum(results)}/{len(results)} passed")
