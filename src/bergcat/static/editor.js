/* Card correction front end.
 *
 * Each card ships with its own TEI source embedded in the page. Clicking "Edit
 * TEI" swaps the rendered card face for a textarea holding that source; saving
 * re-renders it live, flips the card's provenance from #ocr to #human, and
 * persists to localStorage so a session's work survives a reload.
 *
 * The provenance rewrite is the point of the whole exercise: a corrected card
 * is not merely "better text", it is text with a different and recorded
 * authority. Export writes that back into the TEI.
 */
(function () {
  "use strict";

  var STORE = "bergcat.corrections.v1";
  var parser = new DOMParser();
  var serializer = new XMLSerializer();
  var TEI = "http://www.tei-c.org/ns/1.0";

  function load() {
    try { return JSON.parse(localStorage.getItem(STORE)) || {}; }
    catch (e) { return {}; }
  }
  function save(state) {
    try { localStorage.setItem(STORE, JSON.stringify(state)); }
    catch (e) { flash("Could not save locally: " + e.message, true); }
  }

  var corrections = load();

  // --- TEI -> HTML (mirrors render.py for the subset the generator emits) ---

  function esc(s) {
    return (s || "").replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  function inline(node) {
    var out = "";
    for (var i = 0; i < node.childNodes.length; i++) {
      var n = node.childNodes[i];
      if (n.nodeType === 3) { out += esc(n.nodeValue.replace(/\s+/g, " ")); continue; }
      if (n.nodeType !== 1) continue;
      var name = n.localName, kids = inline(n);
      // Must match render.py exactly, trailing space included: with the <br>
      // hidden in reflow mode, "George<lb/>Gordon" would otherwise render as
      // "GeorgeGordon". Compact hand-edited XML carries no whitespace of its
      // own, so the separator has to come from here.
      if (name === "lb") out += '<br class="lb"/> ';
      else if (name === "gap")
        out += '<span class="gap" title="illegible">[&#8230;]</span>';
      else if (name === "unclear") {
        var cert = n.getAttribute("cert") || "unknown";
        var resp = (n.getAttribute("resp") || "").replace("#", "") || "unattributed";
        out += '<span class="unclear" data-cert="' + esc(cert) +
               '" title="uncertain &#183; confidence: ' + esc(cert) +
               " &#183; read by: " + esc(resp) + '">' + kids + "</span>";
      }
      else if (name === "abbr") {
        var ex = n.getAttribute("expan");
        out += ex ? '<abbr title="' + esc(ex) + '">' + kids + "</abbr>"
                  : '<abbr class="code">' + kids + "</abbr>";
      }
      else if (name === "hi")
        out += '<span class="hi hi-' + esc(n.getAttribute("rend") || "") + '">' + kids + "</span>";
      else if (name === "ref")
        out += '<a class="ref ref-' + esc(n.getAttribute("type") || "") +
               '" href="' + esc(n.getAttribute("target") || "#") + '">' + kids + "</a>";
      else if (name === "persName" || name === "placeName" || name === "measure" || name === "term")
        out += '<span class="' + name + '">' + kids + "</span>";
      else if (name === "date")
        out += '<time class="date">' + kids + "</time>";
      else if (name === "space") {
        var q = parseInt(n.getAttribute("quantity") || "1", 10);
        out += '<span class="space">' + new Array(q + 1).join("&#160;") + "</span>";
      }
      else out += kids;
    }
    return out;
  }

  function renderFace(cardEl) {
    var trans = cardEl.getElementsByTagNameNS(TEI, "div")[0];
    var html = "";
    if (!trans) {
      var blank = cardEl.getElementsByTagNameNS(TEI, "note")[0];
      return '<div class="ab ab-blank">' + (blank ? inline(blank) : "empty") + "</div>";
    }
    for (var i = 0; i < trans.children.length; i++) {
      var b = trans.children[i], name = b.localName;
      if (name === "fw") {
        html = '<div class="tracing tracing-top-right">' + inline(b) + "</div>" + html;
      } else if (name === "ab") {
        var cls = "ab ab-" + (b.getAttribute("type") || "");
        var rend = b.getAttribute("rend");
        if (rend) cls += " " + rend;
        html += '<div class="' + cls + '">' + inline(b) + "</div>";
      }
    }
    return html;
  }

  // --- correction bookkeeping ---------------------------------------------

  /* A human reading supersedes the machine's. Retag the transcription's
     responsibility and unwrap the <unclear> hedges the OCR pass added — they
     recorded the machine's doubt, and it is no longer the machine's reading. */
  function markHuman(cardEl) {
    cardEl.setAttribute("status", "verified");
    var trans = cardEl.getElementsByTagNameNS(TEI, "div")[0];
    if (trans) trans.setAttribute("resp", "#human");

    var unclears = cardEl.getElementsByTagNameNS(TEI, "unclear");
    while (unclears.length) {
      var u = unclears[0];
      if ((u.getAttribute("resp") || "") === "#human") break; // human-flagged: keep
      var parent = u.parentNode;
      while (u.firstChild) parent.insertBefore(u.firstChild, u);
      parent.removeChild(u);
    }
    var when = new Date().toISOString().slice(0, 10);
    cardEl.setAttribute("change", when);
    return cardEl;
  }

  function parseCard(xmlText) {
    var doc = parser.parseFromString(xmlText, "application/xml");
    var err = doc.getElementsByTagName("parsererror")[0];
    if (err) throw new Error(err.textContent.split("\n")[0]);
    var el = doc.documentElement;
    if (!el || el.localName !== "div")
      throw new Error("Expected a single <div type=\"card\"> element.");
    return el;
  }

  // --- UI ------------------------------------------------------------------

  function flash(msg, isErr) {
    var el = document.getElementById("toast");
    el.textContent = msg;
    el.className = "toast show" + (isErr ? " err" : "");
    clearTimeout(flash._t);
    flash._t = setTimeout(function () { el.className = "toast"; }, 3200);
  }

  function updateProgress() {
    var total = document.querySelectorAll(".card-record[data-blank=false]").length;
    var done = document.querySelectorAll('.card-record[data-status="verified"]').length;
    var pct = total ? Math.round((done / total) * 100) : 0;
    document.getElementById("prog-text").textContent =
      done + " of " + total + " verified (" + pct + "%)";
    document.getElementById("prog-bar").style.width = pct + "%";
  }

  function applyStatus(rec, status) {
    rec.setAttribute("data-status", status);
    var badge = rec.querySelector(".badge-status");
    if (badge) {
      badge.textContent = status;
      badge.className = "badge badge-status badge-" + status;
    }
  }

  // Sources live in hidden <textarea>s so the HTML parser decodes entities for
  // us; .value is clean XML with no manual unescaping.
  function setSource(rec, xmlText) {
    rec.querySelector(".tei-source").value = xmlText;
  }
  function getSource(rec) {
    return rec.querySelector(".tei-source").value;
  }

  function openEditor(rec) {
    rec.classList.add("editing");
    var ta = rec.querySelector(".tei-editor");
    ta.value = getSource(rec);
    ta.focus();
  }
  function closeEditor(rec) { rec.classList.remove("editing"); }

  function commit(rec) {
    var ta = rec.querySelector(".tei-editor");
    var cardEl;
    try {
      cardEl = parseCard(ta.value);
    } catch (e) {
      flash("Invalid XML — not saved. " + e.message, true);
      return;
    }
    markHuman(cardEl);
    var xmlText = serializer.serializeToString(cardEl);

    setSource(rec, xmlText);
    rec.querySelector(".card").innerHTML =
      renderFace(cardEl) + '<div class="rod-hole" aria-hidden="true"></div>';
    applyStatus(rec, "verified");
    closeEditor(rec);

    corrections[rec.id] = xmlText;
    save(corrections);
    updateProgress();
    // Re-rendering the face wipes any search highlighting and changes the text
    // search indexed; tell search to rebuild rather than let the two drift.
    window.dispatchEvent(new CustomEvent("bergcat:card-updated",
                                         { detail: { id: rec.id } }));
    flash("Saved. Card marked verified, attributed to #human.");
  }

  function revert(rec) {
    var orig = rec.querySelector(".tei-original").value;
    setSource(rec, orig);
    try {
      var el = parseCard(orig);
      rec.querySelector(".card").innerHTML =
        renderFace(el) + '<div class="rod-hole" aria-hidden="true"></div>';
      applyStatus(rec, el.getAttribute("status") || "unverified");
    } catch (e) { /* original is generator output; assume valid */ }
    delete corrections[rec.id];
    save(corrections);
    closeEditor(rec);
    updateProgress();
    window.dispatchEvent(new CustomEvent("bergcat:card-updated",
                                         { detail: { id: rec.id } }));
    flash("Reverted to the machine reading.");
  }

  // --- boot ----------------------------------------------------------------

  function restore() {
    Object.keys(corrections).forEach(function (id) {
      var rec = document.getElementById(id);
      if (!rec) return;
      var xmlText = corrections[id];
      try {
        var el = parseCard(xmlText);
        setSource(rec, xmlText);
        rec.querySelector(".card").innerHTML =
          renderFace(el) + '<div class="rod-hole" aria-hidden="true"></div>';
        applyStatus(rec, "verified");
      } catch (e) { /* drop unparseable stored state rather than break the page */ }
    });
  }

  function exportTEI() {
    var full = document.getElementById("tei-doc").value;
    var doc = parser.parseFromString(full, "application/xml");
    var n = 0;
    Object.keys(corrections).forEach(function (id) {
      var target = doc.querySelector('[*|id="' + id + '"]') ||
                   doc.getElementById(id);
      if (!target) {
        var all = doc.getElementsByTagNameNS(TEI, "div");
        for (var i = 0; i < all.length; i++) {
          if (all[i].getAttribute("xml:id") === id) { target = all[i]; break; }
        }
      }
      if (!target) return;
      var repl = parser.parseFromString(corrections[id], "application/xml").documentElement;
      target.parentNode.replaceChild(doc.importNode(repl, true), target);
      n++;
    });
    var out = serializer.serializeToString(doc);
    var blob = new Blob([out], { type: "application/xml" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "berg-corrected.xml";
    a.click();
    URL.revokeObjectURL(a.href);
    flash("Exported TEI with " + n + " corrected card" + (n === 1 ? "" : "s") + ".");
  }

  document.addEventListener("click", function (ev) {
    var btn = ev.target.closest("[data-act]");
    if (!btn) return;
    var act = btn.getAttribute("data-act");
    if (act === "export") return exportTEI();
    if (act === "reset") {
      if (!confirm("Discard all local corrections?")) return;
      corrections = {}; save(corrections); location.reload(); return;
    }
    var rec = btn.closest(".card-record");
    if (!rec) return;
    if (act === "edit") openEditor(rec);
    else if (act === "cancel") closeEditor(rec);
    else if (act === "save") commit(rec);
    else if (act === "revert") revert(rec);
    else if (act === "lineation") {
      // Display-only. The TEI is untouched, so this never marks a card dirty.
      var on = rec.classList.toggle("lineated");
      btn.textContent = on ? "Reflow text" : "Show original lineation";
    }
  });

  // Ctrl/Cmd+Enter saves from inside the textarea.
  document.addEventListener("keydown", function (ev) {
    if (!(ev.ctrlKey || ev.metaKey) || ev.key !== "Enter") return;
    var ta = ev.target.closest(".tei-editor");
    if (ta) commit(ta.closest(".card-record"));
  });

  document.addEventListener("DOMContentLoaded", function () {
    restore();
    updateProgress();
  });
})();
