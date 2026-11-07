Zetteling is a free Mac app for keeping a Zettelkasten: short, linked notes, each one a plain Markdown file in a folder you choose.

## Download

**Zetteling-5.0.1.dmg** (below, under Assets). Needs macOS 11 or later. Built for Apple-silicon and Intel Macs; tested so far on Apple silicon.

SHA-256: `CHECKSUM-TO-ADD`

## Before you install

The app isn't notarised by Apple, so your Mac blocks it the first time. After installing:

1. Open Zetteling once and click **Done** on the warning.
2. Go to **System Settings → Privacy & Security** and click **Open Anyway**.
3. In Terminal, run `xattr -cr /Applications/Zetteling.app` so exports work.

Full instructions: [INSTALL.md](https://github.com/shivissar/Zetteling/blob/main/INSTALL.md).

## In this version

- One window: sidebar, note list, the note, and a side panel with its thread, backlinks and related notes.
- Autosave while you write (Settings → Writing to turn it off).
- `[[` to link a note and `@` to cite from Zotero, at the cursor; ⌘K command palette.
- Seven light and dark themes, fonts, text sizes and accent colour.
- Export to Word, LaTeX, HTML and EPUB with the bundled pandoc.
- Optional AI (Anthropic, OpenAI, Gemini or local Ollama), off until you set it up.

## Licences

Zetteling: PolyForm Noncommercial 1.0.0 (free for personal, academic and non-profit use). The app bundles pandoc 3.1.13 (GPL-2.0-or-later); its source is attached below as `pandoc-3.1.13-source.tar.gz` and at https://github.com/jgm/pandoc/releases/tag/3.1.13. See THIRD-PARTY-NOTICES.md.
