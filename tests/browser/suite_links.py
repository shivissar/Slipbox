"""5.2 outside links: links to other websites open in the browser (via
/open-link), not in the app window; Zetteling's own links behave as before;
the credits link to Wikipedia and shivissar.com."""
import os, sys, threading, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _harness as H  # noqa: E402
SRC = H.FIXTURE
D = H.prepare("zlinks")
NOTE = """---
created: '2026-11-08T10:00:00+05:30'
id: '20261108100000'
kind: research
modified: '2026-11-08T10:00:00+05:30'
refs: []
tags: []
threads: []
title: Links to elsewhere
---

Read [Luhmann on slip boxes](https://en.wikipedia.org/wiki/Niklas_Luhmann), write to
[the author](mailto:someone@example.com), see [Help](/help), or [run this](javascript:alert(1)).
"""
open(f"{D}/notes/20261108100000-links-to-elsewhere.md", "w").write(NOTE)
os.environ.update(ZETTEL_SETTINGS_PATH=f"{D}/settings.json", ZETTEL_DB_PATH=f"{D}/zettel.db",
                  ZETTEL_NOTES_DIR=f"{D}/notes", ZETTEL_EXPORT_DIR=f"{D}/exports", ZETTEL_VAULT_KDF="light")
sys.path.insert(0, H.SRC_DIR)
from Zetteling import flask_app, embed, watcher, updates
embed.embed_note = lambda note: None
flask_app.embed.embed_note = embed.embed_note
watcher.full_resync()
opened = []
updates.webbrowser.open = lambda url: opened.append(url) or True

from werkzeug.serving import make_server
srv = make_server("127.0.0.1", 0, flask_app.app, threaded=True)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_port}"
results = []
def check(name, ok, detail=""):
    results.append(bool(ok)); print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))

from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = H.launch(p)
    errs = []
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" and "alert" not in m.text else None)
    pg.on("pageerror", lambda e: errs.append(str(e)))
    dialogs = []
    pg.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))

    pg.goto(f"{BASE}/note/20261108100000"); pg.wait_for_timeout(400)
    here = pg.url
    pg.click("article a:has-text('Luhmann on slip boxes')"); pg.wait_for_timeout(500)
    check("web link in a note opens in the browser", opened[-1:] == ["https://en.wikipedia.org/wiki/Niklas_Luhmann"], opened)
    check("and the app window stays put", pg.url == here, pg.url)
    check("with a short confirmation", "browser" in pg.inner_text("#toast"))
    pg.click("article a:has-text('the author')"); pg.wait_for_timeout(400)
    check("mail link goes to the mail app", opened[-1] == "mailto:someone@example.com" and pg.url == here, opened)
    n = len(opened)
    pg.click("article a:has-text('run this')", no_wait_after=True); pg.wait_for_timeout(400)
    check("script links are never sent out", len(opened) == n)
    pg.click("article a:has-text('Help')"); pg.wait_for_timeout(500)
    check("Zetteling's own links still work in the window", pg.url.endswith("/help") and len(opened) == n, pg.url)

    # Middle-click, and links with target=_blank (Settings → AI)
    pg.goto(f"{BASE}/settings#ai"); pg.wait_for_timeout(400)
    pg.select_option("select[name=ai_chat_provider]", "anthropic"); pg.wait_for_timeout(300)
    blank = pg.locator("#ai-fields-anthropic a[target=_blank]").first
    href = blank.get_attribute("href")
    blank.click(); pg.wait_for_timeout(500)
    check("new-window links open in the browser", opened[-1] == href and len(pg.context.pages) == 1, (href, opened[-1:]))

    # Credits
    pg.goto(f"{BASE}/settings#about"); pg.wait_for_timeout(300)
    links = pg.eval_on_selector_all("#about a", "els => els.map(a => [a.textContent.trim(), a.href])")
    want = {"Shiv Issar, Ph.D.": "https://www.shivissar.com/", "Niklas Luhmann": "https://en.wikipedia.org/wiki/Niklas_Luhmann",
            "Aneesh Aneesh": "https://en.wikipedia.org/wiki/A._Aneesh"}
    check("settings credits link all three names", all([k, v] in [[t, h] for t, h in links] for k, v in want.items()), links)
    pg.click("#about a:has-text('Shiv Issar')"); pg.wait_for_timeout(400)
    check("and they open in the browser", opened[-1] == "https://www.shivissar.com/")
    pg.goto(f"{BASE}/about"); pg.wait_for_timeout(300)
    ab = pg.eval_on_selector_all("article a", "els => els.map(a => [a.textContent.trim(), a.href])")
    check("About acknowledgements link Luhmann and Aneesh", ["Niklas Luhmann", want["Niklas Luhmann"]] in ab and ["Aneesh Aneesh", want["Aneesh Aneesh"]] in ab)
    pg.goto(f"{BASE}/help"); pg.wait_for_timeout(300)
    hl = pg.eval_on_selector_all("a[href*='Niklas_Luhmann']", "els => els.length")
    check("every Luhmann mention in Help is linked", hl == 3, hl)
    pg.goto(f"{BASE}/license"); pg.wait_for_timeout(300)
    check("licence page links the author", pg.locator("a[href='https://www.shivissar.com']").count() == 1)

    # The endpoint refuses what isn't an outside web or mail link.
    for bad in ["file:///etc/passwd", "javascript:alert(1)", "zotero://select", f"{BASE}/note/x", "https://", "x" * 5000]:
        r = pg.request.post(f"{BASE}/open-link", data=json.dumps({"url": bad}), headers={"Content-Type": "application/json", "Origin": BASE})
        if r.status != 400:
            check(f"refuses {bad[:30]}", False, r.status)
    check("endpoint refuses files, scripts, app schemes and its own pages", all(not u.startswith(("file:", "javascript:", "zotero:", BASE)) for u in opened))
    r = pg.request.post(f"{BASE}/open-link", data=json.dumps({"url": "https://example.com"}), headers={"Content-Type": "application/json", "Origin": "https://evil.example"})
    check("other websites can't use it", r.status == 403 and opened[-1] != "https://example.com")
    # (javascript: links in notes behave as before: notes are trusted unless
    #  Settings → sanitise note HTML is on. They are never sent to the browser.)
    check("no console errors", not errs, errs[:4])
    b.close()

srv.shutdown()
print(f"\n{sum(results)}/{len(results)} passed")
