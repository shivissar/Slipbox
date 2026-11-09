# Slipbox DEVNOTES

(Called Zetteling until 5.2.0; older entries keep that name.)

## Rename to Slipbox (9 November 2026)

- Everything visible says Slipbox; package src/Slipbox; Briefcase app key
  Slipbox, so the bundle ID is com.shivissar.slipbox and the app-data folder
  moves. migrate.move_old_app_data() runs in App.startup before anything
  opens the database; vault.rename_legacy_files() runs in create_app().
- Development builds were briefly called Verweis (9 Nov, never released);
  migrate.OLD_APP_IDS and the legacy file names include its names too.
- Deliberately unchanged: vault.VAULT_FORMAT, NOTE_FORMAT, BEGIN/END and the
  HKDF info strings (all "zetteling…"), which are bound into the ciphertext;
  ZETTEL_* environment variables; zettel.db; the "zettel-rail-hidden"
  localStorage key.

v4.0.0: migrated from Flask+pywebview to BeeWare/Toga. Detailed changelog not reconstructed; the prior project's DEVNOTES.md was not carried over during the port, and three rounds of conversation_search across the prior chats returned no retrievable evidence of the migration discussions. See git log for the actual diff.

## 5.2 audit (8 November 2026)

- Quit: Toga 0.5 lets App.on_exit be async and wraps an override with
  simple_handler; closing the main window and ⌘Q both go through
  request_exit. WKWebView's evaluateJavaScript doesn't resolve promises, so
  window.__zettelQuit saves with a synchronous XHR and returns a string.
  The Flask thread answers it while the main thread awaits.
- Start-up: flask_app is imported off the main thread (it resyncs at
  import). make_server(host, 0) binds before serve_forever, so the URL is
  set only once the port is listening; no polling.
- watcher.full_resync trusts mtimes only when embed_meta.index_version
  matches INDEX_VERSION. Bump it when upsert_note indexes something new.
- embed.embed_note_async: one daemon worker, de-duplicated queue, re-reads
  the note; after the provider answers it re-checks notes_meta.locked.
  embed.wait_for_background() is for tests.
- Templates must not use inline event handlers (CSP); a unit test checks.
- Browser suites: tests/browser (not collected by pytest).

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
- Argon2id: since 5.2, RFC 9106 second recommended (64 MiB, t=3, p=4) for
  every new password (vault.ARGON2_DEFAULT). The first recommended (2 GiB,
  t=1, p=4) used in 5.1 cost 2 GiB on every unlock and wrong guess, and the
  MemoryError fallback never fired on macOS (no allocation failure, just
  swap). A vault with other settings is re-wrapped under the same password
  on its next successful unlock (_move_to_default_settings), and the header
  copies in locked notes are refreshed. scrypt (N=2^17, r=8) only if
  Argon2id is missing. Container timing: 0.14 s for 64 MiB.
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

v5.2.0 research tools (8 Nov 2026)
- history.py: snapshot() is called from notes.write_note (throttled to
  INTERVAL=600 s, skipped when identical) and forced before bulk changes.
  Versions are named <id>-v<UTC stamp>-<reason>.md so parse_file works on
  them. encrypt_versions() runs from _lock_note and kind-change locking.
- Embeds: _render_note_html splits on EMBED_LINE_RE and renders segments
  with _render_markdown_html (the old function); embeds nest to depth 2
  with a cycle guard. export._expand_embeds does the same for pandoc (depth
  3) and _links_to_text replaces [[id|text]]/[[id]].
- Coding: codebook.SPAN_RE is shared by the Markdown extension (priority
  172, between wikilink 175 and citekey 170, so links and citations inside
  a passage still render) and the indexer. Locked notes never reach the
  `codes` table; codebook.occurrences() decrypts them live when open.
- Search: db.parse_query/filtered_search; plain queries without filters
  still go to the old FTS path, so raw FTS syntax (OR, NOT, *) keeps working.
- notes._unused_id(): IDs are timestamps; a second note in the same second
  takes the next free second.
- Tests: tests/test_research_tools.py plus Playwright checks outside the
  repo.
- Welcome tour (app.js, "Welcome tour" block; CSS "Welcome tour (5.2)"):
  stops point at [data-tour="…"] hooks in _sidebar.html, _notelist.html
  and note.html, and are skipped when the hook isn't on the page. Starts
  when body has data-tour-auto (context processor: settings.tour_seen is
  false and the page is index/view/search) or the URL has ?tour=N. It is
  marked seen as soon as it opens (POST /api/tour/seen). /tour?step=N goes
  to the latest note, so starting from a list carries on there. The key
  handler is a capture listener: it stops the app's own ⌘ shortcuts but
  never calls preventDefault on them, so ⌘Q/⌘W still reach the Mac menu.
  Moving a hook or renaming a stop: update the TOUR array and
  tour_test.py (outside the repo).
- Update check (updates.py): fetch_latest() → settings.update_latest;
  status() decides the notice (newer than __version__, not skipped, checks
  on). Only URLs under https://github.com/shivissar/Slipbox/ are kept
  from GitHub's answer or opened (safe_url). The app never installs
  anything: open_external() hands the DMG link to the browser, so the new
  app arrives quarantined and Gatekeeper checks it; the build is notarised,
  so that check passes without Open Anyway. Releases must be
  published on GitHub (not drafts or pre-releases) with tag vX.Y.Z and a
  .dmg asset; the repo must be public for the unauthenticated request.
  Tests: tests/test_updates.py and update_test.py (outside the repo).
- External links: Toga's WebView loads every link in the app window unless
  on_navigation_starting is set, and on macOS (Toga 0.5.7) a handler that
  returns True makes Toga load the URL a second time (its cleanup sets
  widget.url), which would break form posts. So links are handled in the
  page instead: app.js "Outside links" sends any http(s) link to another
  origin, and mailto links, to POST /open-link (updates.open_in_browser,
  which runs `open`). Links typed or pasted elsewhere (no click) and
  window.open() calls are not covered.
- Back/esc (5.2): context processor sets back_link on _BACK_PAGES via
  _back_link() (the page's own note, else the Referer's /note/<id>, else
  the Referer path); macro back_button in _ui.html; app.js "Back" block.
  WKWebView sends Referer on link and script navigations.
- Recovery key file (5.2): POST /vault/recovery/save takes the key from the
  page (never stored), checks it with vault.parse_recovery, writes
  "Zetteling recovery key <date>.txt" to _recovery_save_dir() (Downloads,
  else home; never inside the notes folder), O_EXCL + 0600, open -R.
  window.print() does nothing in Toga's WebView, hence no Print button.

## Signing and notarisation (from 5.2.0)

- Release builds are signed with "Developer ID Application: Shiv Issar
  (249567G756)", issued from Apple's G2 Developer ID authority. The
  certificate and private key live in the maintainer's login keychain, with
  a .p12 backup kept outside the repository. A Mac without Apple's
  "Developer ID - G2" intermediate certificate lists the identity as not
  valid (security find-identity shows it under "Matching identities" but
  not "Valid identities only"); install DeveloperIDG2CA.cer from
  apple.com/certificateauthority into the login keychain.
- Build: `rm -rf build dist`, `briefcase create macOS`, `briefcase build
  macOS`, `briefcase package macOS`, choosing the Developer ID identity
  (not ad hoc). Briefcase signs every Mach-O under Contents/Resources and
  Contents/Frameworks with the hardened runtime, then the bundle, builds
  and signs the .dmg, submits it with notarytool (keychain profile
  briefcase-macOS-249567G756, created on first run from the Apple Account
  email and an app-specific password) and staples the ticket. Rerunning
  `briefcase package macOS` resumes an interrupted notarisation.
- macOS asks for the login keychain password the first time codesign uses
  the key; "Always Allow" stops it asking for every file.
- Check: `spctl -a -vvv -t install dist/Slipbox-X.Y.Z.dmg` (accepted,
  source=Notarized Developer ID) and `xcrun stapler validate`. On
  rejection: `xcrun notarytool log <submission-id> --keychain-profile
  "briefcase-macOS-249567G756"`.
- Any change to the app after notarisation, even a template, needs a new
  build and a new notarisation.
- Builds from source for development stay ad hoc
  (`briefcase package macOS --adhoc-sign`); they run on the Mac that built
  them.

## Icons (from 5.2.0)

- App icon: src/Slipbox/resources/Slipbox.icns, the mascot waving on light
  blue. Disk-image (mounted volume) icon: artwork/Slipbox-installer.icns, the
  mascot in its slip box, set with installer_icon in pyproject.toml and kept
  outside src/ so it is not bundled into the app. The .dmg file itself keeps
  macOS's standard disk-image icon.
- Sources in artwork/: slipbox-icon.svg and slipbox-disk-icon.svg (full-bleed
  1024 vector drawings of the same mascot as templates/_ui.html), the
  -1024.png renders on Apple's grid (an 824-px square centred on a 1024
  canvas with a soft shadow) and -1024-full-bleed.png versions for other uses.
- Each .icns holds PNGs drawn from the vector at every size (icp4 16, icp5 32,
  ic11 16@2x, ic12 32@2x, ic07 128, ic13 128@2x, ic08 256, ic14 256@2x,
  ic09 512, ic10 512@2x). To rebuild on a Mac from new 1024 artwork: make an
  .iconset folder with icon_16x16.png … icon_512x512@2x.png and run
  `iconutil -c icns Slipbox.iconset`.
- Changing the app icon changes the app: rebuild and notarise again.
- The logo is a trademark (application filed), so wherever it
  stands as the brand it carries a small ™ just outside its top-right corner:
  the website header, the README, the release notes and the About panel.
  artwork/slipbox-logo-tm.png (1024) and resources/about-logo.png (512) are
  the Apple-grid icon scaled to 88 % with the ™ beside it: Inter SemiBold,
  #777C85 (legible on white and near-black), 64 px tall on the 1024 canvas,
  top-aligned with the icon body, 20 px from its right edge. Slipbox.about()
  in app.py is Toga's cocoa show_about_dialog with this logo in place of the
  app icon, falling back to Toga's own About; tests/app_shell_probe.py
  (mode about) checks both. The app and disk-image icons carry no ™.

