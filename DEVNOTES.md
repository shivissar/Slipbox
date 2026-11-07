# Zetteling DEVNOTES

v4.0.0: migrated from Flask+pywebview to BeeWare/Toga. Detailed changelog not reconstructed; the prior project's DEVNOTES.md was not carried over during the port, and three rounds of conversation_search across the prior chats returned no retrievable evidence of the migration discussions. See git log for the actual diff.

## v5.0.0 (November 2026): front-end rebuild

Scope: the Flask/Toga/Briefcase architecture is unchanged. app.py (the Toga
shell) was not modified. No new Python dependencies.

Layout and templates
- base.html is now an app shell: `_scene.html` (illustrated background, all
  colours from CSS variables), `_sidebar.html`, then a `main` block. Pages that
  only fill `content` get a single card automatically.
- index.html and note.html add `_notelist.html` (note list column). note.html
  also renders the side panel; `rail="context"` or `rail="chat"`. The old
  chat.html page was removed: /note/<id>/chat now renders note.html with the
  chat panel, and keeps the same POST, clear and stream endpoints.
- edit.html wraps editor and Details panel in one `<form class="edit-form">`
  (display: contents).
- `_ui.html` holds macros: `icon()`, `mascot()`, `kind_chip()`, `kind_tile()`.

Appearance
- themes.py lists themes, fonts, sizes and validates input (`themes.clean`).
  Colours live only in static/style.css, one block per `[data-theme]`.
- base.html renders `data-theme`, `data-tone`, `data-mode`, `data-light`,
  `data-dark`, `data-ui-font`, `data-read-font`, `data-size`, `data-illus` on
  <html>. A small inline script in <head> picks light or dark in "auto" mode
  before first paint and follows macOS changes.
- POST /api/appearance saves; static/app.js applies changes live.
- Fonts are bundled woff2 files from the @fontsource npm packages (OFL).

Client script (static/app.js)
- Command palette, shortcuts, [[ and @ pickers (caret position via a hidden
  mirror element), links list, tag suggestions, streaming Interrogate,
  panel toggles. Pages still work without it.

Backend
- New endpoints: /welcome, /welcome/skip, /api/notes/search,
  /api/notes/titles, /api/appearance.
- db.py: list_recent_cards, quick_search (title match first, then FTS prefix
  match), titles_for, sidebar_counts.
- FTS gotcha: the porter tokenizer stems prefix queries too, and turns a
  final "y" into "i" ("way" -> "wai"), so "way"* matched nothing.
  _fts_prefix_query drops a trailing y.
- settings.get_export_dir / get_bib_path: UI value now wins over env vars.
  app.py always sets ZETTEL_EXPORT_DIR, so before this the UI value could
  never apply.
- ai.py: defaults updated (Gemini chat uses the gemini-flash-latest alias);
  RETIRED_MODELS + retired_model_warnings(); Gemini calls fail fast with a
  clear message on a retired model.
- flask_app.py: removed unused render_markdown() (second sanitiser with a
  different allowlist). Citekeys render as "Surname Year".

Testing done for v5.0 (in a Linux container, not on macOS)
- Every route returns 200 against seeded sample notes and an empty install.
- Headless Chrome: no console errors on any page; picker insertion, ⌘S save,
  palette search, theme and accent changes, welcome flow exercised.
- Not tested: the Toga WebView itself, native menu interaction with the
  keyboard shortcuts, and a Briefcase build. Run `briefcase dev` first.

Build fix (8 Nov 2026)
- First macOS build of v5 failed in the x86_64 half of the universal build:
  cryptography 49.0.0+ publishes arm64-only macOS wheels, and Briefcase
  re-installs the arm64-resolved versions for Intel. Pinned
  "cryptography<49" in the macOS requires (48.0.1 ships a universal2 wheel).
  Checked with pip download for macosx_11_0_arm64 and macosx_11_0_x86_64:
  both resolve the same 54 wheels. Alternative if the pin becomes a burden:
  universal_build = false (Apple Silicon only).
