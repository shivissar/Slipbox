"""
A local-first, plain-text Zettelkasten note-taking app with AI-assisted interrogation, embeddings-based related notes, and bibliography integration.
"""

"""Verweis - Toga shell hosting the Flask app in a native window."""
import asyncio
import html
import os
import threading
import traceback

import toga
from toga.style import Pack
from toga.style.pack import COLUMN

# Shown while the notes are indexed and the local server starts (5.2: the
# window used to stay hidden, and a large library could miss a 10 s limit).
_PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
html, body {{ height: 100%; margin: 0; }}
body {{ display: flex; align-items: center; justify-content: center; text-align: center;
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif;
  background: #f4f8f5; color: #1d2b25; }}
@media (prefers-color-scheme: dark) {{ body {{ background: #141b18; color: #e3ece7; }} }}
main {{ max-width: 34em; padding: 2em; }}
h1 {{ font-size: 20px; margin: 0 0 .4em; }}
p {{ margin: 0 0 .8em; opacity: .8; }}
pre {{ text-align: left; white-space: pre-wrap; font-size: 12px; opacity: .7; max-height: 40vh; overflow: auto; }}
.dot {{ display: inline-block; width: 8px; height: 8px; margin: 0 3px; border-radius: 50%;
  background: #2fbf71; animation: b 1.2s infinite ease-in-out; }}
.dot:nth-child(2) {{ animation-delay: .15s; }} .dot:nth-child(3) {{ animation-delay: .3s; }}
@keyframes b {{ 0%, 80%, 100% {{ opacity: .2; }} 40% {{ opacity: 1; }} }}
</style></head><body><main>{body}</main></body></html>"""

_LOADING = _PAGE.format(body=(
    '<p><span class="dot"></span><span class="dot"></span><span class="dot"></span></p>'
    "<h1>Opening your notes</h1>"
    "<p>Verweis is reading your notes folder. A large library can take a little while the first time.</p>"))


def _error_page(detail):
    return _PAGE.format(body=(
        "<h1>Verweis couldn’t start</h1>"
        "<p>Something went wrong while opening your notes. Your note files have not been changed. "
        "Quit and open Verweis again; if this keeps happening, send the details below to the developer.</p>"
        "<pre>%s</pre>" % html.escape(detail)))


# Asked of the page when you quit: save what's typed, then say how it went.
_QUIT_JS = "window.__zettelQuit ? window.__zettelQuit() : 'clean'"

_QUIT_QUESTIONS = {
    "failed": ("Your last changes weren’t saved",
               "Verweis couldn’t save what you typed in the open note. Quit anyway and lose those changes?"),
    "unsaved": ("Unsaved changes",
                "The open note has changes you haven’t saved. Quit anyway and lose them?"),
    "untitled": ("This note isn’t saved yet",
                 "The new note has no title, so it hasn’t been saved. Quit anyway and lose it?"),
    "locked": ("Your last changes weren’t saved",
               "Locked notes closed before your changes to this note could be saved. "
               "Quit anyway and lose them? Choose Cancel to unlock and save."),
}


class Verweis(toga.App):
    QUIT_TIMEOUT = 8  # seconds to wait for the page to save when quitting

    def startup(self):
        data_dir = self.paths.data
        # 5.2: the app was Zetteling; bring its data across once.
        from .migrate import move_old_app_data
        try:
            if move_old_app_data(data_dir):
                os.environ["VERWEIS_RENAMED"] = "1"
        except Exception as e:  # never stop the app from opening
            print("[migrate] %s" % e)
        notes_dir = data_dir / "notes"
        exports_dir = data_dir / "exports"
        for d in (data_dir, notes_dir, exports_dir):
            d.mkdir(parents=True, exist_ok=True)

        os.environ["ZETTEL_SETTINGS_PATH"] = str(data_dir / "settings.json")
        os.environ["ZETTEL_DB_PATH"] = str(data_dir / "zettel.db")
        os.environ["ZETTEL_NOTES_DIR"] = str(notes_dir)
        os.environ["ZETTEL_EXPORT_DIR"] = str(exports_dir)

        self.host = "127.0.0.1"
        self.ready = False
        self._quitting = False

        self.webview = toga.WebView(style=Pack(flex=1))
        self.webview.set_content("about:blank", _LOADING)

        box = toga.Box(children=[self.webview], style=Pack(direction=COLUMN, flex=1))
        self.main_window = toga.MainWindow(title=self.formal_name, size=(1200, 800))
        self.main_window.content = box
        self.main_window.show()

        threading.Thread(target=self._serve, name="verweis-server", daemon=True).start()

    def _serve(self):
        """Import the Flask app (which indexes the notes folder), bind a free
        port, show the app, then serve. Runs off the main thread so the
        window stays responsive."""
        try:
            import logging

            from werkzeug.serving import make_server

            # 5.2: no request log. Each line held the address asked for,
            # including search words and note IDs, and the Mac app sends
            # console output to the system log.
            logging.getLogger("werkzeug").setLevel(logging.WARNING)

            from . import flask_app as flask_module

            server = make_server(self.host, 0, flask_module.app, threaded=True)
        except Exception:
            detail = traceback.format_exc()
            print(detail)
            self.loop.call_soon_threadsafe(self.webview.set_content, "about:blank", _error_page(detail))
            return
        self.port = server.server_port
        self.loop.call_soon_threadsafe(self._open, f"http://{self.host}:{self.port}/")
        server.serve_forever()

    def _open(self, url):
        self.ready = True
        self.webview.url = url

    async def on_exit(self):
        """Save what's typed in the open note before quitting (5.2: ⌘Q
        could lose the last second or two of typing, because the page's
        own save-on-leave doesn't run when the app quits)."""
        if self._quitting:
            return False
        if not self.ready:
            return True
        self._quitting = True
        try:
            try:
                state = await asyncio.wait_for(self._ask_page(), timeout=self.QUIT_TIMEOUT)
            except Exception as e:  # page gone, script error or timeout
                print("[quit] couldn't ask the page to save: %s" % e)
                state = "failed"
            question = _QUIT_QUESTIONS.get(str(state))
            if question is None:
                return True
            return bool(await self.main_window.dialog(toga.ConfirmDialog(*question)))
        except Exception as e:  # never trap you in the app
            print("[quit] %s" % e)
            return True
        finally:
            self._quitting = False

    async def _ask_page(self):
        return await self.webview.evaluate_javascript(_QUIT_JS)


def main():
    return Verweis()
