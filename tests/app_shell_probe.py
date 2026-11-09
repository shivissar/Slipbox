"""Run by test_app_shell.py in a fresh process: starts the Toga shell on
Toga's dummy backend, waits for the server, then checks quitting."""
import asyncio
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

mode = sys.argv[1]
os.environ["TOGA_BACKEND"] = "toga_dummy"
os.environ["TOGA_DUMMY_HOME"] = tempfile.mkdtemp(prefix="zshell-")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

if mode == "broken":
    import werkzeug.serving

    def _refuse(*a, **k):
        raise OSError("no ports for you")
    werkzeug.serving.make_server = _refuse

from Slipbox import app as shell  # noqa: E402

a = shell.Slipbox(formal_name="Slipbox", app_id="org.example.slipbox")
pages = []
a.webview.set_content = lambda root, content: pages.append(content)
assert not a.ready

if mode == "about":
    # Not a Mac: Toga's own About. On a Mac: the panel shows the logo with its
    # ™ sign and Toga's other lines; if it can't be built, Toga's own again.
    import types
    import toga
    calls = []
    a._impl.show_about_dialog = lambda: calls.append("toga")
    a.about()
    assert calls == ["toga"], calls

    class Options(dict):
        @classmethod
        def alloc(cls):
            return cls()

        def init(self):
            return self
    keys = ("NSAboutPanelOptionApplicationIcon", "NSAboutPanelOptionApplicationName",
            "NSAboutPanelOptionApplicationVersion", "NSAboutPanelOptionVersion")
    libs = types.ModuleType("toga_cocoa.libs")
    for k in keys:
        setattr(libs, k, k)
    libs.NSMutableDictionary = Options
    sys.modules["toga_cocoa"] = types.ModuleType("toga_cocoa")
    sys.modules["toga_cocoa.libs"] = libs
    shown = []
    a._impl.native = types.SimpleNamespace(orderFrontStandardAboutPanelWithOptions=shown.append)
    toga.platform.current_platform = "macOS"
    a.about()
    assert calls == ["toga"] and len(shown) == 1, (calls, shown)
    opts = shown[0]
    assert opts["NSAboutPanelOptionApplicationName"] == "Slipbox"
    assert opts["NSAboutPanelOptionApplicationVersion"] == (a.version or "0.0")
    assert opts["NSAboutPanelOptionVersion"] == "1" and "Copyright" in opts
    logo = opts["NSAboutPanelOptionApplicationIcon"].raw.convert("RGBA")
    assert logo.size == (512, 512), logo.size
    # The ™: grey ink just outside the top-right corner of the icon body.
    corner = [logo.getpixel((x, y)) for y in range(60, 120) for x in range(400, 512)]
    tm = [px for px in corner
          if px[3] > 200 and all(abs(c - g) < 30 for c, g in zip(px, (0x77, 0x7C, 0x85)))]
    assert len(tm) > 100, len(tm)

    del sys.modules["toga_cocoa.libs"]
    a.about()
    assert calls == ["toga", "toga"] and len(shown) == 1, (calls, shown)
    print("OK about")
    sys.exit(0)


async def wait(cond, secs=30):
    for _ in range(int(secs / 0.05)):
        if cond():
            return True
        await asyncio.sleep(0.05)
    return False


async def quit_with(answer, dialog=None):
    if dialog is not None:
        a.main_window._impl.dialog_responses = {"ConfirmDialog": [dialog]}
    task = asyncio.ensure_future(a.on_exit(a))
    await asyncio.sleep(0.05)
    if answer is not None:
        a.webview._impl._js_result.set_result(answer)
    return await task


async def main():
    if mode == "broken":
        await wait(lambda: pages, 10)
        assert pages and "couldn’t start" in pages[-1] and "no ports for you" in pages[-1], pages
        assert not a.ready
        assert await quit_with(None) is True          # nothing to save, just quit
        print("OK broken")
        return
    assert await wait(lambda: a.ready), "server never came up"
    url = a.webview.url
    assert url.startswith("http://127.0.0.1:") and str(a.port) in url, url
    with urllib.request.urlopen(url) as r:
        assert r.status == 200
    assert await quit_with("clean") is True
    assert await quit_with("saved") is True
    assert await quit_with("failed", dialog=False) is False
    assert await quit_with("failed", dialog=True) is True
    assert await quit_with("untitled", dialog=False) is False
    a.QUIT_TIMEOUT = 0.3
    assert await quit_with(None, dialog=False) is False   # page didn't answer: ask
    print("OK normal")

a.loop.run_until_complete(main())
