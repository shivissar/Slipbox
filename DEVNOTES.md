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

v5.0.1 autosave (8 Nov 2026)
- Save modes via hidden field _autosave: "1" (write + index, JSON),
  "leave" (beacon on pagehide: also thread choice + embedding), absent
  (explicit save: as leave, then redirect). Shared helpers in flask_app.py:
  _save_mode, _claim_seq, _apply_edit_form, _apply_thread_action.
- _SAVE_LOCK guards _LAST_SEQ (draft token -> newest seq written) and
  _DRAFTS (draft token -> note created). The server is threaded, so without
  these an older in-flight autosave could overwrite newer text, and two
  racing first saves could create two notes.
- Thread choice on a new note is applied only on leave/explicit save, so a
  half-typed thread name is never committed.
- Tested in headless Chrome (27 Playwright checks, run outside the repo:
  autosave, leave beacon, new-note creation, thread on save, autosave off,
  stale-request ordering, racing first saves). Not yet checked in
  the Toga WebView: whether pagehide fires on window close or quit. The
  1.5 s debounce limits loss there to the last pause.

v5.1.0 locked notes (8 Nov 2026)
- vault.py holds all crypto (pyca/cryptography; no primitives of our own).
  Data key (32 random bytes) wrapped with AES-256-GCM under
  Argon2id(password) and under HKDF-SHA-256(recovery key); per save a
  32-byte salt -> HKDF-SHA-256 one-use key -> AES-256-GCM with 12-byte random
  nonce, AAD "zetteling-locked-v1|<vault_id>|<note_id>". Armoured body:
  base64 JSON between BEGIN/END lines; the payload carries the vault header.
- Argon2id: tries RFC 9106 first recommended (2 GiB, t=1, p=4), falls back
  to second (64 MiB, t=3, p=4) on allocation failure; scrypt only if
  Argon2id is unavailable. ZETTEL_VAULT_KDF=light forces the second setting
  (tests only). Measured in the Linux container: 5.1 s for 2 GiB, 0.14 s
  for 64 MiB; expect faster on Apple silicon. Not yet measured on a Mac.
- notes.parse_file decrypts when the vault is open; otherwise body "" and
  unreadable=True. write_note never writes a locked body in plain text: an
  unreadable note writes its existing ciphertext back (metadata-only), a
  readable one is re-encrypted, a closed vault raises VaultLocked.
- db.upsert_note indexes "" and no links for locked notes; embed_note
  deletes any vector and returns. secure_delete=ON on every connection;
  scrub_index() runs FTS optimize + wal_checkpoint(TRUNCATE) after locking.
  Checked: a unique word is gone from zettel.db/-wal/-shm after locking.
- Idle lock: before_request locks when idle > lock_after_minutes (background
  paths excluded); the page also runs a timer that flushes the editor,
  posts /vault/lock and reloads.
- Tests: tests/test_vault.py (12 unit tests), plus Playwright checks outside
  the repo (24: set-up, recovery page, lock, palette lock, closed view,
  wrong password, unlock, autosave encrypted, inline unlock, kind lock, new
  journal locked, idle lock, export guard).
- Not checked on macOS: Argon2id 2 GiB timing and memory inside the Toga
  app; print layout of the recovery key page in WKWebView.
