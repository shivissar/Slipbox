Slipbox
=======

.. raw:: html

   <img src="artwork/slipbox-logo-tm.png" width="128" alt="The Slipbox logo">

Slipbox is a local-first Zettelkasten for macOS: a slip box of short notes in
your own words, linked into a web of ideas that grows with your work. Its name
is the English for Zettelkasten, the method Niklas Luhmann made famous. Every
note is a plain Markdown file in a folder you choose, so your notes stay
readable in any text editor, with or without the app.

Slipbox was called Zetteling until version 5.2.

Status
------

Slipbox 5.4.0 released on Monday, Nov 16, 2026, signed and notarised by
Apple. Downloads are on the
`releases page <https://github.com/shivissar/Slipbox/releases>`_.

What it does
------------

* **Linked notes.** Type ``[[`` to link to another note. Each note shows the
  notes that link to it, with the sentence around each link, and mentions of
  its title that are not links yet.
* **Four kinds of note.** Research, journal, teaching and life notes stay in
  separate streams; tags cut across them.
* **Threads.** Ordered sequences of notes, like the numbered branches of a
  paper slip box. Any note can join one, at the place you choose, and a note
  can be in several. A thread can branch at any note, and its branches can
  branch again, ten levels deep.
* **Citations.** Type ``@`` to cite from a ``.bib`` file exported from Zotero.
* **Autosave and history.** Notes save as you write; earlier versions are kept,
  as many per note as you choose, so you can compare and restore them.
* **Import.** Bring in Markdown notes from Obsidian, Zettlr, Bear, a Notion
  export or any folder, as copies: titles, tags, dates and the links between
  them come along.
* **Locked notes.** A note, or every note of a kind, can be encrypted on your
  Mac (AES-256-GCM, with a password and a recovery key). Locked notes are
  never sent to an AI provider.
* **Qualitative coding.** Code passages, then gather them in a codebook with
  definitions, overlaps and CSV export.
* **Manuscripts and templates.** Assemble notes and whole threads into one
  draft, under your own headings, and write around them; start notes
  from templates for interview memos, fieldnotes, reading notes and lecture
  plans, or your own.
* **Search.** ⌘K finds any note or runs any command; filters such as
  ``kind:``, ``tag:``, ``code:`` and ``after:`` narrow the results.
* **Export.** Word, LaTeX, HTML, OpenDocument and EPUB through pandoc, with
  formatted citations.
* **Optional AI.** Questions about a note, tag suggestions and related notes,
  using Anthropic, OpenAI-compatible services, Google Gemini, Voyage AI
  (related notes only) or Ollama running on your own Mac. Off until you set it
  up.

Your notes stay yours
---------------------

Slipbox has no account, no cloud service and no analytics. Settings, search
indexes and note history are kept in
``~/Library/Application Support/com.shivissar.slipbox/``. Once a day, unless
you turn it off, Slipbox asks GitHub whether a newer release exists; nothing
from your notes is sent.

More
----

* **Install, set up, or build from source:**
  `INSTALL.md <https://github.com/shivissar/Slipbox/blob/main/INSTALL.md>`_.
* **What changed:**
  `CHANGELOG <https://github.com/shivissar/Slipbox/blob/main/CHANGELOG>`_ and
  the About page inside the app.
* **Homepage:** https://shivissar.com/slipbox
* **Problems and suggestions:**
  `issues <https://github.com/shivissar/Slipbox/issues>`_.

If you wish to cite Slipbox
---------------------------

Slipbox is archived on Zenodo. The DOI below stands for all versions and
always leads to the latest; the record lists each version.

APA
   Issar, S. (2026). Slipbox: a Zettelkasten for Mac. (Version 5.4.0) [Computer
   software]. Zenodo. https://doi.org/10.5281/zenodo.24086145

Chicago
   Issar, S. Slipbox: A Zettelkasten for Mac. V. 5.4.0. Zenodo, released
   November 16, 2026. https://doi.org/10.5281/zenodo.24086145.

Licence
-------

PolyForm Noncommercial 1.0.0 (see ``LICENSE``): free for personal, academic,
research and non-profit use, but not for commercial use. The app also ships
other people's software and fonts under their own licences, among them pandoc
(GPL-2.0-or-later) and certifi (MPL-2.0); see ``THIRD-PARTY-NOTICES.md``.

Built with `Briefcase <https://briefcase.readthedocs.io/>`_ and
`Toga <https://toga.readthedocs.io/>`_ from the BeeWare project.
