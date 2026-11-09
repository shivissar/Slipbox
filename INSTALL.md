# Installing Slipbox

Slipbox is a note-taking app for the Zettelkasten method. Each note is a plain
Markdown file in a folder you choose, so your notes stay yours and stay readable
without the app.

This guide is in three parts:

1. [Install the app](#1-install-the-app) (about a minute)
2. [Set it up the way you like](#2-set-it-up) (notes folder, Zotero, optional AI)
3. [For developers: build it from source](#3-for-developers-build-from-source)

If something goes wrong, jump to [When things go wrong](#when-things-go-wrong).

---

## 1. Install the app

**What you need:** a Mac running a recent version of macOS. Slipbox has been
tested on Apple-silicon Macs (M1 and later).

### Step 1: Copy it into Applications

1. Double-click the `Slipbox-5.2.0.dmg` file you downloaded or were given. A window opens.
2. Drag **Slipbox** onto the **Applications** folder in that window.
3. Eject the disk image (the drive icon on your desktop or in Finder's sidebar).

### Step 2: Open it

Open **Applications** and double-click **Slipbox**. The first time, macOS
asks whether you want to open an app downloaded from the internet. Click
**Open**. You only do this once.

Slipbox is signed with a Developer ID and notarised by Apple: Apple's
automated check found no known malware in it, and macOS confirms that
nothing has changed since. The bundled export tool, pandoc, is signed and
checked along with the rest of the app, so exporting works straight away.

---

## 2. Set it up

### The welcome screen

The first time Slipbox opens with no notes, it shows a short welcome screen:

- **Notes folder.** Where your notes are saved, one `.md` file per note. You can
  keep the folder it suggests, or type the path of another one. A folder inside
  iCloud Drive or Dropbox gives you automatic backups. If you choose a
  different folder, Slipbox asks you to quit (⌘Q) and reopen it once.
  *iCloud Drive:* with **Optimise Mac Storage** on, macOS removes older files
  from your Mac when space runs low, and Slipbox can't show a note that isn't
  there. On macOS 15 (Sequoia) or later, Control-click the notes folder in
  Finder and choose **Keep Downloaded**; on macOS 14 (Sonoma), turn off
  Optimise Mac Storage (System Settings → your name → iCloud). On earlier
  versions, keep the notes folder outside iCloud Drive.
- **Bibliography (optional).** The path to a `.bib` file from Zotero, so you
  can cite with `@` (see below). You can add this later.
- **AI (optional, off).** Your notes are sent nowhere unless you set this up
  later.
- **Tell me when a new version is out (on).** Once a day Slipbox asks GitHub
  whether a newer version has been published, and shows a notice in the
  sidebar if so. The check sends your IP address and Slipbox's version
  number, nothing from your notes, and never installs anything by itself.
  Untick it if you'd rather not; you can change it later in
  **Settings → Updates**.

Press **Start writing** and you are in.

### Finding your way around

- The **sidebar** on the left has your four kinds of note (Research, Journal,
  Teaching, Life), plus Tags, Threads, Citations and Stats.
- Press **⌘K** at any time to search your notes or run a command.
- Press **⌘N** for a new note. While writing, type `[[` to link another note
  and `@` to cite a reference. Your note saves itself a moment after you stop
  typing (once it has a title); **⌘S** saves and closes the editor. You can
  turn autosave off in **Settings → Writing**.
- **Help** (the question-mark button at the bottom of the sidebar) explains
  everything else, including all the keyboard shortcuts.

### Make it look the way you like

Open **Settings → Appearance** (the Settings button at the bottom of the
sidebar). You can choose:

- a light theme (Meadow, Lagoon, Blossom or Paper) and a dark theme (Starlit,
  Forest or Midnight), and whether Slipbox follows your Mac between them;
- an accent colour;
- the font for the interface and the font for reading and writing;
- the text size;
- whether the background illustrations are shown.

Changes apply immediately.

### Lock private notes (optional)

You can encrypt the text of chosen notes, or of every note of a kind (your
journal, say), so it can't be read outside Slipbox without your password.
Open **Settings → Locked notes**, choose a password, and **save the recovery
key** you're shown: if you forget the password, it's the only way back in.
Then use the padlock button on any note, or tick a kind under *Always lock
these kinds*. **Help → Locked notes** explains exactly what is and isn't
encrypted.

### Connect Zotero (optional)

This lets you cite your Zotero library while you write.

1. In Zotero, install the free **Better BibTeX** plugin
   (from [retorque.re/zotero-better-bibtex](https://retorque.re/zotero-better-bibtex/)),
   then restart Zotero.
2. In Zotero, right-click **My Library** (or a collection) and choose
   **Export Library…**
3. Choose the format **Better BibTeX**, tick **Keep updated**, and click
   **OK**. Save the file somewhere you'll remember, for example
   `~/Zotero/My Library.bib`.
4. In Slipbox, open **Settings → Bibliography**, paste that path, and click
   **Save settings**.

**Keep updated** matters: it makes Zotero rewrite the file whenever your
library changes, and Slipbox notices the change by itself.

### Turn on AI features (optional)

Slipbox can ask an AI model questions about a note (**Interrogate**),
suggest tags from the ones you already use, and find **related notes**. All of
this is off until you choose a provider in **Settings → AI**, and nothing is
sent anywhere until you do. The **Privacy** page in the app lists exactly what
is sent where.

Pick one of these:

| If you want… | Choose | You need |
|---|---|---|
| Nothing to leave your Mac | **Ollama** | The free [Ollama](https://ollama.com) app, and a model downloaded with `ollama pull qwen2.5:14b` (chat) and `ollama pull nomic-embed-text` (related notes) |
| A cloud option from Google | **Google Gemini** | An API key from [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| Claude | **Anthropic** | An API key from [console.anthropic.com](https://console.anthropic.com/) |
| OpenAI or a compatible service | **OpenAI-compatible** | An API key from that service |

Then:

1. In **Settings → AI**, choose the provider under **Chat**, paste the key, and
   leave **Model** blank to use the recommended default.
2. Click **Save settings**, then **Test connection**.
3. For related notes, choose a provider under **Embeddings** (it can be the
   same one), save, then click **Re-index all notes**.

API keys are saved in `settings.json` on your Mac in plain text, so treat that
file like a password.

### Where your things are kept

| What | Where |
|---|---|
| Your notes | The notes folder shown in **Settings → Folders** |
| Exports | The export folder shown in **Settings → Folders** |
| Settings, search index, related-notes index | `~/Library/Application Support/com.shivissar.slipbox/` |
| Deleted notes | A `.trash` folder inside your notes folder |

To back up, back up your notes folder. Everything else can be rebuilt from it.

### Updating

**Finding out.** If you left *Tell me when a new version is out* on, a notice
appears at the foot of the sidebar when a new version is published. Click it to
see what's new. To look yourself, press **Check now** in **Settings → Updates**,
or choose **Check for updates** in the ⌘K palette. All versions are listed at
<https://github.com/shivissar/Slipbox/releases>.

**Installing.** Slipbox doesn't install updates by itself, so macOS checks
each new version just as it did the first one:

1. On the update page, click **Download**. Your browser saves the new `.dmg`,
   usually in Downloads.
2. Quit Slipbox (⌘Q). It saves what you were typing first.
3. Open the `.dmg` and drag **Slipbox** onto **Applications**. Choose
   **Replace** when asked.
4. Open Slipbox. If macOS asks whether to open an app downloaded from the
   internet, click **Open**.

Your notes, settings, note history and locked-notes key live outside the app,
so they are not touched. **Skip this version** on the update page hides the
notice until a later version comes out. Pre-releases are never offered.

**Turning the check off.** Switch off **Check for new versions** in
**Settings → Updates**. **Check now** still works whenever you want it.

### Uninstalling

Drag **Slipbox** from Applications to the Bin. To remove its settings and
indexes as well, delete `~/Library/Application Support/com.shivissar.slipbox/`.
Your notes folder is left alone unless you delete it yourself.

---

## When things go wrong

**macOS won't open Slipbox, or says it is damaged.**
Delete it and download it again from
<https://github.com/shivissar/Slipbox/releases>, then check the download
against the SHA-256 checksum on the release page. Copies passed on from
another Mac or altered after download fail Apple's check. Macs managed by an
IT department may be set to open only App Store apps; ask your IT team rather
than working around their settings.

**Exporting fails, or the log mentions `killed: 9`.**
Make sure the pandoc inside the app is the real program and not a Git LFS
pointer (see section 3), which only affects builds made from source. If an
installed copy still fails, run `xattr -cr /Applications/Slipbox.app` in
Terminal and try again, and please report it as an issue.

**Exports go somewhere unexpected.**
Check **Settings → Folders → Export folder**. The folder shown under *In use
now* is where files go. The first time Slipbox saves into Downloads or
Documents, macOS may ask for permission; click **Allow**.

**Citations show zero entries.**
Make sure Zotero exported with the **Better BibTeX** format (not plain BibTeX),
and that the path in **Settings → Bibliography** is right.

**Interrogate or related notes stopped working.**
Open **Settings → AI**. If a model has been shut down by its provider, a
warning there tells you which one to switch to.

**A keyboard shortcut does nothing.**
The command palette (⌘K, or **Search** in the sidebar) can run every command.

**The app won't start after an update.**
Quit it completely (⌘Q) and open it again.

---

## 3. For developers: build from source

You need Git, [Git LFS](https://git-lfs.com) and Python 3.13.

```sh
# Get the code
git clone https://github.com/shivissar/Slipbox.git
cd Slipbox
git lfs pull                     # fetches the bundled pandoc (about 280 MB)

# A virtual environment with Briefcase
python3.13 -m venv ../beeware-venv
source ../beeware-venv/bin/activate
pip install briefcase

# Run it straight from the source tree while developing
briefcase dev

# Build the app and a .dmg
briefcase create macOS           # use create, not build, after any change
briefcase build macOS
briefcase package macOS --adhoc-sign
```

The `.dmg` appears in `dist/`. An ad-hoc-signed build runs on the Mac that
built it; macOS blocks it on other Macs. Published releases are signed with
the maintainer's Developer ID and notarised by Apple
(`briefcase package macOS` without `--adhoc-sign`; see DEVNOTES.md).
Things that have bitten before:

- Run `git lfs pull` before every `briefcase create`. If pandoc reverts to a
  small LFS pointer file, the app builds and launches but every export fails.
- After changing `pyproject.toml` or any source file, use `briefcase create
  macOS`; `briefcase build` alone does not reliably pick up changes.
- Before adding a Python dependency, check it has a macOS arm64 wheel:
  `pip install --dry-run --only-binary :all: <package>`. Briefcase silently
  skips packages it cannot install from a wheel.

### Trying the v5 branch on a Mac that already has v4

```sh
cd ~/beeware-tutorial/Slipbox
git fetch origin
git switch v5                    # your main branch is untouched
git lfs pull
source ../beeware-venv/bin/activate
briefcase dev                    # try it without building
```

Your notes and settings are shared between versions, so v5 opens your existing
notes. To go back: `git switch main`. v4 ignores the new appearance settings, and saving settings in v4 removes them, so you may need to pick your theme again when you return to v5.
