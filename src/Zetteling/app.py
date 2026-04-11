"""
A local-first, plain-text Zettelkasten note-taking app with AI-assisted interrogation, embeddings-based related notes, and bibliography integration.
"""

"""Zetteling - Toga shell hosting the Flask app in a native window."""
import os
import socket
import threading
import time

import toga
from toga.style import Pack
from toga.style.pack import COLUMN


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_server(host: str, port: int, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


class Zetteling(toga.App):
    def startup(self):
        data_dir = self.paths.data
        notes_dir = data_dir / "notes"
        exports_dir = data_dir / "exports"
        for d in (data_dir, notes_dir, exports_dir):
            d.mkdir(parents=True, exist_ok=True)

        os.environ["ZETTEL_SETTINGS_PATH"] = str(data_dir / "settings.json")
        os.environ["ZETTEL_DB_PATH"] = str(data_dir / "zettel.db")
        os.environ["ZETTEL_NOTES_DIR"] = str(notes_dir)
        os.environ["ZETTEL_EXPORT_DIR"] = str(exports_dir)        

        from . import flask_app as flask_module

        self.host = "127.0.0.1"
        self.port = _find_free_port()

        def run_flask():
            flask_module.app.run(
                host=self.host,
                port=self.port,
                debug=False,
                use_reloader=False,
                threaded=True,
            )

        threading.Thread(target=run_flask, daemon=True).start()

        if not _wait_for_server(self.host, self.port, timeout=10.0):
            raise RuntimeError("Flask server failed to start within 10 seconds")

        self.webview = toga.WebView(style=Pack(flex=1))
        self.webview.url = f"http://{self.host}:{self.port}/"

        box = toga.Box(children=[self.webview], style=Pack(direction=COLUMN, flex=1))
        self.main_window = toga.MainWindow(title=self.formal_name, size=(1200, 800))
        self.main_window.content = box
        self.main_window.show()


def main():
    return Zetteling()
