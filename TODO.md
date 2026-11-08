# Verweis TODO

Outstanding items from the v4.0.0 → v4.0.2 work on 2026-04-12, in rough order of leverage. None of these are urgent. All of them are worth doing before the project grows much further.

## Document today's build landmines in DEVNOTES.md

The v4.0.0 → v4.0.2 rebuild cycle surfaced a cluster of Briefcase/LFS/packaging gotchas that took hours to diagnose individually. Writing them down now — while the pain is fresh — prevents rediscovering them in six months or forcing a collaborator to relearn them from scratch.

Five bullets to add:

1. **Run `git lfs pull` before every build.** The bundled pandoc binary is tracked by Git LFS, and some git operations silently revert the working copy to a 134-byte pointer file. A build that completes against a pointer produces a half-sized dmg that launches fine but fails all exports with `killed: 9`. Always verify pandoc is 281MB before running Briefcase commands.

2. **After editing `pyproject.toml`, use `briefcase create` — not `update -r`, not `build`.** `briefcase build` does not reliably re-copy source files when their mtime is older than Briefcase's internal cache timestamp, and `update -r` does not force pip to re-resolve dependencies against changed version pins. Only `create` wipes and rebuilds from scratch. It takes 2-3 minutes but is the only command that guarantees the build reflects current source.

3. **Before adding any Python dependency, verify it has a macOS arm64 wheel on PyPI.** Briefcase invokes pip with `--only-binary :all:` and `--platform macosx_11_0_arm64`, so source-only packages are invisible to the resolver. When pip can't satisfy a version constraint under these flags, Briefcase silently proceeds without the package, and the feature that depends on it quietly breaks. To check: `pip install --dry-run --only-binary :all: <package>` from the beeware-venv simulates Briefcase's resolver in ten seconds.

4. **The `xattr -cr /Applications/Verweis.app` quarantine strip is required after every fresh install, including on the developer's own machine.** Without it, the bundled pandoc binary keeps its own quarantine attribute (separate from the outer .app's) and exports fail silently. Strip the whole bundle recursively; don't just strip the outer .app. This requirement disappears when the app is notarized with a paid Apple Developer account.

5. **The Flask app inside the Toga shell binds to a random free port at launch.** See `_find_free_port()` in `app.py`. First-launch port collisions are rare but possible if another process happens to claim the same port between the probe and the bind. If the app ever fails to launch with a socket error, relaunching usually resolves it.

## Move pandoc out of Git LFS

The `git lfs pull` requirement before every build is a fragile workaround, not a real solution. Two better options when there's energy for the refactor:

Option A: gitignore `src/Verweis/resources/bin/pandoc` entirely, commit a short setup script that downloads pandoc from its GitHub releases page into the expected path, and document the one-line setup command in the developer notes. New clones run `./setup-pandoc.sh` once; existing clones continue to work because their pandoc is already on disk.

Option B: add a pre-build hook to `pyproject.toml` that checks for pandoc at the expected path and downloads it if missing. Removes the human-memory step entirely but requires figuring out how Briefcase's hook system works. More elegant, more effort.

Either eliminates the class of failures where a build silently produces a non-functional dmg because LFS reverted pandoc to a pointer. The 281MB LFS bandwidth savings per clone are a side benefit.

## Fill in UPGRADING.md's [VERIFY] markers

Three of the four markers in `UPGRADING.md` can now be answered directly from today's experience on the development machine:

- **Data directory location:** `~/Library/Application Support/com.shivissar.verweis/`
- **Settings file:** `settings.json` inside the data directory (plain JSON, editable)
- **Database:** `zettel.db` inside the data directory (SQLite, contains bibliography index and embeddings)
- **Notes directory:** configurable through the settings page, defaults to a directory the user chooses

The one remaining verify-marker — whether a v3.17.4 install on a separate Mac migrates cleanly to v4.0.0+ — still requires testing on an actual second machine with an existing v3.17.x install. Low priority unless someone specifically wants to upgrade rather than start fresh.

## Notarize with an Apple Developer ID

$99/year for an Apple Developer Program membership. After notarization, installs become "double-click the dmg, drag to Applications, double-click the app" with no Gatekeeper "Open Anyway" dialog, no `xattr -cr` command, no `killed: 9` export failures from pandoc quarantine. The install guide collapses from six steps to two.

The setup is a few hours of one-time work: create the Apple Developer account, generate a Developer ID Application certificate, install it in Keychain, configure `briefcase package macOS --identity "Developer ID Application: <name>"`, generate an app-specific password for `xcrun notarytool`, run `notarytool submit` after each build, wait 5-30 minutes for Apple's automated check, then `xcrun stapler staple` the returned ticket onto the dmg.

After this, distributing to non-technical users (academic colleagues, students, other scholars) becomes realistic. Before it, the Gatekeeper friction is enough to lose most non-developer users on first install.

## Write TROUBLESHOOTING.md

Deferred from the doc-writing session but worth doing when `UPGRADING.md`'s verify-markers are closed. Three entries to start with, all grounded in things actually encountered:

1. **Exports fail with `killed: 9` in the logs.** The bundled pandoc has macOS quarantine attributes that weren't stripped. Run `xattr -cr /Applications/Verweis.app`. If it still fails after a second run, check that the bundled pandoc is the real 281MB binary and not a Git LFS pointer (look at file size of `/Applications/Verweis.app/Contents/Resources/app/Verweis/resources/bin/pandoc`).

2. **Bibliography shows zero entries despite a configured path.** Check the launch log for `[bib]` messages. `[bib] indexed 0 entries` with no error means the file path is right but parsing returned nothing — likely cause is the .bib file lacks citekeys, which happens when Zotero exports through the built-in BibTeX format instead of the Better BibTeX format. Re-export using Better BibTeX.

3. **First launch after an update crashes with a socket error.** Port collision on Flask's bind. Quit fully (Cmd+Q) and relaunch.

## Address the rename of `BIBTEXPARSER_AVAILABLE`

v5.0: the settings page no longer reads this flag and the settings route no
longer passes it. The constant remains in `bib.py` only; delete it after
grepping for callers once more.

## v5.x follow-ups

- **Model menus filled from each provider's live model list**, so retired
  models disappear on their own. Until then, keep `ai.RETIRED_MODELS` current.
- **Native menu items for the keyboard shortcuts** (Toga commands), if any
  shortcut turns out to be swallowed by the WebView on macOS.
- **Native folder pickers** for the notes and export folders (Toga dialogs
  via a small bridge from Flask to the app object).
- Consider a richer editor (CodeMirror 6, vendored) with inline link pills.

## Long-term watch items

Not actionable, but worth periodically revisiting:

- **Dependency rot.** Once a year, audit `pyproject.toml` for dependencies that have had major version bumps (Flask, Toga, the AI provider libraries). A weekend of "upgrade and retest" is cheaper than accumulating years of skipped upgrades at once.
- **The bus factor.** If Verweis keeps growing in features and surface area, the gap between what exists and what any second person could pick up widens. Consider whether any version of Verweis is small enough for another developer to understand in a weekend, and whether keeping it that size is worth it as a constraint.
- **When 2.x of bibtexparser goes stable.** Revisit the in-house parser decision. A stable upstream parser may be worth depending on again, though the current in-house version is small enough to maintain indefinitely.
