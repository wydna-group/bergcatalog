"""Stage 9: render TEI to the correction front end.

Each card is emitted three times over: as a styled card face, as the source
scan beside it, and as its own TEI source in a hidden block. The third is what
makes the page an editor rather than a report — `editor.js` swaps the face for
a textarea over that source, and a save rewrites the card's provenance from
#ocr to #human.

    python -m bergcat.render data/out/site/wise.xml -o data/out/site/index.html
"""
from __future__ import annotations

import argparse
import html
import re
from pathlib import Path

from lxml import etree

TEI_NS = "http://www.tei-c.org/ns/1.0"
XML_NS = "http://www.w3.org/XML/1998/namespace"
NS = {"t": TEI_NS}
XML_ID = f"{{{XML_NS}}}id"
STATIC = Path(__file__).parent / "static"

_WS = re.compile(r"\s+")


def _tag(el: etree._Element) -> str:
    return etree.QName(el).localname


def _esc(s: str | None) -> str:
    """Escape a TEI text node for HTML.

    Whitespace is collapsed deliberately: the XML is pretty-printed, so its
    newlines and indentation are an artifact of file formatting, not content.
    Lineation comes from <lb/> alone — that is what the tag is for.
    """
    return html.escape(_WS.sub(" ", s or ""), quote=True)


# --- inline rendering -----------------------------------------------------

def render_inline(el: etree._Element) -> str:
    tag = _tag(el)
    inner = _esc(el.text) + "".join(render_inline(c) + _esc(c.tail) for c in el)

    if tag == "lb":
        # Classed so the card can reflow or reproduce the original lineation
        # without changing the TEI.
        #
        # The trailing space is load-bearing: in reflow mode the <br> is hidden,
        # and without it "...of George<lb/>Gordon..." renders as "GeorgeGordon".
        # Relying on the source XML's own newline works only for our generated,
        # pretty-printed output — a hand-edited card saved as compact XML has no
        # whitespace around <lb/>. In lineation mode the space falls at the end
        # of a line, where it is invisible.
        return '<br class="lb"/> '
    if tag == "gap":
        reason = _esc(el.get("reason", "illegible"))
        return f'<span class="gap" title="gap: {reason}">[&#8230;]</span>'
    if tag == "unclear":
        cert = _esc(el.get("cert", "unknown"))
        resp = _esc(el.get("resp", "").lstrip("#") or "unattributed")
        reason = _esc(el.get("reason", ""))
        tip = f"uncertain reading &#183; confidence: {cert} &#183; read by: {resp}"
        if reason:
            tip += f" &#183; {reason}"
        return f'<span class="unclear" data-cert="{cert}" title="{tip}">{inner}</span>'
    if tag == "hi":
        return f'<span class="hi hi-{_esc(el.get("rend",""))}">{inner}</span>'
    if tag == "abbr":
        expan = el.get("expan")
        if expan:
            return f'<abbr title="{_esc(expan)}">{inner}</abbr>'
        return f'<abbr class="code">{inner}</abbr>'
    if tag == "ref":
        kind = _esc(el.get("type", ""))
        target = _esc(el.get("target", "#"))
        return f'<a class="ref ref-{kind}" href="{target}">{inner}</a>'
    if tag in ("persName", "placeName", "measure", "term"):
        return f'<span class="{tag}">{inner}</span>'
    if tag == "date":
        norm = el.get("when") or el.get("from") or ""
        return f'<time class="date" datetime="{_esc(norm)}">{inner}</time>'
    if tag == "space":
        return '<span class="space">' + "&#160;" * int(el.get("quantity", "1")) + "</span>"
    return inner


# --- card -----------------------------------------------------------------

def render_face(card: etree._Element) -> str:
    trans = card.find("t:div[@type='transcription']", NS)
    if trans is None:
        note = card.find("t:note", NS)
        body = render_inline(note) if note is not None else "empty cell"
        return f'<div class="ab ab-blank">{body}</div>'

    tracing, blocks = "", []
    for b in trans:
        t = _tag(b)
        if t == "fw":
            tracing = (f'<div class="tracing tracing-{_esc(b.get("place",""))}">'
                       f"{render_inline(b)}</div>")
        elif t == "ab":
            cls = f'ab ab-{_esc(b.get("type",""))}'
            if rend := b.get("rend"):
                cls += f" {_esc(rend)}"
            blocks.append(f'<div class="{cls}">{render_inline(b)}</div>')
    return tracing + "".join(blocks)


def render_card(card: etree._Element, zones: dict[str, str], img_root: str,
                revert_label: str = "machine reading") -> str:
    card_id = card.get(XML_ID, "")
    status = card.get("status", "unverified")
    is_blank = status == "blank"

    url = zones.get(card.get("facs", "").lstrip("#"), "")
    img = (f'<img class="card-facs" src="{_esc(img_root + url)}" '
           f'alt="Scan of card {_esc(card_id)}" loading="lazy"/>') if url else \
          '<div class="no-facs">no crop</div>'

    # The card's own TEI, pretty-printed, is what the editor edits.
    src = etree.tostring(card, pretty_print=True, encoding="unicode").strip()

    conf = card.find("t:note[@type='ocrConfidence']", NS)
    conf_html = ""
    if conf is not None and conf.text:
        conf_html = (f'<span class="badge badge-conf" '
                     f'title="mean OCR word confidence">ocr {float(conf.text):.0f}%</span>')

    badges = [f'<span class="badge badge-status badge-{status}">{status}</span>']
    if conf_html:
        badges.append(conf_html)

    # Badge from the card's content, not from one encoding path's artefacts.
    # The OCR path records a continuation as <note type="continuation"> and a
    # cross-reference as <note type="cardFunction">; the VLM path uses @prev and
    # <ab type="crossReference">. Checking only one silently under-badges the
    # other — cross-references scored zero until this was fixed.
    is_cont = (card.get("prev")
               or card.find("t:note[@type='continuation']", NS) is not None)
    is_xref = (card.find("t:note[@type='cardFunction']", NS) is not None
               or card.find(".//t:ab[@type='crossReference']", NS) is not None
               or card.find(".//t:ref[@type='see']", NS) is not None)
    if is_cont:
        badges.append('<span class="badge badge-cont">continuation</span>')
    if is_xref:
        badges.append('<span class="badge badge-xref">cross-reference</span>')

    notes = "".join(
        f'<div class="card-note"><span class="note-label">{_esc(n.get("type",""))}</span>'
        f"{render_inline(n)}</div>"
        for n in card.findall("t:note", NS)
        if n.get("type") != "ocrConfidence"
    )

    controls = "" if is_blank else f"""
      <div class="card-actions">
        <button data-act="edit" class="btn">Edit TEI</button>
        <button data-act="lineation" class="btn btn-quiet"
                title="The TEI always keeps the original line endings; this only
                       changes how the card is displayed."
        >Show original lineation</button>
        <button data-act="revert" class="btn btn-quiet">Revert to {revert_label}</button>
      </div>"""

    desc_el = card.find("t:listBibl[@type='description']", NS)
    desc_html = render_description(desc_el) if desc_el is not None else ""

    return f"""
<article class="card-record" id="{_esc(card_id)}"
         data-status="{_esc(status)}" data-blank="{str(is_blank).lower()}">
  <div class="card-head">
    <code class="card-id">{_esc(card_id)}</code>
    <div class="badges">{"".join(badges)}</div>
  </div>
  <div class="card-body">
    <div class="card-pane">
      <div class="card">{render_face(card)}<div class="rod-hole" aria-hidden="true"></div></div>
      <div class="editor-shell">
        <label class="editor-label" for="ed-{_esc(card_id)}">TEI source for this card</label>
        <textarea class="tei-editor" id="ed-{_esc(card_id)}" spellcheck="false"></textarea>
        <div class="editor-actions">
          <button data-act="save" class="btn btn-primary">Save &amp; mark verified</button>
          <button data-act="cancel" class="btn btn-quiet">Cancel</button>
          <span class="hint">Ctrl/&#8984;+Enter to save</span>
        </div>
      </div>
      {controls}
      {notes}
    </div>
    <div class="facs-pane">
      {img}
      <div class="pane-label">source scan</div>
    </div>
  </div>
  {desc_html}
  <!-- Hidden textareas, not <script> blocks: a script element's textContent is
       NOT entity-decoded, so escaped markup would reach the editor literally.
       The parser decodes textarea content, and .value returns clean XML. -->
  <textarea class="tei-source" hidden aria-hidden="true">{_esc(src)}</textarea>
  <textarea class="tei-original" hidden aria-hidden="true">{_esc(src)}</textarea>
</article>"""


def render_description(desc: etree._Element) -> str:
    """The <listBibl type='description'> panel: bibliographic interpretation
    beside the physical transcription.

    The OCR seed does not produce these yet — only hand-encoded cards and, later,
    the VLM stage do. Cards without one simply render no panel.
    """
    rows: list[tuple[str, str]] = []
    bs = desc.find("t:biblStruct", NS)
    ms = desc.find("t:msDesc", NS)

    def add(label: str, el: etree._Element | None) -> None:
        if el is not None:
            rows.append((label, render_inline(el)))

    if bs is not None:
        m = bs.find("t:monogr", NS)
        if m is not None:
            add("Author", m.find("t:author", NS))
            add("Title", m.find("t:title", NS))
            add("Edition", m.find("t:edition", NS))
            imp = m.find("t:imprint", NS)
            if imp is not None:
                parts = [render_inline(x) for x in imp
                         if _tag(x) in ("pubPlace", "publisher", "date")]
                rows.append(("Imprint", " &#183; ".join(parts)))
            add("Extent", m.find("t:extent", NS))
        for note in bs.findall("t:note", NS):
            label = note.get("type", "note")
            label = {"editionSize": "Edition size", "copies": "Copies"}.get(
                label, label.title())
            rows.append((label, render_inline(note)))
    elif ms is not None:
        ident = ms.find("t:msIdentifier", NS)
        if ident is not None:
            rows.append(("Repository",
                         " &#183; ".join(render_inline(x) for x in ident)))
        contents = ms.find("t:msContents", NS)
        if contents is not None:
            items = "".join(f"<li>{render_inline(it)}</li>"
                            for it in contents.findall("t:msItem", NS))
            rows.append(("Contents", f'<ul class="ms-items">{items}</ul>'))
        add("Physical", ms.find("t:physDesc", NS))

    if not rows:
        return ""
    body = "".join(f'<div class="desc-row"><dt>{_esc(k)}</dt><dd>{v}</dd></div>'
                   for k, v in rows)
    return f"""
  <details class="description">
    <summary>Structured description</summary>
    <dl class="desc-grid">{body}</dl>
  </details>"""


def build_zone_index(root: etree._Element) -> dict[str, str]:
    """Map zone xml:id -> graphic url. ElementPath cannot query xml:id, and one
    pass over the facsimile is cheaper than a search per card."""
    index: dict[str, str] = {}
    for zone in root.iter(f"{{{TEI_NS}}}zone"):
        zid = zone.get(XML_ID)
        g = zone.find("t:graphic", NS)
        if zid and g is not None:
            index[zid] = g.get("url", "")
    return index


def render_document(tei_path: Path, img_root: str = "") -> str:
    tree = etree.parse(str(tei_path))
    root = tree.getroot()

    title = (root.findtext(".//t:titleStmt/t:title", "Berg Catalog", NS) or "").strip()
    zones = build_zone_index(root)
    cards = root.findall(".//t:div[@type='card']", NS)
    pages = root.findall(".//t:div[@type='page']", NS)

    # The page must describe its own provenance honestly: an OCR-seeded build
    # and a VLM-seeded one warrant very different confidence from a corrector.
    n_vlm = len(root.findall(".//t:div[@resp='#vlm']", NS))
    n_ocr = len(root.findall(".//t:div[@resp='#ocr']", NS))
    if n_vlm >= n_ocr:
        seed, revert_label = "#vlm", "machine reading"
        seed_blurb = (
            "Cards are seeded by a vision model reading the scan directly, "
            "attributed to <code>#vlm</code> and left <em>unverified</em>. It is "
            "accurate on most cards but not authoritative &#8212; where it was "
            "unsure it says so in a note rather than guessing."
        )
    else:
        seed, revert_label = "#ocr", "OCR"
        seed_blurb = (
            "Cards are seeded from uncorrected OCR (~75&#8211;82% accurate), "
            "marked <span class=\"unclear\">uncertain</span> and attributed to "
            "<code>#ocr</code>."
        )

    cards_html = "\n".join(
        render_card(c, zones, img_root, revert_label) for c in cards)
    full_tei = etree.tostring(tree, pretty_print=True, encoding="unicode")

    css = (STATIC / "cards.css").read_text(encoding="utf-8")
    js = (STATIC / "editor.js").read_text(encoding="utf-8")
    search_js = (STATIC / "search.js").read_text(encoding="utf-8")
    n_real = sum(1 for c in cards if c.get("status") != "blank")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{_esc(title)}</title>
<style>{css}</style>
</head>
<body>
<header class="site-head">
  <h1>{_esc(title)}</h1>
  <p class="source">{len(pages)} pages &#183; {len(cards)} cards
     ({n_real} with content) &#183; seeded by <code>{seed}</code>, corrected by hand</p>
  <div class="progress">
    <div class="prog-track"><div class="prog-bar" id="prog-bar"></div></div>
    <span id="prog-text" class="prog-text">0 verified</span>
  </div>
  <div class="search">
    <div class="search-row">
      <input id="q" type="search" class="search-input" autocomplete="off"
             spellcheck="false"
             placeholder="Search the catalogue &#8212; press / to focus"
             aria-label="Search cards"/>
      <button id="q-clear" class="btn btn-quiet" type="button">Clear</button>
      <span id="search-count" class="search-count"></span>
    </div>
    <div class="search-row search-opts">
      <span class="opt-group" role="radiogroup" aria-label="Search scope">
        <label><input type="radio" name="scope" value="all" checked/> whole card</label>
        <label><input type="radio" name="scope" value="main"/> main entry</label>
        <label><input type="radio" name="scope" value="notes"/> notes</label>
      </span>
      <label class="opt-group">show
        <select id="facet" aria-label="Filter cards">
          <option value="all">all cards</option>
          <option value="unverified">unverified only</option>
          <option value="verified">verified only</option>
          <option value="uncertain">flagged uncertain</option>
          <option value="continuation">continuations</option>
          <option value="xref">cross-references</option>
        </select>
      </label>
    </div>
  </div>
  <div class="toolbar">
    <button data-act="export" class="btn btn-primary">Export corrected TEI</button>
    <button data-act="reset" class="btn btn-quiet">Discard local corrections</button>
  </div>
  <p class="legend">
    {seed_blurb}
    Correct a card and it is re-attributed to <code>#human</code> and marked
    verified. Work is stored in this browser until you export.
  </p>
</header>
<main>
<p id="no-hits" class="no-hits" hidden>No cards match. Try fewer words, or
   switch the filter back to <em>all cards</em>.</p>
{cards_html}
</main>
<div id="toast" class="toast"></div>
<textarea id="tei-doc" hidden aria-hidden="true">{_esc(full_tei)}</textarea>
<script>{js}</script>
<script>{search_js}</script>
</body>
</html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tei", type=Path)
    ap.add_argument("-o", "--out", type=Path, required=True)
    ap.add_argument("--img-root", default="")
    args = ap.parse_args()

    out_html = render_document(args.tei, args.img_root)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(out_html, encoding="utf-8")
    print(f"wrote {args.out}  ({len(out_html)/1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
