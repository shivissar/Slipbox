"""
themes.py — Appearance options for Verweis v5.

Everything a user can change about how the app looks lives here: the
light and dark theme families, the optional accent colour, the interface
and reading fonts, the text size and whether the background
illustrations are shown. The colours themselves are defined in
static/style.css; this module only knows each theme's name, tone and a
few preview swatches for the Settings page, and validates what the user
picks before it is saved.
"""

# Copyright (c) 2026 Shiv Issar
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

import re

from . import settings

# Each theme: label, tone ("light" or "dark"), one-line description, and
# preview swatches (sky, hill, surface, accent, ink) used only to draw
# the little preview cards in Settings → Appearance.
THEMES = {
    "meadow": {
        "label": "Meadow", "tone": "light",
        "blurb": "Sky, hills and a little house. The default.",
        "preview": {"sky": "#BFE6F5", "hill": "#7ECB93", "surface": "#FFFFFF", "accent": "#34C38F", "ink": "#17283D"},
    },
    "lagoon": {
        "label": "Lagoon", "tone": "light",
        "blurb": "Shallow water and bright aqua.",
        "preview": {"sky": "#9FD8F2", "hill": "#5BBFD8", "surface": "#FFFFFF", "accent": "#22B8CF", "ink": "#0F2A3A"},
    },
    "blossom": {
        "label": "Blossom", "tone": "light",
        "blurb": "Pink trees on green hills.",
        "preview": {"sky": "#F9D6E6", "hill": "#A6DABA", "surface": "#FFFFFF", "accent": "#F472B6", "ink": "#2A1F33"},
    },
    "paper": {
        "label": "Paper", "tone": "light",
        "blurb": "Plain and quiet. No scenery.",
        "preview": {"sky": "#F5F6F8", "hill": "#E3E5E9", "surface": "#FFFFFF", "accent": "#2563EB", "ink": "#111827"},
    },
    "starlit": {
        "label": "Starlit", "tone": "dark",
        "blurb": "Night sky, moon and fireflies. The default.",
        "preview": {"sky": "#1E2660", "hill": "#283071", "surface": "#1B2150", "accent": "#5EEAD4", "ink": "#EEF0FF"},
    },
    "forest": {
        "label": "Forest", "tone": "dark",
        "blurb": "Deep green woods after dusk.",
        "preview": {"sky": "#12322A", "hill": "#1E4A3A", "surface": "#12291F", "accent": "#7DD87D", "ink": "#EAF7EF"},
    },
    "midnight": {
        "label": "Midnight", "tone": "dark",
        "blurb": "Plain dark. No scenery.",
        "preview": {"sky": "#15171C", "hill": "#262A33", "surface": "#1D2027", "accent": "#60A5FA", "ink": "#EDEFF3"},
    },
}

LIGHT_THEMES = [k for k, v in THEMES.items() if v["tone"] == "light"]
DARK_THEMES = [k for k, v in THEMES.items() if v["tone"] == "dark"]

MODES = {"auto": "Match my Mac", "light": "Always light", "dark": "Always dark"}

# Font choices. All are bundled with the app (static/fonts, SIL Open Font
# Licence) except "system", which uses the Mac's own font.
FONTS = {
    "nunito": {"label": "Nunito", "blurb": "Rounded and friendly"},
    "zen": {"label": "Zen Maru Gothic", "blurb": "Soft and round"},
    "atkinson": {"label": "Atkinson Hyperlegible", "blurb": "Designed for legibility"},
    "literata": {"label": "Literata", "blurb": "A bookish serif"},
    "system": {"label": "System", "blurb": "Your Mac's own font"},
}

TEXT_SIZES = {"s": "Small", "m": "Medium", "l": "Large", "xl": "Extra large"}

# A few accent suggestions shown as swatches beside the colour picker.
ACCENT_SWATCHES = ["#34C38F", "#22B8CF", "#3B82F6", "#7C3AED", "#F472B6", "#F97316", "#EF4444", "#64748B"]

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _choice(value, allowed, default):
    value = (value or "").strip().lower()
    return value if value in allowed else default


def clean(raw):
    """Validate a dict of appearance values (from a form or JSON) and
    return only the keys that are safe to save."""
    out = {}
    if "theme_mode" in raw:
        out["theme_mode"] = _choice(raw.get("theme_mode"), MODES, "auto")
    if "theme_light" in raw:
        out["theme_light"] = _choice(raw.get("theme_light"), LIGHT_THEMES, "meadow")
    if "theme_dark" in raw:
        out["theme_dark"] = _choice(raw.get("theme_dark"), DARK_THEMES, "starlit")
    if "ui_font" in raw:
        out["ui_font"] = _choice(raw.get("ui_font"), FONTS, "nunito")
    if "reading_font" in raw:
        out["reading_font"] = _choice(raw.get("reading_font"), FONTS, "nunito")
    if "text_size" in raw:
        out["text_size"] = _choice(raw.get("text_size"), TEXT_SIZES, "m")
    if "accent_color" in raw:
        value = (raw.get("accent_color") or "").strip()
        out["accent_color"] = value.upper() if _HEX_RE.match(value) else ""
    if "illustrations" in raw:
        value = raw.get("illustrations")
        out["illustrations"] = value in (True, "true", "on", "1", 1)
    return out


def _luminance(hex_colour):
    """WCAG relative luminance of a #RRGGBB colour."""
    def channel(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a, b):
    """WCAG contrast ratio between two #RRGGBB colours."""
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def ink_for(hex_colour):
    """Pick near-black or white text for a filled button of this colour,
    whichever contrasts more."""
    dark, light = "#0F172A", "#FFFFFF"
    return dark if contrast(hex_colour, dark) >= contrast(hex_colour, light) else light


def current():
    """The saved appearance, filled with defaults, plus derived values
    the base template needs."""
    s = settings.load()
    ap = {
        "mode": _choice(s.get("theme_mode"), MODES, "auto"),
        "light": _choice(s.get("theme_light"), LIGHT_THEMES, "meadow"),
        "dark": _choice(s.get("theme_dark"), DARK_THEMES, "starlit"),
        "ui_font": _choice(s.get("ui_font"), FONTS, "nunito"),
        "reading_font": _choice(s.get("reading_font"), FONTS, "nunito"),
        "text_size": _choice(s.get("text_size"), TEXT_SIZES, "m"),
        "accent": s.get("accent_color") if _HEX_RE.match(s.get("accent_color") or "") else "",
        "illustrations": bool(s.get("illustrations", True)),
    }
    # The theme to render before any script runs. In "auto" the inline
    # script in base.html swaps to the dark theme if the Mac is in dark mode.
    ap["initial"] = ap["dark"] if ap["mode"] == "dark" else ap["light"]
    ap["initial_tone"] = THEMES[ap["initial"]]["tone"]
    ap["accent_ink"] = ink_for(ap["accent"]) if ap["accent"] else ""
    return ap
