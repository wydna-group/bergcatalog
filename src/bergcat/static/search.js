/* Catalogue search.
 *
 * Filters in place rather than reordering, so results stay in the drawer's
 * filing sequence — a catalogue reordered by relevance is no longer a
 * catalogue. Everything is client-side; the page is a static file.
 *
 * Beyond text search there are facets the correction workflow actually needs:
 * which cards are still unverified, and which the machine flagged as uncertain.
 * Together those are the worklist.
 */
(function () {
  "use strict";

  var index = [];        // one entry per card, built once
  var debounceTimer = null;

  // --- normalisation -------------------------------------------------------

  /* Fold case and accents so "Bronte" finds "Brontë". The scans also carry
     machine-reading errors, but fuzzy matching would surface noise on a corpus
     this size; exactness plus the flagged-uncertain facet works better. */
  function norm(s) {
    return (s || "")
      .toLowerCase()
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "")
      .replace(/\s+/g, " ")
      .trim();
  }

  function buildIndex() {
    index = [];
    document.querySelectorAll(".card-record").forEach(function (rec) {
      var face = rec.querySelector(".card");
      var main = rec.querySelector(".ab-mainEntry");
      var desc = rec.querySelector(".description");
      var notes = rec.querySelectorAll(".card-note");
      var noteText = "";
      var hasUncertain = false;
      notes.forEach(function (n) {
        noteText += " " + n.textContent;
        var label = (n.querySelector(".note-label") || {}).textContent || "";
        if (/transcription/i.test(label)) hasUncertain = true;
      });

      var id = rec.id;
      var m = /^([a-z0-9]+)-p(\d+)-r(\d)c(\d)$/.exec(id) || [];
      index.push({
        rec: rec,
        id: id,
        volume: m[1] || "",
        page: m[2] || "",
        idText: norm(id),
        main: norm(main ? main.textContent : ""),
        face: norm(face ? face.textContent : ""),
        notes: norm(noteText),
        desc: norm(desc ? desc.textContent : ""),
        status: rec.getAttribute("data-status") || "",
        uncertain: hasUncertain,
        continuation: !!rec.querySelector(".badge-cont"),
        xref: !!rec.querySelector(".badge-xref"),
      });
    });
    return index;
  }

  // --- query ---------------------------------------------------------------

  /* Quoted phrases stay intact; bare words must all match (AND), which is what
     people expect when narrowing a catalogue. */
  function parseQuery(q) {
    var terms = [];
    var re = /"([^"]+)"|(\S+)/g, m;
    while ((m = re.exec(q))) terms.push(norm(m[1] || m[2]));
    return terms.filter(Boolean);
  }

  function haystack(entry, scope) {
    if (scope === "main") return entry.main;
    if (scope === "notes") return entry.notes;
    return entry.face + " " + entry.notes + " " + entry.desc + " " + entry.idText;
  }

  function matches(entry, terms, scope, facet) {
    if (facet === "unverified" && entry.status !== "unverified") return false;
    if (facet === "verified" && entry.status !== "verified") return false;
    if (facet === "uncertain" && !entry.uncertain) return false;
    if (facet === "continuation" && !entry.continuation) return false;
    if (facet === "xref" && !entry.xref) return false;
    if (!terms.length) return true;
    var hay = haystack(entry, scope);
    for (var i = 0; i < terms.length; i++) {
      if (hay.indexOf(terms[i]) === -1) return false;
    }
    return true;
  }

  // --- highlighting --------------------------------------------------------
  // Only ever touches text nodes in the rendered face. The TEI source lives in
  // a separate textarea and is never modified by search.

  function clearMarks(root) {
    root.querySelectorAll("mark.hit").forEach(function (mk) {
      var parent = mk.parentNode;
      parent.replaceChild(document.createTextNode(mk.textContent), mk);
      parent.normalize();
    });
  }

  function markTerms(root, terms) {
    if (!terms.length) return;
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: function (n) {
        if (!n.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
        if (n.parentNode.closest("mark.hit")) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      },
    });
    var texts = [], n;
    while ((n = walker.nextNode())) texts.push(n);

    texts.forEach(function (node) {
      var hay = norm(node.nodeValue);
      var hit = null, at = -1;
      for (var i = 0; i < terms.length; i++) {
        var p = hay.indexOf(terms[i]);
        if (p !== -1 && (at === -1 || p < at)) { at = p; hit = terms[i]; }
      }
      if (at === -1) return;
      // norm() collapses whitespace, so offsets can drift on multi-space runs;
      // re-find case-insensitively in the raw text to place the mark exactly.
      var raw = node.nodeValue;
      var idx = raw.toLowerCase().indexOf(hit);
      if (idx === -1) return;
      var after = node.splitText(idx);
      after.splitText(hit.length);
      var mk = document.createElement("mark");
      mk.className = "hit";
      mk.appendChild(document.createTextNode(after.nodeValue));
      after.parentNode.replaceChild(mk, after);
    });
  }

  // --- apply ---------------------------------------------------------------

  function apply() {
    var q = document.getElementById("q").value;
    var scope = document.querySelector("[name=scope]:checked").value;
    var facet = document.getElementById("facet").value;
    var terms = parseQuery(q);
    var shown = 0;

    index.forEach(function (e) {
      var ok = matches(e, terms, scope, facet);
      e.rec.hidden = !ok;
      var face = e.rec.querySelector(".card");
      if (face) clearMarks(face);
      var notes = e.rec.querySelectorAll(".card-note, .description");
      notes.forEach(clearMarks);
      if (ok && terms.length) {
        if (face) markTerms(face, terms);
        notes.forEach(function (nd) { markTerms(nd, terms); });
      }
      if (ok) shown++;
    });

    var out = document.getElementById("search-count");
    if (!terms.length && facet === "all") {
      out.textContent = index.length + " cards";
    } else {
      out.textContent = shown + " of " + index.length + " cards";
    }
    document.getElementById("no-hits").hidden = shown !== 0;
    syncHash(q, scope, facet);
  }

  function debounced() {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(apply, 120);
  }

  // --- shareable state -----------------------------------------------------

  function syncHash(q, scope, facet) {
    var p = new URLSearchParams();
    if (q) p.set("q", q);
    if (scope !== "all") p.set("scope", scope);
    if (facet !== "all") p.set("facet", facet);
    var s = p.toString();
    history.replaceState(null, "", s ? "#" + s : location.pathname);
  }

  function restoreHash() {
    if (!location.hash) return;
    var p = new URLSearchParams(location.hash.slice(1));
    if (p.get("q")) document.getElementById("q").value = p.get("q");
    var scope = p.get("scope");
    if (scope) {
      var r = document.querySelector('[name=scope][value="' + scope + '"]');
      if (r) r.checked = true;
    }
    if (p.get("facet")) document.getElementById("facet").value = p.get("facet");
  }

  // --- wiring --------------------------------------------------------------

  document.addEventListener("DOMContentLoaded", function () {
    buildIndex();
    restoreHash();
    apply();

    document.getElementById("q").addEventListener("input", debounced);
    document.querySelectorAll("[name=scope]").forEach(function (r) {
      r.addEventListener("change", apply);
    });
    document.getElementById("facet").addEventListener("change", apply);
    document.getElementById("q-clear").addEventListener("click", function () {
      document.getElementById("q").value = "";
      document.getElementById("facet").value = "all";
      apply();
      document.getElementById("q").focus();
    });
  });

  // "/" focuses search, Escape clears it — but never while typing in the TEI
  // editor, where both are ordinary characters.
  document.addEventListener("keydown", function (ev) {
    var inField = /^(INPUT|TEXTAREA|SELECT)$/.test(ev.target.tagName);
    if (ev.key === "/" && !inField) {
      ev.preventDefault();
      document.getElementById("q").focus();
    } else if (ev.key === "Escape" && ev.target.id === "q") {
      document.getElementById("q-clear").click();
    }
  });

  // The editor re-renders a card on save, wiping its marks; rebuild that card's
  // index entry and re-apply so search stays consistent with what is on screen.
  window.addEventListener("bergcat:card-updated", function () {
    buildIndex();
    apply();
  });
})();
