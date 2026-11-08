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
    # v3.17: HTML sanitization of rendered markdown. Default False preserves
    # backward compatibility (permissive inline HTML). Set to True when
    # importing notes from an untrusted source. Requires the 'bleach' package.
    "sanitize_note_html": False,
    # v5.0.1: save the note being edited a moment after typing stops.
    "autosave": True,
    # v5.1: locked notes. Kinds whose notes are always locked, and minutes
    # of inactivity before locked notes close again (0 = only on quit).
    "locked_kinds": [],
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


def load():
    """Return the merged settings dict (defaults + on-disk overrides)."""
    out = dict(DEFAULTS)
    if SETTINGS_PATH.exists():
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                stored = json.load(f)
            if isinstance(stored, dict):
                out.update({k: stored[k] for k in stored if k in DEFAULTS})
        except (json.JSONDecodeError, OSError) as e:
            print(f"[settings] could not read {SETTINGS_PATH}: {e}")
    return out


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
        tmp = SETTINGS_PATH.with_name(SETTINGS_PATH.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, SETTINGS_PATH)
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
