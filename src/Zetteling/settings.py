"""
settings.py — Persistent user settings stored in a JSON file.

The settings file lives next to zettel.db (or wherever ZETTEL_SETTINGS_PATH
points). For folder and file paths, a value saved through the UI wins;
environment variables are a fallback (useful for Docker or server
deployments) and a built-in default comes last. v5.0 made the export
folder and bibliography path follow the same rule the notes folder
already followed.
"""

# Copyright (c) 2026 Shiv Issar
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import json
import os
import threading
from pathlib import Path

DEFAULT_PATH = Path(__file__).parent / "settings.json"
SETTINGS_PATH = Path(os.environ.get("ZETTEL_SETTINGS_PATH", DEFAULT_PATH))

# Schema with defaults. Add new keys here when adding settings.
DEFAULTS = {
    "bib_path": "",
    "notes_dir": "",
    "export_dir": "",
    "ai_chat_provider": "",
    "ai_chat_model": "",
    "ai_chat_api_key": "",
    "ai_chat_base_url": "",
    "ai_embed_provider": "",
    "ai_embed_model": "",
    "ai_embed_api_key": "",
    "ai_embed_base_url": "",
    # HTML sanitisation of rendered notes (v3.17; on by default since 5.2,
    # when existing installs were switched on once: html_safety_applied).
    "sanitize_note_html": True,
    "html_safety_applied": False,
    # v5.0.1: save the note being edited a moment after typing stops.
    "autosave": True,
    # v5.1: locked notes. Kinds whose notes are always locked, and minutes
    # of inactivity before locked notes close again (0 = only on quit).
    "locked_kinds": [],
    # 5.2: notes of an always-locked kind whose lock the person removed on
    # purpose; the sweep that locks plain-text notes of those kinds skips them.
    "lock_exempt": [],
    # v5.2: the welcome tour has been shown (it starts by itself once)
    "tour_seen": False,
    # v5.2: daily check for a newer release on GitHub (updates.py). The
    # rest is the remembered answer; update_skip is a version the user
    # chose to skip.
    "update_check": True,
    "update_next_check": 0,
    "update_checked_at": "",
    "update_attempt_at": "",
    "update_latest": {},
    "update_error": "",
    "update_skip": "",
    "lock_after_minutes": 15,
    # v5.0: appearance. Allowed values live in themes.py.
    "theme_mode": "auto",
    "theme_light": "meadow",
    "theme_dark": "starlit",
    "accent_color": "",
    "ui_font": "nunito",
    "reading_font": "nunito",
    "text_size": "m",
    "illustrations": True,
    # v5.0: set once the first-run welcome screen has been completed or
    # skipped. Installs that already have notes never see it.
    "welcomed": False,
}


# 5.2: a settings file that can't be read used to load as defaults, and the
# next save overwrote it, losing folders and API keys. Now every save also
# writes settings.json.bak; an unreadable file is read from that copy
# instead, kept aside on the next save, and reported in the app.
PROBLEM = {}       # the file on disk can't be read right now
RECOVERED = {}     # what happened at the last such save, for the notice


def problem_notice():
    """For the app: None, or {"error", "from_backup", "kept"} (kept is the
    name the unreadable file was given, once a save has set it aside)."""
    folder = str(SETTINGS_PATH.parent)
    if PROBLEM:
        return {"error": PROBLEM["error"], "from_backup": PROBLEM["from_backup"], "kept": "", "folder": folder}
    return dict(RECOVERED, folder=folder) if RECOVERED else None


def _backup_path():
    return SETTINGS_PATH.with_name(SETTINGS_PATH.name + ".bak")


def _read(path):
    """The stored dict, None if there's no file, or raises ValueError."""
    try:
        with open(path, encoding="utf-8") as f:
            stored = json.load(f)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        raise ValueError(str(e).splitlines()[0] if str(e) else type(e).__name__)
    if not isinstance(stored, dict):
        raise ValueError("it doesn’t hold a set of settings")
    return stored


def load():
    """Return the merged settings dict (defaults + on-disk overrides)."""
    out = dict(DEFAULTS)
    try:
        stored = _read(SETTINGS_PATH)
        PROBLEM.clear()
    except ValueError as e:
        stored = None
        try:
            stored = _read(_backup_path())
        except ValueError:
            pass
        info = {"error": str(e), "from_backup": stored is not None}
        if PROBLEM.get("error") != info["error"]:
            print(f"[settings] could not read {SETTINGS_PATH}: {e}"
                  + ("; using settings.json.bak" if stored is not None else "; using defaults"))
        PROBLEM.update(info)
    if stored:
        out.update({k: stored[k] for k in stored if k in DEFAULTS})
    return out


def _write_json(path, data):
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


_SAVE_LOCK = threading.RLock()


def save(updates):
    """Merge updates into the on-disk settings file. Returns the new dict.

    5.2: saves are serialised and the file is replaced in one step, so a
    background save (the update check) can't interleave with one from a
    page, and a reader never sees a half-written file."""
    with _SAVE_LOCK:
        current = load()
        current.update({k: v for k, v in updates.items() if k in DEFAULTS})
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        if PROBLEM:
            # Keep the unreadable file for the person to look at.
            from datetime import datetime
            kept = SETTINGS_PATH.with_name("%s.unreadable-%s" % (
                SETTINGS_PATH.name, datetime.now().strftime("%Y%m%d-%H%M%S")))
            try:
                os.replace(SETTINGS_PATH, kept)
                print(f"[settings] kept the unreadable file as {kept.name}")
            except OSError as e:
                print(f"[settings] couldn't keep the unreadable file: {e}")
                kept = None
            RECOVERED.clear()
            RECOVERED.update(error=PROBLEM["error"], from_backup=PROBLEM["from_backup"],
                             kept=kept.name if kept else "")
            PROBLEM.clear()
        _write_json(SETTINGS_PATH, current)
        try:
            _write_json(_backup_path(), current)
        except OSError as e:
            print(f"[settings] couldn't write the backup copy: {e}")
        return current


def get(key, default=None):
    return load().get(key, default if default is not None else DEFAULTS.get(key))


def get_notes_dir():
    """Resolve the notes directory: UI-saved value wins, then env var, then ./notes."""
    stored = get("notes_dir", "").strip()
    if stored:
        return stored
    env = os.environ.get("ZETTEL_NOTES_DIR", "").strip()
    if env:
        return env
    return str(Path(__file__).parent / "notes")


def get_export_dir():
    """Resolve the export directory: UI-saved value wins, then env var,
    then ~/Downloads/zetteling-exports. Before v5.0 the env var won, and
    the desktop app always sets it, so a folder chosen in the UI was
    silently ignored."""
    stored = get("export_dir", "").strip()
    if stored:
        return str(Path(stored).expanduser())
    env = os.environ.get("ZETTEL_EXPORT_DIR", "").strip()
    if env:
        return env
    return str(Path.home() / "Downloads" / "zetteling-exports")


def get_bib_path():
    """Resolve the bib path: UI-saved value wins, then env var, then None."""
    stored = get("bib_path", "").strip()
    if stored:
        return str(Path(stored).expanduser())
    env = os.environ.get("ZETTEL_BIB_PATH", "").strip()
    return env or None
