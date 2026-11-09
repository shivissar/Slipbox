/* Slipbox v5 — client-side behaviour.
 *
 * Everything here is progressive: every page works without this file
 * (links and forms are ordinary HTML). This adds the command palette,
 * keyboard shortcuts, the [[ and @ pickers in the editor, streaming
 * Interrogate replies, panel toggles and live appearance changes.
 *
 * Copyright (c) 2026 Shiv Issar
 * SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
 */
(function () {
  "use strict";

  var html = document.documentElement;
  var body = document.body;
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  // Escape, then let FTS <mark> highlights back through.
  function escMarks(s) {
    return esc(s).replace(/&lt;mark&gt;/g, "<mark>").replace(/&lt;\/mark&gt;/g, "</mark>");
  }
  function store(key, value) {
    try {
      if (value === undefined) return window.localStorage.getItem(key);
      if (value === null) window.localStorage.removeItem(key);
      else window.localStorage.setItem(key, value);
    } catch (e) { return null; }
    return null;
  }
  function debounce(fn, ms) {
    var t;
    return function () {
      var args = arguments, self = this;
      clearTimeout(t);
      t = setTimeout(function () { fn.apply(self, args); }, ms);
    };
  }

  var toastTimer;
  function toast(msg) {
    var el = $("#toast");
    if (!el) return;
    el.textContent = msg;
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.classList.remove("show"); }, 1800);
  }
  window.zToast = toast;

  var KIND_LABEL = { research: "Research", journal: "Journal", teaching: "Teaching", life: "Life" };
  function kindTile(kind) {
    kind = kind || "research";
    return '<span class="kind-tile kind-tile-' + esc(kind) + '" aria-hidden="true"><span class="dot dot-' + esc(kind) + '"></span></span>';
  }

  /* ------------------------------------------------------------------ *
   * Appearance: apply immediately, then save.
   * ------------------------------------------------------------------ */

  function luminance(hex) {
    var c = [1, 3, 5].map(function (i) {
      var v = parseInt(hex.substr(i, 2), 16) / 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
  }
  function inkFor(hex) {
    var l = luminance(hex);
    var withDark = (l + 0.05) / (luminance("#0F172A") + 0.05);
    var withWhite = 1.05 / (l + 0.05);
    return withDark >= withWhite ? "#0F172A" : "#FFFFFF";
  }

  function applyAppearance(p) {
    if ("theme_mode" in p) html.setAttribute("data-mode", p.theme_mode);
    if ("theme_light" in p) html.setAttribute("data-light", p.theme_light);
    if ("theme_dark" in p) html.setAttribute("data-dark", p.theme_dark);
    if ("ui_font" in p) html.setAttribute("data-ui-font", p.ui_font);
    if ("reading_font" in p) html.setAttribute("data-read-font", p.reading_font);
    if ("text_size" in p) html.setAttribute("data-size", p.text_size);
    if ("illustrations" in p) html.setAttribute("data-illus", p.illustrations ? "on" : "off");
    if ("accent_color" in p) {
      if (p.accent_color) {
        html.classList.add("custom-accent");
        html.style.setProperty("--accent", p.accent_color);
        html.style.setProperty("--accent-ink", inkFor(p.accent_color));
      } else {
        html.classList.remove("custom-accent");
        html.style.removeProperty("--accent");
        html.style.removeProperty("--accent-ink");
      }
    }
    if (window.zApplyTheme) window.zApplyTheme();
  }

  function saveAppearance(p, quiet) {
    applyAppearance(p);
    return fetch("/api/appearance", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(p),
    }).then(function (r) { return r.json(); })
      .then(function (d) { if (!quiet) toast(d.ok ? "Saved" : (d.message || "Could not save")); return d; })
      .catch(function () { toast("Could not save"); });
  }
  window.zSaveAppearance = saveAppearance;

  var MODE_NAMES = { auto: "Matching your Mac", light: "Always light", dark: "Always dark" };
  function setMode(mode) {
    saveAppearance({ theme_mode: mode }, true).then(function () { toast(MODE_NAMES[mode]); });
    syncAppearanceControls();
  }
  $$("[data-cycle-mode]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var order = ["auto", "light", "dark"];
      var cur = html.getAttribute("data-mode") || "auto";
      setMode(order[(order.indexOf(cur) + 1) % order.length]);
    });
  });

  // Settings → Appearance controls (present only on the settings page).
  function syncAppearanceControls() {
    var state = {
      theme_mode: html.getAttribute("data-mode"),
      theme_light: html.getAttribute("data-light"),
      theme_dark: html.getAttribute("data-dark"),
      ui_font: html.getAttribute("data-ui-font"),
      reading_font: html.getAttribute("data-read-font"),
      text_size: html.getAttribute("data-size"),
    };
    $$("[data-ap-key]").forEach(function (el) {
      var key = el.getAttribute("data-ap-key");
      if (key in state) el.setAttribute("aria-pressed", el.getAttribute("data-ap-value") === state[key] ? "true" : "false");
    });
    var accent = html.classList.contains("custom-accent") ? (html.style.getPropertyValue("--accent") || "").trim().toUpperCase() : "";
    $$("[data-accent]").forEach(function (el) {
      el.setAttribute("aria-pressed", (el.getAttribute("data-accent") || "").toUpperCase() === accent ? "true" : "false");
    });
    var picker = $("#accent-picker");
    if (picker && accent) picker.value = accent.toLowerCase();
    var illus = $("#illustrations-toggle");
    if (illus) illus.checked = html.getAttribute("data-illus") !== "off";
  }
  $$("[data-ap-key]").forEach(function (el) {
    el.addEventListener("click", function () {
      var p = {};
      p[el.getAttribute("data-ap-key")] = el.getAttribute("data-ap-value");
      saveAppearance(p);
      syncAppearanceControls();
    });
  });
  $$("[data-accent]").forEach(function (el) {
    el.addEventListener("click", function () {
      saveAppearance({ accent_color: el.getAttribute("data-accent") });
      syncAppearanceControls();
    });
  });
  var accentPicker = $("#accent-picker");
  if (accentPicker) {
    accentPicker.addEventListener("change", function () {
      saveAppearance({ accent_color: accentPicker.value.toUpperCase() });
      syncAppearanceControls();
    });
  }
  var illusToggle = $("#illustrations-toggle");
  if (illusToggle) {
    illusToggle.addEventListener("change", function () {
      saveAppearance({ illustrations: illusToggle.checked });
    });
  }
  syncAppearanceControls();

  /* ------------------------------------------------------------------ *
   * Panels: side panel and note list (drawers on narrow windows).
   * ------------------------------------------------------------------ */

  var app = $("#app");
  var narrowRail = window.matchMedia("(max-width: 1240px)");
  var narrowList = window.matchMedia("(max-width: 980px)");
  if (app && store("zettel-rail-hidden") === "1" && !narrowRail.matches) app.classList.add("rail-hidden");

  $$("[data-toggle-rail]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      if (!app) return;
      if (narrowRail.matches) {
        app.classList.toggle("rail-open");
      } else {
        var hidden = app.classList.toggle("rail-hidden");
        store("zettel-rail-hidden", hidden ? "1" : null);
      }
    });
  });
  $$("[data-toggle-list]").forEach(function (btn) {
    btn.addEventListener("click", function () { if (app && narrowList.matches) app.classList.toggle("list-open"); });
  });
  document.addEventListener("click", function (e) {
    if (!app) return;
    if (app.classList.contains("rail-open") && !e.target.closest(".rail") && !e.target.closest("[data-toggle-rail]")) app.classList.remove("rail-open");
    if (app.classList.contains("list-open") && !e.target.closest(".listcol") && !e.target.closest("[data-toggle-list]")) app.classList.remove("list-open");
  });

  // Note list search: hide the cards while showing results.
  var listSearch = $("#list-search");
  if (listSearch) {
    var cards = $("#list-cards"), results = $("#list-results");
    var syncList = function () {
      var has = listSearch.value.trim() !== "";
      if (cards) cards.hidden = has;
      if (!has && results) results.innerHTML = "";
    };
    listSearch.addEventListener("input", syncList);
    listSearch.addEventListener("search", syncList);
  }

  /* ------------------------------------------------------------------ *
   * Command palette (⌘K).
   * ------------------------------------------------------------------ */

  var ICONS = {
    edit: '<path d="M4 20h4L19 9l-4-4L4 16z"></path>',
    chat: '<path d="M4 5h16v11H9l-5 4z"></path>',
    split: '<rect x="3" y="4" width="18" height="16" rx="4"></rect><path d="M12 4v16"></path>',
    export: '<path d="M12 3v12M7 8l5-5 5 5"></path><path d="M5 14v6h14v-6"></path>',
    plus: '<path d="M12 5v14M5 12h14"></path>',
    journal: '<path d="M6 4h11a1 1 0 0 1 1 1v15H7a2 2 0 0 1-2-2V5a1 1 0 0 1 1-1z"></path><path d="M9 8h6M9 12h6"></path>',
    go: '<path d="M5 12h14M13 6l6 6-6 6"></path>',
    tag: '<path d="M3 12V4h8l9 9-8 8z"></path><circle cx="7.5" cy="7.5" r="1.2"></circle>',
    thread: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"></path><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"></path>',
    book: '<path d="M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3z"></path><path d="M5 17a3 3 0 0 1 3-3h11"></path>',
    chart: '<path d="M5 20V11M12 20V4M19 20v-7"></path>',
    sliders: '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"></path><circle cx="15" cy="7" r="2"></circle><circle cx="9" cy="17" r="2"></circle>',
    download: '<path d="M12 4v11"></path><path d="M7 10l5 5 5-5"></path><path d="M5 20h14"></path>',
    compass: '<circle cx="12" cy="12" r="9"></circle><path d="M15.5 8.5l-2 5-5 2 2-5z"></path>',
    help: '<circle cx="12" cy="12" r="9"></circle><path d="M9.5 9.5a2.5 2.5 0 0 1 4.8 1c0 1.7-2.3 2-2.3 3.5"></path><path d="M12 17.5h.01"></path>',
    sun: '<circle cx="12" cy="12" r="4"></circle><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"></path>',
    moon: '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"></path>',
    search: '<circle cx="11" cy="11" r="7"></circle><path d="M20 20l-3.5-3.5"></path>',
    lock: '<rect x="5" y="11" width="14" height="9" rx="2.5"></rect><path d="M8 11V8a4 4 0 0 1 8 0v3"></path>',
    unlock: '<rect x="5" y="11" width="14" height="9" rx="2.5"></rect><path d="M8 11V8a4 4 0 0 1 7.6-1.7"></path>',
  };
  function svg(name) { return '<svg class="i" viewBox="0 0 24 24" aria-hidden="true">' + (ICONS[name] || ICONS.go) + "</svg>"; }

  var ctx = {
    noteId: body.getAttribute("data-note-id"),
    noteKind: body.getAttribute("data-note-kind"),
    chat: body.getAttribute("data-chat") === "on",
    threads: (body.getAttribute("data-threads") || "").split("|").filter(Boolean),
    vault: body.getAttribute("data-vault") || "none",
    templates: (body.getAttribute("data-templates") || "").split("|").filter(Boolean).map(function (s) {
      var i = s.indexOf("::"); return { slug: s.slice(0, i), name: s.slice(i + 2) };
    }),
    noteLocked: body.getAttribute("data-note-locked") === "1",
  };
  function postTo(url) {
    // Submit a small POST form (the server answers with a redirect).
    var f = document.createElement("form");
    f.method = "post"; f.action = url; f.hidden = true;
    var nx = document.createElement("input"); nx.type = "hidden"; nx.name = "next";
    nx.value = window.location.pathname + window.location.search;
    f.appendChild(nx); document.body.appendChild(f); f.submit();
  }
  function currentKind() {
    var params = new URLSearchParams(window.location.search);
    var k = ctx.noteKind || params.get("kind");
    return KIND_LABEL[k] ? k : "research";
  }

  function commandList() {
    var c = [];
    if (ctx.noteId) {
      var n = ctx.noteId;
      c.push({ group: "This note", label: "Edit", hint: "⌘E", icon: "edit", href: "/note/" + n + "/edit" });
      if (ctx.chat) c.push({ group: "This note", label: "Interrogate", hint: "⌘I", icon: "chat", href: "/note/" + n + "/chat" });
      c.push({ group: "This note", label: "Open another note beside this one", icon: "split", href: "/note/" + n + "/split" });
      if (ctx.noteLocked) c.push({ group: "This note", label: "Remove the lock from this note", icon: "unlock", run: function () {
        if (window.confirm("Remove the lock from this note? Its text will be saved as plain text again.")) postTo("/note/" + n + "/remove-lock");
      } });
      else c.push({ group: "This note", label: "Lock this note", sub: "Encrypt its text on disk", icon: "lock", run: function () { postTo("/note/" + n + "/lock"); } });
      c.push({ group: "This note", label: "Export note…", sub: "Word, LaTeX, Markdown, EPUB, ODT, HTML, text", icon: "export", href: "/export/note/" + n });
      ctx.threads.forEach(function (t) {
        c.push({ group: "This note", label: "Export thread “" + t + "”…", icon: "export", href: "/export/thread/" + encodeURIComponent(t) });
      });
    }
    c.push({ group: "Create", label: "New note", hint: "⌘N", icon: "plus", href: "/new?kind=" + currentKind() });
    c.push({ group: "Create", label: "New journal entry, stamped now", hint: "⇧⌘N", icon: "journal", href: "/journal/new" });
    [["All notes", "/?kind=all"], ["Research", "/?kind=research"], ["Journal", "/?kind=journal"],
     ["Teaching", "/?kind=teaching"], ["Life", "/?kind=life"]].forEach(function (x) {
      c.push({ group: "Go to", label: x[0], icon: "go", href: x[1] });
    });
    c.push({ group: "Go to", label: "Tags", icon: "tag", href: "/tags" });
    c.push({ group: "Go to", label: "Threads", icon: "thread", href: "/threads" });
    c.push({ group: "Go to", label: "Citations", icon: "book", href: "/citekeys" });
    c.push({ group: "Go to", label: "Stats", icon: "chart", href: "/stats" });
    c.push({ group: "Go to", label: "Settings", hint: "⌘,", icon: "sliders", href: "/settings" });
    c.push({ group: "Go to", label: "Appearance and themes", icon: "sliders", href: "/settings#appearance" });
    c.push({ group: "Go to", label: "Help and shortcuts", icon: "help", href: "/help" });
    c.push({ group: "Go to", label: "About, privacy and licence", icon: "help", href: "/about" });
    c.push({ group: "Go to", label: "Take the tour", sub: "A one-minute look around the window", icon: "compass", run: function () {
      if (ctx.noteId) { closePalette(); setTimeout(function () { tourStart(1); }, 50); } else window.location.href = "/tour";
    } });
    c.push({ group: "Go to", label: "Check for updates", sub: "Is there a newer version of Slipbox?", icon: "download", href: "/update" });
    c.push({ group: "Go to", label: "Manuscripts", icon: "list", href: "/manuscripts" });
    c.push({ group: "Go to", label: "Codebook", icon: "list", href: "/codes" });
    c.push({ group: "New from a template", label: "New manuscript", sub: "Gather notes into one draft", icon: "plus", run: function () { postTo("/manuscripts/new"); } });
    if (ctx.vault === "open") c.push({ group: "Locked notes", label: "Lock notes now", sub: "Close all locked notes until you enter the password", icon: "lock", run: function () { postTo("/vault/lock"); } });
    else if (ctx.vault === "closed") c.push({ group: "Locked notes", label: "Unlock locked notes", icon: "unlock", href: "/unlock?next=" + encodeURIComponent(window.location.pathname) });
    else c.push({ group: "Locked notes", label: "Set up locked notes", sub: "Choose a password to encrypt chosen notes", icon: "lock", href: "/settings#locked" });
    ctx.templates.forEach(function (t) {
      c.push({ group: "New from a template", label: "New " + t.name.charAt(0).toLowerCase() + t.name.slice(1), icon: "plus", href: "/new?template=" + encodeURIComponent(t.slug) });
    });
    c.push({ group: "Appearance", label: "Always light", icon: "sun", run: function () { setMode("light"); } });
    c.push({ group: "Appearance", label: "Always dark", icon: "moon", run: function () { setMode("dark"); } });
    c.push({ group: "Appearance", label: "Match my Mac (light or dark)", icon: "sliders", run: function () { setMode("auto"); } });
    return c;
  }

  var pal = $("#palette"), palInput = $("#palette-input"), palList = $("#palette-list");
  var palItems = [], palSel = 0, palReq = 0;

  function openPalette() {
    if (!pal) return;
    pal.hidden = false;
    palInput.value = "";
    renderPalette("");
    setTimeout(function () { palInput.focus(); }, 0);
  }
  function closePalette() { if (pal) pal.hidden = true; }
  window.zOpenPalette = openPalette;

  // Every typed word must start a word in the label ("exp th" finds
  // "Export thread"), so "way" does not match "Always".
  function matches(label, q) {
    var words = label.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
    return q.toLowerCase().split(/\s+/).filter(Boolean).every(function (w) {
      return words.some(function (lw) { return lw.indexOf(w) === 0; });
    });
  }

  function renderPalette(q) {
    var cmds = commandList();
    var shown = q ? cmds.filter(function (c) { return matches(c.label + " " + (c.group || ""), q); }) : cmds.filter(function (c) { return c.group !== "Appearance"; });
    palItems = shown.slice();
    palSel = 0;
    drawPalette(null);
    var reqId = ++palReq;
    fetch("/api/notes/search?limit=6&q=" + encodeURIComponent(q) + (ctx.noteId ? "&exclude=" + ctx.noteId : ""))
      .then(function (r) { return r.json(); })
      .then(function (notes) {
        if (reqId !== palReq) return;
        var group = q ? "Notes" : "Recent notes";
        notes.forEach(function (n) {
          palItems.push({ group: group, label: n.title, note: n, href: "/note/" + n.id });
        });
        if (q) palItems.push({ group: "Notes", label: "Search all notes for “" + q + "”", icon: "search", href: "/search?q=" + encodeURIComponent(q) });
        drawPalette(q);
      })
      .catch(function () {});
  }

  function drawPalette() {
    var out = [], lastGroup = null;
    palItems.forEach(function (it, i) {
      if (it.group !== lastGroup) {
        out.push('<li class="palette-group" role="presentation">' + esc(it.group) + "</li>");
        lastGroup = it.group;
      }
      var tile = it.note ? kindTile(it.note.kind) : '<span class="tile">' + svg(it.icon) + "</span>";
      var sub = it.note ? (it.note.snip ? escMarks(it.note.snip) : esc((it.note.modified || "").slice(0, 10))) : esc(it.sub || "");
      out.push('<li class="palette-item' + (i === palSel ? " sel" : "") + '" role="option" aria-selected="' + (i === palSel) + '" data-i="' + i + '">' +
        tile + '<span class="label">' + esc(it.label) + (sub ? "<small>" + sub + "</small>" : "") + "</span>" +
        (it.hint ? '<span class="hint">' + esc(it.hint) + "</span>" : "") + "</li>");
    });
    if (!palItems.length) out.push('<li class="palette-group" role="presentation">Nothing matches yet…</li>');
    palList.innerHTML = out.join("");
    var sel = $(".palette-item.sel", palList);
    if (sel && sel.scrollIntoView) sel.scrollIntoView({ block: "nearest" });
  }

  function runItem(it) {
    if (!it) return;
    closePalette();
    if (it.run) it.run();
    else if (it.href) window.location.href = it.href;
  }

  if (pal) {
    palInput.addEventListener("input", debounce(function () { renderPalette(palInput.value.trim()); }, 90));
    palInput.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") { palSel = Math.min(palSel + 1, palItems.length - 1); drawPalette(); e.preventDefault(); }
      else if (e.key === "ArrowUp") { palSel = Math.max(palSel - 1, 0); drawPalette(); e.preventDefault(); }
      else if (e.key === "Enter") { runItem(palItems[palSel]); e.preventDefault(); }
      else if (e.key === "Escape") { closePalette(); e.preventDefault(); }
    });
    palList.addEventListener("click", function (e) {
      var li = e.target.closest(".palette-item");
      if (li) runItem(palItems[parseInt(li.getAttribute("data-i"), 10)]);
    });
    pal.addEventListener("click", function (e) { if (e.target === pal) closePalette(); });
  }
  $$("[data-open-palette]").forEach(function (b) { b.addEventListener("click", openPalette); });

  /* ------------------------------------------------------------------ *
   * Keyboard shortcuts.
   * ------------------------------------------------------------------ */

  document.addEventListener("keydown", function (e) {
    var mod = e.metaKey || e.ctrlKey;
    if (!mod) {
      if (e.key === "Escape" && app) { app.classList.remove("rail-open"); app.classList.remove("list-open"); }
      return;
    }
    var k = (e.key || "").toLowerCase();
    if (k === "k") { e.preventDefault(); if (pal && !pal.hidden) closePalette(); else openPalette(); return; }
    if (pal && !pal.hidden) return;
    if (k === "s") {
      var form = $("#edit-form");
      e.preventDefault();
      if (form) { allowLeave = true; form.requestSubmit ? form.requestSubmit() : form.submit(); }
      return;
    }
    if (k === "c" && e.shiftKey && window.__zettelCode) { e.preventDefault(); window.__zettelCode(); return; }
    if (k === "n" && e.shiftKey) { e.preventDefault(); window.location.href = "/journal/new"; return; }
    if (k === "n") { e.preventDefault(); window.location.href = "/new?kind=" + currentKind(); return; }
    if (k === "," ) { e.preventDefault(); window.location.href = "/settings"; return; }
    if (k === "e" && ctx.noteId && !$("#edit-form")) { e.preventDefault(); window.location.href = "/note/" + ctx.noteId + "/edit"; return; }
    if (k === "i" && ctx.noteId && ctx.chat && !$("#edit-form")) { e.preventDefault(); window.location.href = "/note/" + ctx.noteId + "/chat"; return; }
  });

  /* ------------------------------------------------------------------ *
   * Editor: autosize, unsaved-changes guard, [[ and @ pickers, links
   * list, tag suggestions and the thread controls.
   * ------------------------------------------------------------------ */

  var ta = $("#body");
  var editForm = $("#edit-form");
  var allowLeave = false;

  /* Asked by the Mac app when you quit (5.2). Must answer synchronously:
   * "clean" (nothing to save), "saved", or why it couldn't save, in which
   * case the app asks before quitting. The editor replaces this below. */
  window.__zettelQuit = function () { return "clean"; };

  if (ta && editForm) {
    var wrap = $("#editor-wrap");
    var picker = $("#picker"), pList = $("#picker-list"), pTitle = $("#picker-title"), pCount = $("#picker-count"), pFoot = $("#picker-foot");
    var dirtyChip = $("#dirty-chip");
    var dirty = false;

    var autosize = function () {
      ta.style.height = "auto";
      ta.style.height = Math.max(ta.scrollHeight + 4, 320) + "px";
    };
    autosize();

    /* Saving. With autosave on (Settings → Writing), the note is saved
     * 1.5 s after typing stops and once more when the editor closes; only
     * that last save (or ⌘S) refreshes related notes. A new note is created
     * on its first save, which waits for a title and for the cursor to leave
     * the title field, so its filename gets the whole title. Every request
     * carries this page's draft token and a rising sequence number; the
     * server ignores anything older than what it has already written. */
    var auto = editForm.getAttribute("data-autosave") === "1";
    var saveUrl = editForm.getAttribute("data-save-url") || editForm.action;
    var isNew = editForm.getAttribute("data-new") === "1";
    var noteId = isNew ? "" : (body.getAttribute("data-note-id") || "");
    var titleInput = $("#title-input");
    var cancel = $("[data-cancel]");
    var draft = Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
    var seq = 0, timer = null, inflight = false, failed = false, needsEmbed = false, submitting = false;
    var lockedOut = false;  // last save was refused because locked notes are closed

    var hidden = function (name) {
      var el = editForm.querySelector('input[type="hidden"][name="' + name + '"]');
      if (!el) { el = document.createElement("input"); el.type = "hidden"; el.name = name; editForm.appendChild(el); }
      return el;
    };
    hidden("_draft").value = draft;

    var STATUS = {
      dirty: ["Not saved yet", true], saving: ["Saving…", false], saved: ["Saved", false],
      title: ["Add a title to start saving", true], error: ["Couldn’t save. Retrying…", true],
      locked: ["Locked notes are closed: unlock to save", true]
    };
    var setStatus = function (key) {
      if (!dirtyChip) return;
      var st = STATUS[key];
      dirtyChip.textContent = st[0];
      dirtyChip.classList.toggle("chip-warn", st[1]);
      dirtyChip.hidden = false;
    };
    var hasTitle = function () { return !!(titleInput && titleInput.value.trim()); };
    var canAutosave = function () { return auto && (!!noteId || hasTitle()); };
    var payload = function (mode) {
      var fd = new FormData(editForm);
      fd.set("_autosave", mode);
      fd.set("_draft", draft);
      fd.set("_seq", String(++seq));
      return fd;
    };

    var becameNote = function (j) {
      // First save of a new note: from here on, save to its edit URL.
      noteId = j.id; isNew = false;
      var trow = $(".template-row"); if (trow) trow.hidden = true;
      saveUrl = j.edit_url; editForm.action = j.edit_url;
      body.setAttribute("data-note-id", j.id);
      excludeId = j.id;
      if (cancel && j.view_url) cancel.href = j.view_url;
      try { window.history.replaceState(null, "", j.edit_url); } catch (err) { /* ignore */ }
    };

    var saveNow = function () {
      timer = null;
      if (!dirty || submitting || lockedOut) return;
      if (!canAutosave()) { setStatus(hasTitle() ? "dirty" : "title"); return; }
      // Don't create a new note mid-title: wait until the cursor moves on.
      if (!noteId && document.activeElement === titleInput) { setStatus("dirty"); return; }
      if (inflight) { schedule(); return; }
      inflight = true; failed = false; dirty = false; setStatus("saving");
      fetch(saveUrl, { method: "POST", body: payload("1"), headers: { "Accept": "application/json" } })
        .then(function (r) { return r.json().then(function (j) { return { ok: r.ok && j && j.ok, j: j, status: r.status }; }); })
        .then(function (res) {
          if (res.status === 423) { lockedOut = true; dirty = true; showInlineUnlock(); return; }
          if (!res.ok) throw new Error((res.j && res.j.message) || "save failed");
          needsEmbed = true;
          if (!noteId && res.j.edit_url) becameNote(res.j);
          setStatus(dirty ? "saving" : "saved");
          if (res.j.outside_version_kept) {
            toast("This note had been changed in another app. That version is kept in its history.");
          }
        })
        .catch(function () { dirty = true; failed = true; setStatus("error"); })
        .then(function () { inflight = false; if (dirty && !lockedOut) schedule(failed ? 3000 : 1500); });
    };
    var schedule = function (ms) {
      if (!auto) return;
      clearTimeout(timer);
      timer = setTimeout(saveNow, ms || 1500);
    };

    // Locked notes closed (idle lock, or a kind that is always locked):
    // unlock right here, without leaving the editor, then save.
    var unlockBox = $("#inline-unlock"), unlockPw = $("#inline-unlock-pw"), unlockBtn = $("#inline-unlock-btn");
    var showInlineUnlock = function () {
      setStatus("locked");
      if (unlockBox) { unlockBox.hidden = false; if (unlockPw) unlockPw.focus(); }
    };
    var doInlineUnlock = function () {
      if (!unlockPw || !unlockPw.value) return;
      unlockBtn.disabled = true; unlockBtn.textContent = "Unlocking…";
      var fd = new FormData(); fd.set("password", unlockPw.value);
      fetch("/unlock", { method: "POST", body: fd, headers: { "Accept": "application/json" } })
        .then(function (r) { return r.json(); })
        .then(function (j) {
          unlockBtn.disabled = false; unlockBtn.textContent = "Unlock";
          if (!j.ok) { unlockPw.value = ""; unlockPw.placeholder = j.message || "Wrong password"; return; }
          unlockPw.value = ""; unlockBox.hidden = true; lockedOut = false;
          body.setAttribute("data-vault", "open");
          if (auto) { dirty = true; saveNow(); } else setStatus("dirty");
        })
        .catch(function () { unlockBtn.disabled = false; unlockBtn.textContent = "Unlock"; });
    };
    if (unlockBtn) unlockBtn.addEventListener("click", doInlineUnlock);
    if (unlockPw) unlockPw.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); doInlineUnlock(); }
    });

    var markDirty = function (e) {
      if (e && e.target && unlockBox && unlockBox.contains(e.target)) return;
      dirty = true;
      if (lockedOut) { setStatus("locked"); return; }  // wait for the inline unlock
      if (!auto) { setStatus("dirty"); return; }
      setStatus(canAutosave() ? "saving" : "title");
      schedule();
    };
    editForm.addEventListener("input", markDirty);
    editForm.addEventListener("change", markDirty);
    if (titleInput) titleInput.addEventListener("blur", function () { if (auto && dirty) schedule(200); });
    editForm.addEventListener("submit", function (e) {
      if (lockedOut) { e.preventDefault(); showInlineUnlock(); return; }
      allowLeave = true; submitting = true; clearTimeout(timer);
      hidden("_seq").value = String(++seq);
    });

    // Closing the editor: one last save that also refreshes related notes.
    window.addEventListener("pagehide", function () {
      if (!auto || submitting) return;
      if (!(dirty || needsEmbed || inflight) || !canAutosave()) return;
      clearTimeout(timer);
      var fd = payload("leave");
      if (!(navigator.sendBeacon && navigator.sendBeacon(saveUrl, fd))) {
        try { fetch(saveUrl, { method: "POST", body: fd, keepalive: true }); } catch (err) { /* ignore */ }
      }
      needsEmbed = false; dirty = false;
    });
    var unsafeToLeave = function () { return dirty && !allowLeave && !canAutosave(); };
    // Used by the idle lock: save what's typed before locked notes close.
    window.__zettelFlush = function () {
      clearTimeout(timer);
      if (!auto) return Promise.resolve(!dirty);
      if (dirty && !canAutosave()) return Promise.resolve(false);  // e.g. a new note with no title yet
      if (!(dirty || needsEmbed)) return Promise.resolve(true);
      return fetch(saveUrl, { method: "POST", body: payload("leave"), headers: { "Accept": "application/json" } })
        .then(function (r) { dirty = !r.ok; needsEmbed = false; return r.ok; })
        .catch(function () { return false; });
    };
    // Quitting the app: the page doesn't get pagehide, so save now, and
    // wait for the answer (a synchronous request, so the app can await it).
    window.__zettelQuit = function () {
      clearTimeout(timer);
      if (submitting) return "clean";
      if (!auto) return dirty ? "unsaved" : "clean";
      if (lockedOut) return dirty ? "locked" : "clean";
      if (!(dirty || needsEmbed || inflight)) return "clean";
      if (!canAutosave()) return dirty ? "untitled" : "clean";
      try {
        var x = new XMLHttpRequest();
        x.open("POST", saveUrl, false);
        x.setRequestHeader("Accept", "application/json");
        x.send(payload("leave"));
        if (x.status === 423) { lockedOut = true; dirty = true; showInlineUnlock(); return "locked"; }
        if (x.status >= 200 && x.status < 300) {
          var j = null;
          try { j = JSON.parse(x.responseText); } catch (err) { /* not JSON */ }
          if (!j || j.ok !== false) {
            dirty = false; needsEmbed = false; setStatus("saved");
            if (!noteId && j && j.edit_url) becameNote(j);
            return "saved";
          }
        }
      } catch (err) { /* fall through */ }
      dirty = true; setStatus("error");
      return "failed";
    };
    window.addEventListener("beforeunload", function (e) {
      if (unsafeToLeave()) { e.preventDefault(); e.returnValue = ""; }
    });
    document.querySelectorAll("[data-template-link]").forEach(function (a) {
      a.addEventListener("click", function (e) {
        if (dirty && !window.confirm("Start again from this template? What you've typed so far will be lost.")) { e.preventDefault(); return; }
        allowLeave = true; submitting = true;  // nothing worth saving from the old start
      });
    });
    if (cancel) cancel.addEventListener("click", function (e) {
      if (unsafeToLeave() && !window.confirm("Leave without saving? Your changes will be lost.")) e.preventDefault();
      else allowLeave = true;
    });

    // Caret position inside the textarea, via a hidden mirror element.
    var MIRROR_PROPS = ["boxSizing", "width", "paddingTop", "paddingRight", "paddingBottom", "paddingLeft",
      "borderTopWidth", "borderRightWidth", "borderBottomWidth", "borderLeftWidth", "fontStyle", "fontVariant",
      "fontWeight", "fontStretch", "fontSize", "lineHeight", "fontFamily", "textAlign", "textTransform",
      "textIndent", "letterSpacing", "wordSpacing", "tabSize"];
    var caretXY = function (pos) {
      var cs = window.getComputedStyle(ta);
      var m = document.createElement("div");
      MIRROR_PROPS.forEach(function (p) { m.style[p] = cs[p]; });
      m.style.position = "absolute"; m.style.visibility = "hidden"; m.style.top = "0"; m.style.left = "-9999px";
      m.style.whiteSpace = "pre-wrap"; m.style.wordWrap = "break-word"; m.style.overflow = "hidden";
      m.style.width = ta.offsetWidth + "px";
      m.textContent = ta.value.substring(0, pos);
      var span = document.createElement("span");
      span.textContent = ta.value.substring(pos) || ".";
      m.appendChild(span);
      document.body.appendChild(m);
      var lh = parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.6;
      var out = { top: span.offsetTop, left: span.offsetLeft, height: lh };
      document.body.removeChild(m);
      return out;
    };

    var pState = null; // {mode, start, query}
    var pItems = [], pSel = 0, pReq = 0;
    var excludeId = body.getAttribute("data-note-id") || "";

    var closePicker = function () { if (picker) picker.hidden = true; pState = null; };

    var placePicker = function () {
      var xy = caretXY(pState.start);
      var maxLeft = wrap.clientWidth - Math.min(420, wrap.clientWidth - 12);
      picker.style.top = (ta.offsetTop + xy.top + xy.height + 6) + "px";
      picker.style.left = Math.max(0, Math.min(xy.left, maxLeft)) + "px";
    };

    var drawPicker = function () {
      if (!pState) return;
      if (pState.mode === "note") {
        pTitle.textContent = "Link to a note";
        pFoot.innerHTML = "<span>↵ link</span><span>⇧↵ link with its title as the text</span><span>esc close</span>";
      } else {
        pTitle.textContent = "Cite from your bibliography";
        pFoot.innerHTML = "<span>↵ insert citation</span><span>esc close</span>";
      }
      pCount.textContent = pItems.length ? pItems.length + (pItems.length === 1 ? " match" : " matches") : "";
      if (!pItems.length) {
        pList.innerHTML = '<li class="picker-empty">' + (pState.mode === "cite"
          ? (pState.query ? "No references match. Is your .bib file set in Settings?" : "Type an author, title or citekey…")
          : "No notes match yet. Keep typing, or press esc.") + "</li>";
        return;
      }
      pList.innerHTML = pItems.map(function (it, i) {
        var cls = "picker-item" + (i === pSel ? " sel" : "");
        if (pState.mode === "note") {
          var sub = it.match === "body" && it.snip ? escMarks(it.snip)
            : esc((KIND_LABEL[it.kind] || "Research") + " · " + (it.modified || "").slice(0, 10) + (it.match === "title" ? " · in the title" : ""));
          return '<li class="' + cls + '" role="option" aria-selected="' + (i === pSel) + '" data-i="' + i + '">' + kindTile(it.kind) +
            '<span><span class="t">' + esc(it.title) + '</span><br><span class="s">' + sub + "</span></span></li>";
        }
        return '<li class="' + cls + '" role="option" aria-selected="' + (i === pSel) + '" data-i="' + i + '">' +
          '<span class="kind-tile" style="background: var(--cite-bg); color: var(--cite-text); font-weight: 900">@</span>' +
          '<span><span class="t">' + esc(it.author || it.citekey) + (it.year ? " " + esc(it.year) : "") + '</span><br><span class="s">' +
          esc(it.title || "") + " · @" + esc(it.citekey) + "</span></span></li>";
      }).join("");
    };

    var fetchPicker = function () {
      if (!pState) return;
      var reqId = ++pReq;
      var url = pState.mode === "note"
        ? "/api/notes/search?limit=8&q=" + encodeURIComponent(pState.query) + "&exclude=" + encodeURIComponent(excludeId)
        : "/api/citekeys?q=" + encodeURIComponent(pState.query);
      fetch(url).then(function (r) { return r.json(); }).then(function (data) {
        if (reqId !== pReq || !pState) return;
        pItems = data || [];
        pSel = 0;
        drawPicker();
      }).catch(function () {});
    };
    var fetchPickerSoon = debounce(fetchPicker, 110);

    var detect = function () {
      var caret = ta.selectionStart;
      if (caret !== ta.selectionEnd) { closePicker(); return; }
      var before = ta.value.slice(0, caret);
      var m = before.match(/\[\[([^\[\]\n|]{0,60})$/);
      if (m) {
        var st = { mode: "note", start: caret - m[0].length, query: m[1] };
        openPicker(st);
        return;
      }
      m = before.match(/(^|[\s(\[;,])@([A-Za-z0-9_:\-]{0,60})$/);
      if (m) {
        openPicker({ mode: "cite", start: caret - m[2].length - 1, query: m[2] });
        return;
      }
      closePicker();
    };
    var openPicker = function (st) {
      var changed = !pState || pState.mode !== st.mode || pState.query !== st.query || pState.start !== st.start;
      pState = st;
      picker.hidden = false;
      placePicker();
      if (changed) { drawPicker(); fetchPickerSoon(); }
    };

    var insertPick = function (it, withAlias) {
      if (!pState || !it) return;
      var start = pState.start, end = ta.selectionStart;
      var text;
      if (pState.mode === "note") {
        var after = ta.value.slice(end, end + 2);
        if (after === "]]") end += 2;
        text = withAlias ? "[[" + it.id + "|" + it.title + "]]" : "[[" + it.id + "]]";
      } else {
        text = "@" + it.citekey;
      }
      ta.focus();
      ta.setRangeText(text, start, end, "end");
      closePicker();
      ta.dispatchEvent(new Event("input", { bubbles: true }));
    };

    ta.addEventListener("input", function () { autosize(); detect(); refreshLinksSoon(); });
    ta.addEventListener("click", detect);
    ta.addEventListener("keyup", function (e) {
      if (["ArrowLeft", "ArrowRight", "Home", "End"].indexOf(e.key) >= 0) detect();
    });
    ta.addEventListener("blur", function () { setTimeout(function () { if (document.activeElement !== ta) closePicker(); }, 150); });
    ta.addEventListener("keydown", function (e) {
      if (!pState || picker.hidden) return;
      if (e.key === "ArrowDown") { pSel = Math.min(pSel + 1, pItems.length - 1); drawPicker(); e.preventDefault(); }
      else if (e.key === "ArrowUp") { pSel = Math.max(pSel - 1, 0); drawPicker(); e.preventDefault(); }
      else if ((e.key === "Enter" || e.key === "Tab") && pItems.length) { insertPick(pItems[pSel], e.shiftKey); e.preventDefault(); }
      else if (e.key === "Escape") { closePicker(); e.preventDefault(); e.stopPropagation(); }
    });
    pList.addEventListener("mousedown", function (e) {
      var li = e.target.closest(".picker-item");
      if (!li) return;
      e.preventDefault();
      insertPick(pItems[parseInt(li.getAttribute("data-i"), 10)], e.shiftKey);
    });
    window.addEventListener("resize", function () { autosize(); if (pState) placePicker(); });

    /* Qualitative coding: wrap the selected passage as [passage]{.code}. */
    var cp = $("#code-picker"), cpInput = $("#code-picker-input"), cpList = $("#code-picker-list"), cpCount = $("#code-picker-count");
    var cpRange = null, cpItems = [], cpSel = -1, cpReq = 0;
    var normCode = function (s) {
      return (s || "").trim().toLowerCase().replace(/[\s_\/–—]+/g, "-").replace(/[^\p{L}\p{N}_-]/gu, "").replace(/^-+|-+$/g, "").slice(0, 60);
    };
    var cpTokens = function () { return cpInput.value.split(","); };
    var closeCodePicker = function (refocus) { if (cp) cp.hidden = true; cpRange = null; if (refocus) ta.focus(); };
    var drawCodes = function () {
      var last = normCode(cpTokens().slice(-1)[0]);
      cpCount.textContent = cpItems.length ? cpItems.length + (cpItems.length === 1 ? " code" : " codes") : "";
      var rows = cpItems.map(function (it, i) {
        return '<li class="picker-item' + (i === cpSel ? " sel" : "") + '" role="option" data-i="' + i + '">' +
          '<span class="qcode-tag">' + esc(it.code) + '</span><span class="s">' + esc(it.count + (it.count === 1 ? " passage" : " passages")) +
          (it.description ? " · " + esc(it.description.slice(0, 70)) : "") + "</span></li>";
      });
      var exists = cpItems.some(function (it) { return it.code === last; });
      if (last && !exists) rows.unshift('<li class="picker-empty">Enter adds the new code <span class="qcode-tag">' + esc(last) + "</span></li>");
      if (!rows.length) rows.push('<li class="picker-empty">No codes yet. Type a name, such as <em>trust</em>, and press Enter.</li>');
      cpList.innerHTML = rows.join("");
    };
    var fetchCodes = debounce(function () {
      var id = ++cpReq;
      fetch("/api/codes?q=" + encodeURIComponent(normCode(cpTokens().slice(-1)[0])))
        .then(function (r) { return r.json(); })
        .then(function (list) { if (id !== cpReq) return; cpItems = list || []; cpSel = -1; drawCodes(); })
        .catch(function () {});
    }, 90);
    var applyCodes = function (codes) {
      var seen = {}, list = [];
      codes.map(normCode).forEach(function (c) { if (c && !seen[c]) { seen[c] = 1; list.push(c); } });
      if (!list.length || !cpRange) return;
      var s = cpRange[0], e2 = cpRange[1], text = ta.value.slice(s, e2), out;
      var m = text.match(/^\[([\s\S]*)\]\{((?:\s*\.[\p{L}\p{N}_-]+)+)\s*\}$/u);
      if (m) {
        var have = (m[2].match(/\.[\p{L}\p{N}_-]+/gu) || []).map(function (x) { return x.slice(1); });
        list.forEach(function (c) { if (have.indexOf(c) < 0) have.push(c); });
        out = "[" + m[1] + "]{" + have.map(function (c) { return "." + c; }).join(" ") + "}";
      } else {
        out = "[" + text + "]{" + list.map(function (c) { return "." + c; }).join(" ") + "}";
      }
      ta.focus();
      ta.setRangeText(out, s, e2, "end");
      closeCodePicker(false);
      ta.dispatchEvent(new Event("input", { bubbles: true }));
      toast(list.length === 1 ? "Coded: " + list[0] : "Coded: " + list.join(", "));
    };
    var openCodePicker = function () {
      if (!cp) return;
      var s = ta.selectionStart, e2 = ta.selectionEnd;
      while (s < e2 && /\s/.test(ta.value[s])) s++;
      while (e2 > s && /\s/.test(ta.value[e2 - 1])) e2--;
      if (s >= e2) { toast("Select a passage first"); ta.focus(); return; }
      if (/\n\s*\n/.test(ta.value.slice(s, e2))) { toast("Code one paragraph at a time"); return; }
      cpRange = [s, e2];
      closePicker();
      var xy = caretXY(e2);
      var maxLeft = wrap.clientWidth - Math.min(420, wrap.clientWidth - 12);
      cp.style.top = (ta.offsetTop + xy.top + xy.height + 6) + "px";
      cp.style.left = Math.max(0, Math.min(xy.left, maxLeft)) + "px";
      cp.hidden = false;
      cpInput.value = ""; cpItems = []; cpSel = -1; drawCodes(); fetchCodes();
      cpInput.focus();
    };
    window.__zettelCode = openCodePicker;
    var codeBtn = $("#code-btn");
    if (codeBtn) {
      // Keep the textarea's selection when the button is pressed.
      codeBtn.addEventListener("mousedown", function (e) { e.preventDefault(); });
      codeBtn.addEventListener("click", openCodePicker);
    }
    if (cpInput) {
      cpInput.addEventListener("input", function () { fetchCodes(); drawCodes(); });
      cpInput.addEventListener("keydown", function (e) {
        if (e.key === "ArrowDown") { cpSel = Math.min(cpSel + 1, cpItems.length - 1); drawCodes(); e.preventDefault(); }
        else if (e.key === "ArrowUp") { cpSel = Math.max(cpSel - 1, -1); drawCodes(); e.preventDefault(); }
        else if (e.key === "Tab" && cpItems.length) {
          var t = cpTokens(); t[t.length - 1] = (t.length > 1 ? " " : "") + cpItems[Math.max(cpSel, 0)].code;
          cpInput.value = t.join(","); e.preventDefault(); fetchCodes();
        }
        else if (e.key === "Enter") {
          e.preventDefault(); e.stopPropagation();
          var toks = cpTokens();
          if (cpSel >= 0 && cpItems[cpSel]) toks[toks.length - 1] = cpItems[cpSel].code;
          applyCodes(toks);
        }
        else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeCodePicker(true); }
      });
      cpInput.addEventListener("blur", function () { setTimeout(function () { if (document.activeElement !== cpInput) closeCodePicker(false); }, 180); });
      cpList.addEventListener("mousedown", function (e) {
        var li = e.target.closest(".picker-item");
        if (!li) return;
        e.preventDefault();
        var toks = cpTokens(); toks[toks.length - 1] = cpItems[parseInt(li.getAttribute("data-i"), 10)].code;
        applyCodes(toks);
      });
    }

    // "Links in this note": resolve [[id]] to titles as you type.
    var linksList = $("#links-list"), linksCount = $("#links-count");
    var lastIds = "";
    var refreshLinks = function () {
      if (!linksList) return;
      var ids = [], seen = {};
      var re = /\[\[(\d{14})(?:\|[^\]]+)?\]\]/g, m;
      while ((m = re.exec(ta.value))) { if (!seen[m[1]]) { seen[m[1]] = 1; ids.push(m[1]); } }
      var key = ids.join(",");
      if (key === lastIds) return;
      lastIds = key;
      if (linksCount) linksCount.textContent = ids.length ? ids.length : "";
      if (!ids.length) {
        linksList.innerHTML = '<li class="rail-empty">None yet. Type <kbd>[[</kbd> in the text to link a note.</li>';
        return;
      }
      fetch("/api/notes/titles?ids=" + encodeURIComponent(key)).then(function (r) { return r.json(); }).then(function (map) {
        linksList.innerHTML = ids.map(function (id) {
          var t = map[id];
          return t ? '<li class="rail-link">' + kindTile(t.kind) + "<span>" + esc(t.title) + "</span></li>"
                   : '<li class="rail-link"><span class="kind-tile" style="background: var(--warn-bg)"></span><span class="muted">Missing note ' + esc(id) + "</span></li>";
        }).join("");
      }).catch(function () {});
    };
    var refreshLinksSoon = debounce(refreshLinks, 400);
    refreshLinks();

    // Tag suggestions.
    var sBtn = $("#suggest-tags-btn"), sOut = $("#suggest-tags-result"), tagsInput = $("#tags-input");
    if (sBtn && sOut && tagsInput) {
      sBtn.addEventListener("click", function () {
        var title = ($("#title-input") || {}).value || "";
        if (!title.trim() && !ta.value.trim()) { sOut.innerHTML = '<span class="suggest-msg">Add a title or some text first.</span>'; return; }
        sBtn.disabled = true; sBtn.textContent = "Thinking…"; sOut.innerHTML = "";
        var params = new URLSearchParams(); params.set("title", title); params.set("body", ta.value);
        params.set("note_id", body.getAttribute("data-note-id") || "");
        var kindPick = editForm.querySelector('input[name="kind"]:checked');
        params.set("kind", kindPick ? kindPick.value : "");
        fetch("/api/tags/suggest", { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: params.toString() })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            if (!data.ok) { sOut.innerHTML = '<span class="suggest-msg err">' + esc(data.message) + "</span>"; return; }
            if (!data.tags.length) { sOut.innerHTML = '<span class="suggest-msg">No suggestions this time.</span>'; return; }
            data.tags.forEach(function (tag) {
              var chip = document.createElement("button");
              chip.type = "button"; chip.className = "suggest-chip"; chip.textContent = "+ " + tag;
              chip.addEventListener("click", function () {
                var existing = tagsInput.value.split(",").map(function (s) { return s.trim(); }).filter(Boolean);
                if (existing.indexOf(tag) < 0) { existing.push(tag); tagsInput.value = existing.join(", "); markDirty(); }
                chip.classList.add("used");
              });
              sOut.appendChild(chip);
            });
          })
          .catch(function (err) { sOut.innerHTML = '<span class="suggest-msg err">Request failed: ' + esc(err) + "</span>"; })
          .finally(function () { sBtn.disabled = false; sBtn.textContent = "Suggest tags"; });
      });
    }

    // Threads (new notes only): show for research notes, reveal the right input.
    var threadFs = $("#thread-fieldset");
    if (threadFs) {
      var syncThread = function () {
        var kind = ($("input[name=kind]:checked") || {}).value || "research";
        threadFs.hidden = kind !== "research";
        var action = ($("input[name=thread_action]:checked") || {}).value || "";
        $$("[data-thread-when]", threadFs).forEach(function (el) { el.hidden = el.getAttribute("data-thread-when") !== action; });
      };
      $$("input[name=kind], input[name=thread_action]").forEach(function (r) { r.addEventListener("change", syncThread); });
      syncThread();
    }
  }

  /* ------------------------------------------------------------------ *
   * Interrogate: stream replies into the side panel.
   * ------------------------------------------------------------------ */

  var chatForm = $("#chat-form");
  if (chatForm) {
    var chatInput = $("#chat-input"), chatBtn = $("#chat-submit"), chatStatus = $("#chat-status"), chatLog = $("#chat-log");
    var railScroll = chatForm.closest(".rail");
    var scrollDown = function () { if (railScroll) railScroll.scrollTop = railScroll.scrollHeight; };
    scrollDown();
    $$(".prompt-chip").forEach(function (chip) {
      chip.addEventListener("click", function () { chatInput.value = chip.getAttribute("data-prompt"); chatInput.focus(); });
    });
    chatInput.addEventListener("keydown", function (e) {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); chatForm.requestSubmit ? chatForm.requestSubmit() : chatForm.submit(); }
    });
    var addUser = function (text) {
      var empty = $(".rail-empty", chatLog); if (empty) empty.remove();
      var d = document.createElement("div"); d.className = "chat-msg-user"; d.textContent = text; chatLog.appendChild(d);
    };
    var addReply = function () {
      var d = document.createElement("div"); d.className = "chat-msg-assistant";
      d.innerHTML = '<span class="chat-role">Model reply</span><div class="chat-content">…</div>';
      chatLog.appendChild(d); return $(".chat-content", d);
    };
    chatForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var message = chatInput.value.trim();
      if (!message) return;
      addUser(message);
      var out = addReply();
      scrollDown();
      chatInput.value = ""; chatInput.disabled = true; chatBtn.disabled = true; chatStatus.textContent = "Thinking…";
      fetch(chatForm.getAttribute("data-stream-url"), {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({ message: message }),
      }).then(function (response) {
        if (!response.ok) { out.textContent = "HTTP " + response.status + " from the app"; return; }
        var reader = response.body.getReader(), decoder = new TextDecoder();
        var buffer = "", text = "", first = true;
        var pump = function () {
          return reader.read().then(function (res) {
            if (res.done) return;
            buffer += decoder.decode(res.value, { stream: true });
            var idx;
            while ((idx = buffer.indexOf("\n\n")) !== -1) {
              var raw = buffer.slice(0, idx); buffer = buffer.slice(idx + 2);
              var ev = "message", data = "";
              raw.split("\n").forEach(function (line) {
                if (line.indexOf("event: ") === 0) ev = line.slice(7).trim();
                else if (line.indexOf("data: ") === 0) data = line.slice(6);
              });
              var payload; try { payload = JSON.parse(data); } catch (err) { payload = data; }
              if (ev === "chunk") {
                if (first) { out.textContent = ""; first = false; chatStatus.textContent = "Writing…"; }
                text += payload; out.textContent = text; scrollDown();
              } else if (ev === "error") {
                out.textContent = "Something went wrong: " + payload; chatStatus.textContent = "Error";
              } else if (ev === "done") {
                chatStatus.textContent = "⌘↵ to send";
              }
            }
            return pump();
          });
        };
        return pump();
      }).catch(function (err) {
        out.textContent = "The request failed: " + err.message; chatStatus.textContent = "Error";
      }).finally(function () {
        chatInput.disabled = false; chatBtn.disabled = false; chatInput.focus();
      });
    });
  }

  /* ------------------------------------------------------------------ *
   * Locked notes: close them after the idle time chosen in Settings
   * (the server does the same check on the next request), and show
   * progress while a password is checked (Argon2id takes a moment).
   * ------------------------------------------------------------------ */
  var idleLimit = parseInt(body.getAttribute("data-vault-idle") || "0", 10);
  if (ctx.vault === "open" && idleLimit > 0) {
    var lastInput = Date.now(), locking = false;
    var bump = function () { lastInput = Date.now(); };
    ["keydown", "mousedown", "mousemove", "wheel", "touchstart", "scroll"].forEach(function (ev) {
      window.addEventListener(ev, bump, { passive: true, capture: true });
    });
    setInterval(function () {
      if (locking || Date.now() - lastInput < idleLimit * 1000) return;
      locking = true;
      var flush = window.__zettelFlush ? window.__zettelFlush() : Promise.resolve(true);
      flush.then(function (saved) {
        if (!saved) { locking = false; lastInput = Date.now(); return; }  // keep unsaved text; try later
        return fetch("/vault/lock", { method: "POST", headers: { "Accept": "application/json" } })
          .then(function () { window.location.reload(); });
      }).catch(function () { locking = false; });
    }, 10000);
  }
  document.querySelectorAll("[data-unlock-form]").forEach(function (f) {
    f.addEventListener("submit", function () {
      var b = f.querySelector('button[type="submit"]');
      if (b) { b.disabled = true; b.textContent = "Unlocking…"; }
    });
  });

  /* Manuscript outline: suggest notes for "Add a note…". */
  document.querySelectorAll("[data-ms-notes]").forEach(function (inp) {
    var dl = document.getElementById(inp.getAttribute("list"));
    var fill = debounce(function () {
      fetch("/api/notes/search?limit=12&q=" + encodeURIComponent(inp.value) + (ctx.noteId ? "&exclude=" + ctx.noteId : ""))
        .then(function (r) { return r.json(); })
        .then(function (list) {
          dl.innerHTML = (list || []).map(function (n) { return '<option value="' + esc(n.title) + " · " + n.id + '">'; }).join("");
        }).catch(function () {});
    }, 120);
    inp.addEventListener("input", fill);
    inp.addEventListener("focus", fill);
  });

  /* ------------------------------------------------------------------ *
   * Welcome tour (5.2): starts by itself once, on the first main page you
   * see; again from the compass button in the sidebar or ⌘K. Each stop
   * spotlights one part of the window; stops whose part isn't on this
   * page are skipped.
   * ------------------------------------------------------------------ */
  // 5.2: the first stops (intro: true) explain the method before the app.
  var SLIPS_ART =
    '<svg class="tour-slips" viewBox="0 0 220 96" width="220" height="96" aria-hidden="true">' +
      '<path d="M58 34 C 88 20, 104 20, 128 30" /><path d="M58 52 C 80 74, 104 80, 124 72" /><path d="M164 42 L 164 58" />' +
      '<rect x="8" y="22" width="54" height="40" rx="7" class="s1"/><rect x="126" y="8" width="76" height="34" rx="7" class="s2"/>' +
      '<rect x="122" y="58" width="84" height="32" rx="7" class="s3"/>' +
      '<line x1="18" y1="36" x2="50" y2="36"/><line x1="18" y1="46" x2="42" y2="46"/>' +
      '<line x1="136" y1="20" x2="190" y2="20"/><line x1="136" y1="29" x2="176" y2="29"/>' +
      '<line x1="132" y1="70" x2="194" y2="70"/><line x1="132" y1="79" x2="170" y2="79"/>' +
    '</svg>';
  var TOUR = [
    { title: "Welcome to Slipbox", mascot: true, next: "Begin",
      body: "<p>Slipbox is a Zettelkasten: a box of short notes in your own words, linked into a web of ideas that grows with your work.</p><p>This tour takes about two minutes: first how a Zettelkasten works, then the app. Use the arrow keys or the buttons, and leave whenever you like.</p><p class='tour-aside'>Know the method already? <button type='button' class='linklike tour-jump'>Go straight to the app</button></p>" },
    { intro: true, art: SLIPS_ART, title: "The idea: a slip-box",
      body: "<p>A Zettelkasten (German for “slip box”, hence this app’s name) is the note-taking method of the sociologist Niklas Luhmann, who kept around 90,000 paper slips over four decades.</p><p>Its value lies less in any single note than in the links between them. Over time a well-linked slip-box starts to answer back: it brings forgotten notes into view and puts ideas side by side that you would not have thought to compare.</p>" },
    { intro: true, title: "Four habits", wide: true,
      body: "<ol class='tour-list'><li><strong>One idea per note.</strong> “Algorithmic governance erodes accountability” is a note; “Notes on chapter 3” is not.</li><li><strong>Your own words.</strong> Rewriting an idea is where understanding happens. Keep what a source says, with its citation, apart from what you make of it.</li><li><strong>Links are the structure.</strong> No folders: when you write a note, link it to the notes it speaks to, and say why in the sentence around the link.</li><li><strong>Write for later.</strong> Each note is material for some future piece of writing, so write it to make sense on its own, months from now.</li></ol>" },
    { intro: true, title: "How Slipbox helps", wide: true, next: "Show me the app",
      body: "<ul class='tour-list'><li>Every note gets a permanent ID, so links survive renaming.</li><li><kbd>[[</kbd> links one note to another, and every note shows what links back to it.</li><li><strong>Threads</strong> keep notes that continue one another in order, like the numbered branches of Luhmann’s slips.</li><li><strong>Manuscripts</strong> gather a line of thought into a draft.</li><li>The four kinds and your tags are loose groupings; the links do the real work.</li></ul><p class='tour-aside'>Help → <em>What a Zettelkasten is</em> has more, with further reading.</p>" },
    { target: "library", title: "Four kinds of note",
      body: "<p>Research, journal, teaching and life notes each have their own list. Links, search and backlinks work across all four.</p>" },
    { target: "new", title: "New note",
      body: "<p><kbd>⌘N</kbd>. Start blank or from a template: interview memo, fieldnote, reading note or lecture plan. <kbd>⇧⌘N</kbd> starts a journal entry.</p>" },
    { target: "search", title: "Find anything",
      body: "<p><kbd>⌘K</kbd> searches every note and runs any command. Filters narrow it down: <code>kind:journal</code>, <code>tag:drones</code>, <code>after:2025</code>.</p>" },
    { target: "list", title: "Your notes",
      body: "<p>The notes in the list you’ve picked, newest first, with a box above to search them.</p>" },
    { target: "edit", title: "Write and edit",
      body: "<p><kbd>⌘E</kbd>. Your note saves itself a moment after you stop typing.</p>" },
    { target: "lock", title: "Lock a note",
      body: "<p>Encrypts the note’s text with a password only you know: for interviews, diaries, anything private. Set it up once in Settings → Locked notes.</p>" },
    { target: "history", title: "History",
      body: "<p>Earlier versions of this note, what changed since, and one-click restore.</p>" },
    { target: "export", title: "Export",
      body: "<p>Word, LaTeX, HTML and more, with your Zotero citations formatted.</p>" },
    { target: "rail", title: "Everything connected to this note",
      body: "<p>Its thread, the notes that link here (with the sentence they’re in), notes that mention it without linking, and related notes.</p><p>The <em>Interrogate</em> tab lets you question the note with an AI model, if you set one up.</p>" },
    { title: "While you write",
      body: "<ul class='tour-keys'><li><kbd>[[</kbd> link another note</li><li><kbd>@</kbd> cite from your Zotero library</li><li><kbd>![[</kbd> embed a note’s whole text</li><li>select a passage, <kbd>⇧⌘C</kbd> to code it for analysis</li></ul>" },
    { target: "browse", title: "Browse",
      body: "<p>Tags and threads, your citations, manuscripts (notes gathered into one draft), the codebook of coded passages, and writing stats.</p>" },
    { target: "settings", title: "Make it yours",
      body: "<p>Themes, fonts and colours; templates; locked notes; AI providers; and your Zotero library.</p>" },
    { target: "tour", title: "That’s the tour", mascot: true, place: "above",
      body: "<p>Find it again here whenever you like. Help, next to it, explains everything in detail.</p><p>Happy note-making.</p>" }
  ];

  var tourEl = null, tourStops = [], tourAt = 0, tourReturnFocus = null;
  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function tourTarget(stop) {
    if (!stop.target) return null;
    var el = document.querySelector('[data-tour="' + stop.target + '"]');
    if (!el) return null;
    var r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4 || r.right < 0 || r.left > window.innerWidth) return null;
    var cs = window.getComputedStyle(el);
    if (cs.visibility === "hidden" || cs.display === "none") return null;
    return el;
  }

  function tourBuild() {
    tourEl = document.createElement("div");
    tourEl.className = "tour" + (reduceMotion ? " no-motion" : "");
    tourEl.innerHTML =
      '<div class="tour-catch"></div>' +
      '<div class="tour-spot" aria-hidden="true"></div>' +
      '<div class="tour-card" role="dialog" aria-modal="true" aria-labelledby="tour-title" aria-describedby="tour-body">' +
        '<div class="tour-art"></div>' +
        '<p class="tour-step" aria-live="polite"></p>' +
        '<h2 id="tour-title"></h2>' +
        '<div id="tour-body" class="tour-body"></div>' +
        '<div class="tour-dots" aria-hidden="true"></div>' +
        '<div class="tour-actions">' +
          '<button type="button" class="btn tour-skip">Skip tour</button><span class="grow"></span>' +
          '<button type="button" class="btn tour-back">Back</button>' +
          '<button type="button" class="btn btn-primary tour-next">Next</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(tourEl);
    tourEl.querySelector(".tour-skip").addEventListener("click", function () { tourEnd(); });
    tourEl.querySelector(".tour-back").addEventListener("click", function () { tourGo(tourAt - 1); });
    tourEl.querySelector(".tour-next").addEventListener("click", function () {
      if (tourAt >= tourStops.length - 1) tourEnd(); else tourToApp(tourAt + 1);
    });
    tourEl.addEventListener("click", function (e) {
      if (e.target.closest && e.target.closest(".tour-jump")) {
        e.preventDefault();
        var first = tourStops.findIndex(function (s) { return s !== tourStops[0] && !s.intro; });
        tourToApp(first > 0 ? first : 1);
      }
    });
    tourEl.querySelector(".tour-catch").addEventListener("click", function (e) { e.preventDefault(); });
  }

  // Moving on to stop i. The first stop about the app itself, reached from a
  // list with no note open: carry on from the latest note, so the note's
  // own buttons are part of the tour.
  function tourToApp(i) {
    var s = tourStops[i];
    var fromIntro = !tourStops[tourAt].target && s && !!s.target;
    if (fromIntro && !ctx.noteId && document.querySelector('[data-tour="list"] a[href*="/note/"], .note-card a[href*="/note/"], a.card[href*="/note/"]')) {
      window.location.href = "/tour?step=" + (i + 1); return;
    }
    tourGo(i);
  }

  function tourPlace() {
    if (!tourEl) return;
    var stop = tourStops[tourAt], el = tourTarget(stop);
    var spot = tourEl.querySelector(".tour-spot"), card = tourEl.querySelector(".tour-card");
    var vw = window.innerWidth, vh = window.innerHeight, gap = 16, pad = 6;
    var cw = card.offsetWidth, ch = card.offsetHeight;
    if (!el) {
      tourEl.classList.add("centered");
      spot.style.cssText = "top:" + (vh / 2) + "px;left:" + (vw / 2) + "px;width:0;height:0";
      card.style.left = Math.max(12, (vw - cw) / 2) + "px";
      card.style.top = Math.max(12, (vh - ch) / 2) + "px";
      return;
    }
    tourEl.classList.remove("centered");
    var r = el.getBoundingClientRect();
    var top = Math.max(4, r.top - pad), left = Math.max(4, r.left - pad);
    var bottom = Math.min(vh - 4, r.bottom + pad), right = Math.min(vw - 4, r.right + pad);
    spot.style.cssText = "top:" + top + "px;left:" + left + "px;width:" + (right - left) + "px;height:" + (bottom - top) + "px";
    var clampX = function (x) { return Math.max(12, Math.min(x, vw - cw - 12)); };
    var clampY = function (y) { return Math.max(12, Math.min(y, vh - ch - 12)); };
    var midY = clampY(r.top + r.height / 2 - ch / 2), midX = clampX(r.left + r.width / 2 - cw / 2);
    var options = {
      right: [right + gap, midY, right + gap + cw <= vw - 8],
      left: [left - gap - cw, midY, left - gap - cw >= 8],
      below: [midX, bottom + gap, bottom + gap + ch <= vh - 8],
      above: [midX, top - gap - ch, top - gap - ch >= 8]
    };
    var order = ["right", "left", "below", "above"];
    if (stop.place) order.unshift(stop.place);
    options = order.map(function (k) { return options[k]; });
    var pick = options.filter(function (o) { return o[2]; })[0] || [clampX((vw - cw) / 2), clampY((vh - ch) / 2)];
    card.style.left = pick[0] + "px";
    card.style.top = pick[1] + "px";
  }

  function tourGo(i) {
    if (i < 0 || i >= tourStops.length) return;
    tourAt = i;
    var stop = tourStops[i], card = tourEl.querySelector(".tour-card");
    var art = tourEl.querySelector(".tour-art"), tpl = document.getElementById("tour-mascot");
    art.innerHTML = stop.mascot && tpl ? tpl.innerHTML : (stop.art || "");
    art.hidden = !stop.mascot && !stop.art;
    card.classList.toggle("wide", !!stop.wide);
    tourEl.querySelector(".tour-step").textContent = (i + 1) + " of " + tourStops.length;
    tourEl.querySelector("#tour-title").textContent = stop.title;
    tourEl.querySelector("#tour-body").innerHTML = stop.body;
    tourEl.querySelector(".tour-dots").innerHTML = tourStops.map(function (_, k) {
      return '<span class="' + (k === i ? "on" : "") + '"></span>';
    }).join("");
    tourEl.querySelector(".tour-back").hidden = i === 0;
    tourEl.querySelector(".tour-skip").hidden = i === tourStops.length - 1;
    var next = tourEl.querySelector(".tour-next");
    next.textContent = stop.next || (i === tourStops.length - 1 ? "Done" : "Next");
    var el = tourTarget(stop);
    if (el && el.scrollIntoView) el.scrollIntoView({ block: "nearest" });
    tourPlace();
    next.focus();
  }

  function tourStart(at) {
    if (tourEl) return;
    closePalette && closePalette();
    tourStops = TOUR.filter(function (s) { return !s.target || tourTarget(s); });
    tourReturnFocus = document.activeElement;
    tourBuild();
    document.documentElement.classList.add("touring");
    tourGo(Math.max(0, Math.min((at || 1) - 1, tourStops.length - 1)));
    // Shown once is enough: don't start it by itself again even if the
    // window is closed mid-tour.
    if (body.getAttribute("data-tour-auto") === "1") markTourSeen();
  }
  function markTourSeen() {
    body.removeAttribute("data-tour-auto");
    fetch("/api/tour/seen", { method: "POST", headers: { "Accept": "application/json" } }).catch(function () {});
  }

  function tourEnd() {
    if (!tourEl) return;
    tourEl.remove(); tourEl = null;
    document.documentElement.classList.remove("touring");
    markTourSeen();
    if (/[?&]tour=\d/.test(window.location.search)) {
      try { window.history.replaceState(null, "", window.location.pathname); } catch (err) { /* ignore */ }
    }
    if (tourReturnFocus && tourReturnFocus.focus) tourReturnFocus.focus();
  }
  window.__zettelTour = function () { tourStart(1); };

  document.addEventListener("keydown", function (e) {
    if (!tourEl) return;
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); tourEnd(); }
    else if (e.key === "ArrowRight") { e.preventDefault(); e.stopPropagation(); tourEl.querySelector(".tour-next").click(); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); e.stopPropagation(); if (tourAt > 0) tourGo(tourAt - 1); }
    else if (e.key === "Tab") {
      var f = Array.prototype.filter.call(tourEl.querySelectorAll(".tour-card button"), function (b) { return !b.hidden; });
      var i = f.indexOf(document.activeElement);
      e.preventDefault();
      f[(i + (e.shiftKey ? -1 : 1) + f.length) % f.length].focus();
    }
    // Keep the app's own shortcuts (⌘K, ⌘N…) out of the way mid-tour, but
    // don't preventDefault: ⌘Q, ⌘W and copy must still reach the Mac.
    else if (e.metaKey || e.ctrlKey) { e.stopPropagation(); }
  }, true);
  window.addEventListener("resize", function () { tourPlace(); });

  document.querySelectorAll("[data-start-tour]").forEach(function (a) {
    a.addEventListener("click", function (e) {
      // On a note, tour right here; elsewhere, go to a note first.
      if (ctx.noteId) { e.preventDefault(); tourStart(1); }
    });
  });
  var tourParam = /[?&]tour=(\d+)/.exec(window.location.search);
  if (body.getAttribute("data-tour-auto") === "1" || tourParam) {
    setTimeout(function () { tourStart(tourParam ? parseInt(tourParam[1], 10) : 1); }, reduceMotion ? 50 : 450);
  }

  // Forms marked data-autosubmit save as soon as a switch changes (5.2:
  // Settings → Updates lives outside the main settings form).
  document.querySelectorAll("form[data-autosubmit]").forEach(function (f) {
    f.addEventListener("change", function () { f.submit(); });
  });

  // Copy buttons: data-copy="#id" copies that element's text (5.2).
  document.querySelectorAll("[data-copy]").forEach(function (b) {
    b.addEventListener("click", function () {
      var src = document.querySelector(b.getAttribute("data-copy"));
      if (!src) return;
      var text = src.textContent.trim(), done = function () { b.textContent = "Copied"; setTimeout(function () { b.textContent = "Copy"; }, 1600); };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, function () { selectText(src); });
      } else selectText(src);
    });
  });
  function selectText(el) {
    var r = document.createRange(); r.selectNodeContents(el);
    var sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(r);
  }

  /* Outside links (5.2): a link to another website would otherwise replace
   * Slipbox in its own window, with no way back. Send it to the browser
   * (or the mail app) through /open-link instead. */
  function outsideHref(a) {
    if (!a || !a.getAttribute("href") || a.hasAttribute("download")) return null;
    var u;
    try { u = new URL(a.getAttribute("href"), window.location.href); } catch (err) { return null; }
    if (u.protocol === "mailto:") return u.href;
    if ((u.protocol === "http:" || u.protocol === "https:") && u.origin !== window.location.origin) return u.href;
    return null;
  }
  function openOutside(e) {
    if (e.defaultPrevented || (e.type === "auxclick" && e.button !== 1)) return;
    var a = e.target && e.target.closest ? e.target.closest("a[href]") : null;
    var href = outsideHref(a);
    if (!href) return;
    e.preventDefault();
    e.stopPropagation();
    fetch("/open-link", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify({ url: href })
    }).then(function (r) {
      if (r.ok) toast("Opened in your browser");
      else return r.json().then(function (d) { toast(d.error || "That link couldn’t be opened."); });
    }).catch(function () { toast("That link couldn’t be opened."); });
  }
  document.addEventListener("click", openOutside, true);
  document.addEventListener("auxclick", openOutside, true);

  /* Back (5.2): on Help, Export and Open beside, esc returns to the note.
   * Overlays (palette, pickers, tour) handle esc first; in a text field the
   * first esc just leaves the field. */
  var backLink = document.querySelector("a[data-back]");
  if (backLink) {
    var panelOpenAtEsc = false;
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") panelOpenAtEsc = !!(app && (app.classList.contains("rail-open") || app.classList.contains("list-open")));
    }, true);
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape" || e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return;
      if (panelOpenAtEsc || tourEl) return;
      var pal = document.getElementById("palette");
      if (pal && !pal.hidden) return;
      var a = document.activeElement;
      if (a && a !== document.body && a.matches && a.matches("input, textarea, select, [contenteditable]")) {
        a.blur(); e.preventDefault(); return;
      }
      e.preventDefault();
      window.location.href = backLink.getAttribute("href");
    });
  }

  /* Confirmations (5.2): forms marked data-confirm ask first. These used to
   * be inline onsubmit handlers, which the content-security policy blocks. */
  document.addEventListener("submit", function (e) {
    var f = e.target;
    var msg = f && f.getAttribute && f.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) { e.preventDefault(); e.stopImmediatePropagation(); }
  }, true);
  document.querySelectorAll("[data-history-back]").forEach(function (b) {
    b.addEventListener("click", function (e) { e.preventDefault(); window.history.back(); });
  });

  // One-off notices from the server (5.2), shown a little longer than toasts.
  var flashMsg = body.getAttribute("data-flash");
  if (flashMsg) {
    setTimeout(function () {
      toast(flashMsg);
      clearTimeout(toastTimer);
      toastTimer = setTimeout(function () { var el = $("#toast"); if (el) el.classList.remove("show"); }, 6000);
    }, 300);
  }
})();
