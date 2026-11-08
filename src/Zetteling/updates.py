"""Update check (5.2).

Once a day, if the user hasn't turned it off, ask GitHub for the latest
published release of Zetteling and remember the answer in settings. The
app then shows a notice and a page with the release notes and a link that
opens in the browser. Nothing is ever downloaded or installed from here:
the user installs the new DMG themselves, so Gatekeeper checks it as usual.

The request goes to GitHub's REST API without credentials; it carries the
user's IP address (unavoidably) and a User-Agent of "Zetteling/<version>",
and nothing else. GitHub's "latest release" is the most recent release
that is neither a draft nor a pre-release.
"""
import json
import platform
import re
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime, timezone

from . import settings

REPO = "shivissar/Zetteling"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
INTERVAL = 24 * 3600     # between successful checks
RETRY = 3 * 3600         # after a failed one (offline, rate-limited)
TIMEOUT = 8
NOTES_LIMIT = 6000

_VERSION_RE = re.compile(r"^[vV]?(\d+)\.(\d+)(?:\.(\d+))?$")
_RUNNING = threading.Lock()


class UpdateError(Exception):
    """A check that didn't get an answer; the message is shown to the user."""


def parse_version(text):
    """'v5.2.0' or '5.2' -> (5, 2, 0); anything else (5.3.0-beta) -> None."""
    m = _VERSION_RE.match((text or "").strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def safe_url(url):
    """Only ever open pages and downloads from this project's GitHub repo."""
    if not isinstance(url, str):
        return None
    prefix = f"https://github.com/{REPO}/".lower()
    if url.lower().startswith(prefix) and not any(c in url for c in ' "<>\\\n\r\t'):
        return url
    return None


def _ssl_context():
    # Briefcase's Python doesn't always find the macOS root certificates;
    # certifi (already installed for the AI libraries) always works.
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def fetch_latest(current_version):
    """Ask GitHub for the latest release. Returns a dict, or raises UpdateError."""
    req = urllib.request.Request(API_URL, headers={
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": f"Zetteling/{current_version}",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ssl_context()) as r:
            data = json.loads(r.read(2_000_000).decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError("GitHub has no published release of Zetteling yet.")
        if e.code in (403, 429):
            raise UpdateError("GitHub asked Zetteling to wait before checking again. It will try later.")
        raise UpdateError(f"GitHub answered with an error ({e.code}). Zetteling will try again later.")
    except (urllib.error.URLError, TimeoutError, OSError):
        raise UpdateError("Couldn’t reach GitHub. Check that you’re online; Zetteling will try again later.")
    except ValueError:
        raise UpdateError("GitHub’s answer couldn’t be read. Zetteling will try again later.")

    if not isinstance(data, dict):
        raise UpdateError("GitHub’s answer couldn’t be read. Zetteling will try again later.")
    tag = str(data.get("tag_name") or "")
    version = parse_version(tag)
    if not version:
        raise UpdateError(f"The latest release ({tag[:40] or 'untitled'}) has no version number Zetteling understands.")
    dmg = ""
    for asset in data.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "")
        url = safe_url(asset.get("browser_download_url"))
        if name.lower().endswith(".dmg") and url:
            dmg = url
            break
    notes = str(data.get("body") or "").replace("\r\n", "\n").strip()
    if len(notes) > NOTES_LIMIT:
        notes = notes[:NOTES_LIMIT].rstrip() + "\n\n…"
    return {
        "version": ".".join(str(n) for n in version),
        "tag": tag[:40],
        "name": str(data.get("name") or "")[:120],
        "published": str(data.get("published_at") or "")[:10],
        "notes": notes,
        "url": safe_url(data.get("html_url")) or RELEASES_PAGE,
        "dmg_url": dmg,
    }


def _now():
    return time.time()


def due(s=None):
    s = s or settings.load()
    return bool(s.get("update_check", True)) and _now() >= float(s.get("update_next_check") or 0)


def check(current_version, force=False):
    """Run a check if one is due (or forced) and store the result.
    Returns status(). Never raises."""
    if not force and not due():
        return status(current_version)
    if not _RUNNING.acquire(blocking=False):
        return status(current_version)
    try:
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            latest = fetch_latest(current_version)
        except UpdateError as e:
            settings.save({"update_error": str(e), "update_attempt_at": stamp,
                           "update_next_check": _now() + RETRY})
        else:
            settings.save({"update_latest": latest, "update_error": "",
                           "update_checked_at": stamp, "update_attempt_at": stamp,
                           "update_next_check": _now() + INTERVAL})
    finally:
        _RUNNING.release()
    return status(current_version)


def check_in_background(current_version):
    """Start a check on a background thread if one is due. Cheap when not."""
    if not due() or _RUNNING.locked():
        return False
    threading.Thread(target=check, args=(current_version,), daemon=True,
                     name="zetteling-update-check").start()
    return True


def status(current_version, s=None):
    s = s or settings.load()
    latest = s.get("update_latest") or {}
    if not isinstance(latest, dict):
        latest = {}
    lv, cv = parse_version(latest.get("version", "")), parse_version(current_version)
    newer = bool(lv and cv and lv > cv)
    skipped = newer and latest.get("version") == s.get("update_skip")
    return {
        "enabled": bool(s.get("update_check", True)),
        "current": current_version,
        "latest": latest if lv else None,
        "newer": newer,
        "skipped": skipped,
        "notice": newer and not skipped and bool(s.get("update_check", True)),
        "checked_at": s.get("update_checked_at") or "",
        "attempt_at": s.get("update_attempt_at") or "",
        "error": s.get("update_error") or "",
        "releases_page": RELEASES_PAGE,
    }


def open_external(url):
    """Open a GitHub page or download in the user's browser (not in the app
    window). Returns False for anything that isn't this project's GitHub."""
    url = safe_url(url)
    if not url:
        return False
    if platform.system() == "Darwin":
        subprocess.run(["open", url], check=False)
    else:
        webbrowser.open(url)
    return True
