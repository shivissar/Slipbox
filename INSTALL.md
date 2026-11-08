# Installing Zetteling

Zetteling is a note-taking app for the Zettelkasten method. Each note is a plain
Markdown file in a folder you choose, so your notes stay yours and stay readable
without the app.

This guide is in three parts:

1. [Install the app](#1-install-the-app) (about five minutes, no Terminal needed in most cases)
2. [Set it up the way you like](#2-set-it-up) (notes folder, Zotero, optional AI)
3. [For developers: build it from source](#3-for-developers-build-from-source)

If something goes wrong, jump to [When things go wrong](#when-things-go-wrong).

---

## 1. Install the app

**What you need:** a Mac running a recent version of macOS. Zetteling has been
tested on Apple-silicon Macs (M1 and later).

### Step 1: Copy it into Applications

1. Double-click the `Zetteling-5.2.0.dmg` file you downloaded or were given. A window opens.
2. Drag **Zetteling** onto the **Applications** folder in that window.
3. Eject the disk image (the drive icon on your desktop or in Finder's sidebar).

### Step 2: Open it the first time

Zetteling is not yet registered with Apple (that costs a yearly fee), so the
first time you open it your Mac asks you to confirm. You only do this once.

**On macOS 15 (Sequoia) or later:**

1. Open **Applications** and double-click **Zetteling**. A message says it
   can't be opened. Click **Done** (or **OK**).
2. Open **System Settings → Privacy & Security** and scroll to the bottom.
3. Next to the message about Zetteling, click **Open Anyway**.
4. Confirm with **Open Anyway** again, and enter your Mac password if asked.

**On macOS 14 (Sonoma) or earlier:**

1. In **Applications**, hold **Control** and click **Zetteling**, then
   choose **Open**.
2. Click **Open** in the message that appears.

### Step 3: One Terminal command, so exporting works

Zetteling includes pandoc, the tool it uses to export notes to Word, LaTeX and
other formats. macOS marks that tool as "downloaded from the internet" too,
and blocks it silently, so exports fail until you clear the mark. To do that:

1. Open **Terminal** (press ⌘-Space, type *Terminal*, press Return).
2. Copy this line, paste it into Terminal and press Return:

   ```
   xattr -cr /Applications/Zetteling.app
   ```

   Nothing is printed if it worked. You can close Terminal.

You need to do this again after installing a new version.

---

## 2. Set it up

### The welcome screen

The first time Zetteling opens with no notes, it shows a short welcome screen:

- **Notes folder.** Where your notes are saved, one `.md` file per note. You can
  keep the folder it suggests, or type the path of another one. A folder inside
  iCloud Drive or Dropbox gives you automatic backups. If you choose a
  different folder, Zetteling asks you to quit (⌘Q) and reopen it once.
  *iCloud Drive:* with **Optimise Mac Storage** on, macOS removes older files
  from your Mac when space runs low, and Zetteling can't show a note that isn't
  there. On macOS 15 (Sequoia) or later, Control-click the notes folder in
  Finder and choose **Keep Downloaded**; on macOS 14 (Sonoma), turn off
  Optimise Mac Storage (System Settings → your name → iCloud). On earlier
  versions, keep the notes folder outside iCloud Drive.
- **Bibliography (optional).** The path to a `.bib` file from Zotero, so you
  can cite with `@` (see below). You can add this later.
- **AI (optional, off).** Your notes are sent nowhere unless you set this up
  later.
- **Tell me when a new version is out (on).** Once a day Zetteling asks GitHub
  whether a newer version has been published, and shows a notice in the
  sidebar if so. The check sends your IP address and Zetteling's version
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
  Forest or Midnight), and whether Zetteling follows your Mac between them;
- an accent colour;
- the font for the interface and the font for reading and writing;
- the text size;
- whether the background illustrations are shown.

Changes apply immediately.

### Lock private notes (optional)

You can encrypt the text of chosen notes, or of every note of a kind (your
journal, say), so it can't be read outside Zetteling without your password.
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
4. In Zetteling, open **Settings → Bibliography**, paste that path, and click
   **Save settings**.

**Keep updated** matters: it makes Zotero rewrite the file whenever your
library changes, and Zetteling notices the change by itself.

### Turn on AI features (optional)

Zetteling can ask an AI model questions about a note (**Interrogate**),
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
| Settings, search index, related-notes index | `~/Library/Application Support/com.shivissar.zetteling/` |
| Deleted notes | A `.trash` folder inside your notes folder |

To back up, back up your notes folder. Everything else can be rebuilt from it.

### Updating

**Finding out.** If you left *Tell me when a new version is out* on, a notice
appears at the foot of the sidebar when a new version is published. Click it to
see what's new. To look yourself, press **Check now** in **Settings → Updates**,
or choose **Check for updates** in the ⌘K palette. All versions are listed at
<https://github.com/shivissar/Zetteling/releases>.

**Installing.** Zetteling doesn't install updates by itself, so macOS checks
each new version just as it did the first one:

1. On the update page, click **Download**. Your browser saves the new `.dmg`,
   usually in Downloads.
2. Quit Zetteling (⌘Q). It saves what you were typing first.
3. Open the `.dmg` and drag **Zetteling** onto **Applications**. Choose
   **Replace** when asked.
4. Run the Terminal command from Step 3 again, so exporting keeps working:
   `xattr -cr /Applications/Zetteling.app`
5. Open Zetteling. If macOS says it can't check the app, use **Open Anyway** as
   in Step 2.

Your notes, settings, note history and locked-notes key live outside the app,
so they are not touched. macOS may ask again whether Zetteling can use your
Documents, Desktop or iCloud Drive folders: this version isn't signed with an
Apple Developer ID, so macOS treats each new version as a new app. Click
**Allow**. **Skip this version** on the update page hides the
notice until a later version comes out. Pre-releases are never offered.

**Turning the check off.** Switch off **Check for new versions** in
**Settings → Updates**. **Check now** still works whenever you want it.

### Uninstalling

Drag **Zetteling** from Applications to the Bin. To remove its settings and
indexes as well, delete `~/Library/Application Support/com.shivissar.zetteling/`.
Your notes folder is left alone unless you delete it yourself.

---

## When things go wrong

**"Zetteling can't be opened" and there is no Open Anyway button.**
Try opening the app once more, then look in **System Settings → Privacy &
Security** again; the button appears after a failed attempt. If it still isn't
there, run the Terminal command from Step 3 and try again.

**Exporting fails, or the log mentions `killed: 9`.**
Run the Terminal command from Step 3 (`xattr -cr /Applications/Zetteling.app`).

**Exports go somewhere unexpected.**
Check **Settings → Folders → Export folder**. The folder shown under *In use
now* is where files go. The first time Zetteling saves into Downloads or
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
git clone https://github.com/shivissar/Zetteling.git
cd Zetteling
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

The `.dmg` appears in `dist/`. Things that have bitten before:

- Run `git lfs pull` before every `briefcase create`. If pandoc reverts to a
  small LFS pointer file, the app builds and launches but every export fails.
- After changing `pyproject.toml` or any source file, use `briefcase create
  macOS`; `briefcase build` alone does not reliably pick up changes.
- Before adding a Python dependency, check it has a macOS arm64 wheel:
  `pip install --dry-run --only-binary :all: <package>`. Briefcase silently
  skips packages it cannot install from a wheel.

### Trying the v5 branch on a Mac that already has v4

```sh
cd ~/beeware-tutorial/Zetteling
git fetch origin
git switch v5                    # your main branch is untouched
git lfs pull
source ../beeware-venv/bin/activate
briefcase dev                    # try it without building
```

Your notes and settings are shared between versions, so v5 opens your existing
notes. To go back: `git switch main`. v4 ignores the new appearance settings, and saving settings in v4 removes them, so you may need to pick your theme again when you return to v5.
