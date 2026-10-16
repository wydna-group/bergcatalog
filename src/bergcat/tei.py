"""Stage 8: assemble TEI P5 from segmented cards and their transcriptions.

Reads only what earlier stages wrote — the grid metadata and card crops from
`bergcat.segment`, and the results JSON from `bergcat.transcribe`. It never
opens a source PDF, which keeps a rebuild to seconds rather than ~45s/page.

Every card is provisional. VLM readings are `resp="#vlm"`, OCR `resp="#ocr"`,
and both leave the card `status="unverified"`; only a human correction in the
front end rewrites that to `resp="#human"` / `status="verified"`. That is the
point of the whole arrangement: the file records not just *what* it says but
*who read it* and *how much to trust them*.

    python -m bergcat.tei test -o demo/wise.xml
    python -m bergcat.tei test v2:103-106 --vlm a.json --vlm b.json -o out.xml
"""
from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path

import cv2

from . import config
from .ocr import CardOcr, ocr_card

TEI_NS = "http://www.tei-c.org/ns/1.0"


def esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def _lines_xml(lines, indent: str) -> str:
    """Emit OCR lines as text separated by <lb/>, preserving lineation."""
    if not lines:
        return ""
    parts = [esc(l.text) for l in lines]
    return f"\n{indent}" + f"<lb/>\n{indent}".join(parts)


def card_to_tei(card: CardOcr, card_id: str, zone_id: str,
                row: int, col: int, prev_id: str | None = None,
                seq: int = 0) -> str:
    """One <div type='card'>. Blank cells are recorded, not skipped — an empty
    slot in the drawer is data, and silently dropping it would corrupt the
    row/column geometry that continuation-linking depends on."""
    i = "          "
    sort_attr = f' sortKey="{seq:06d}"' if seq else ""

    if card.is_blank:
        return (
            f'        <div type="card" xml:id="{card_id}" facs="#{zone_id}"\n'
            f'             n="r{row}c{col}"{sort_attr} status="blank">\n'
            f'{i}<note type="blank">Grid cell contained no card.</note>\n'
            f"        </div>"
        )

    prev_attr = f'\n             prev="#{prev_id}"' if prev_id else ""
    out = [
        f'        <div type="card" xml:id="{card_id}" facs="#{zone_id}"\n'
        f'             n="r{row}c{col}"{sort_attr} status="unverified"'
        f'{prev_attr}>',
        f'{i}<div type="transcription" resp="#ocr">',
    ]

    if card.tracing:
        out.append(f'{i}  <fw type="tracing" place="top-right">'
                   f'{_lines_xml(card.tracing, i + "    ")}\n{i}  </fw>')

    if card.main_entry:
        out.append(f'{i}  <ab type="mainEntry">'
                   f'<unclear cert="low" resp="#ocr">'
                   f'{esc(card.main_entry.text)}</unclear></ab>')

    if card.body:
        out.append(f'{i}  <ab type="body" rend="indent1">'
                   f'<unclear cert="low" resp="#ocr">'
                   f'{_lines_xml(card.body, i + "    ")}\n{i}  </unclear></ab>')

    if card.notes:
        out.append(f'{i}  <ab type="note">'
                   f'<unclear cert="low" resp="#ocr">'
                   f'{_lines_xml(card.notes, i + "    ")}\n{i}  </unclear></ab>')

    for code in card.holdings:
        out.append(f'{i}  <ab type="holdings">'
                   f'<abbr type="holdingsCode">{esc(code)}.</abbr></ab>')

    out.append(f"{i}</div>")

    if card.card_number:
        out.append(f'{i}<note type="continuation" n="{card.card_number}">'
                   f"Card {card.card_number} of a multi-card entry.</note>")
    if card.see_target:
        out.append(f'{i}<note type="cardFunction">Cross-reference to '
                   f"{esc(card.see_target)}.</note>")

    out.append(f'{i}<note type="ocrConfidence">{card.mean_conf:.1f}</note>')
    out.append("        </div>")
    return "\n".join(out)


GAP_WORD_RE = re.compile(r"\[illegible\]", re.I)
GAP_CHAR_RE = re.compile(r"\[\?\]")


def _text_with_gaps(text: str) -> str:
    """Turn the model's illegibility markers into real TEI <gap/> elements.

    `[?]` and `[illegible]` are a prompt convention, not data. Left as literal
    text they would be indistinguishable from a card that actually reads
    "[illegible]", and no downstream query could count how much is unread.
    """
    out = esc(text)
    out = GAP_WORD_RE.sub('<gap reason="illegible" unit="word"/>', out)
    out = GAP_CHAR_RE.sub('<gap reason="illegible" unit="character" quantity="1"/>',
                          out)
    return out


def _vlm_lines(lines: list[dict], indent: str) -> str:
    parts = []
    for line in lines:
        body = _text_with_gaps(line.get("text", ""))
        if line.get("underline"):
            body = f'<hi rend="underline">{body}</hi>'
        parts.append(body)
    if not parts:
        return ""
    return f"\n{indent}" + f"<lb/>\n{indent}".join(parts)


def vlm_card_to_tei(data: dict, card_id: str, zone_id: str,
                    row: int, col: int, prev_id: str | None = None,
                    seq: int = 0) -> str:
    """Serialize one VLM result to a TEI card div.

    Attribution is `#vlm` and status stays `unverified`: a machine reading is a
    strong starting point, not a verified one. Only a human correction in the
    front end promotes it.

    `@sortKey` records the card's position in the drawer explicitly. Document
    order already carries it, but a consumer that re-sorts or merges fragments
    would lose it silently, and the filing sequence cannot be recomputed from
    the text (see config.catalog_order).
    """
    i = "          "
    sort_attr = f' sortKey="{seq:06d}"' if seq else ""
    if data.get("is_blank"):
        return (
            f'        <div type="card" xml:id="{card_id}" facs="#{zone_id}"\n'
            f'             n="r{row}c{col}"{sort_attr} status="blank">\n'
            f'{i}<note type="blank">Grid cell contained no card.</note>\n'
            f"        </div>"
        )

    conf = data.get("confidence", "high")
    prev_attr = f'\n             prev="#{prev_id}"' if prev_id else ""
    out = [
        f'        <div type="card" xml:id="{card_id}" facs="#{zone_id}"\n'
        f'             n="r{row}c{col}"{sort_attr} status="unverified"{prev_attr}>',
        f'{i}<div type="transcription" resp="#vlm">',
    ]

    tracings = [t for t in data.get("tracings", []) if t.get("heading") or t.get("prefix")]
    if tracings:
        rows = []
        for t in tracings:
            if t.get("prefix"):
                rows.append(f'<abbr>{esc(t["prefix"])}</abbr>')
            if t.get("heading"):
                rows.append(f'<ref type="added-entry">{esc(t["heading"])}</ref>')
        out.append(f'{i}  <fw type="tracing" place="top-right">\n'
                   f'{i}    ' + f"<lb/>\n{i}    ".join(rows) + f"\n{i}  </fw>")

    for block in data.get("blocks", []):
        role = block.get("role", "body")
        lines = block.get("lines", [])
        if not lines:
            continue
        rend = ""
        if role == "body":
            depth = max(1, min(2, (block.get("indent", 2) or 2) // 2))
            rend = f' rend="indent{depth}"'
        inner = _vlm_lines(lines, i + "    ")
        # Only a low-confidence card is hedged; wrapping a clean reading in
        # <unclear> would make the marker meaningless.
        if conf in ("low", "unreadable"):
            inner = (f'<unclear cert="{esc(conf)}" resp="#vlm">'
                     f"{inner}\n{i}  </unclear>")
            out.append(f'{i}  <ab type="{esc(role)}"{rend}>{inner}</ab>')
        else:
            out.append(f'{i}  <ab type="{esc(role)}"{rend}>{inner}\n{i}  </ab>')

    # `holdings_codes` is *interpretation* of text already present in `blocks`
    # (the card's own "H." line). Emitting both duplicated the code at the foot
    # of every card. Verbatim text stays in the transcription; the parsed codes
    # belong in the description layer below.

    if see := data.get("see_reference", "").strip():
        out.append(f'{i}  <ab type="crossReference">'
                   f'<ref type="see">{esc(see)}</ref></ab>')

    out.append(f"{i}</div>")

    # --- description layer ---
    entry = data.get("entry", {})
    copies = data.get("copies", [])
    codes = [c.strip().rstrip(".") for c in data.get("holdings_codes", [])]
    codes = [c for c in codes if c]
    if any(entry.get(k) for k in entry) or copies or codes:
        out.append(f'{i}<listBibl type="description">')
        out.append(f'{i}  <biblStruct>')
        out.append(f'{i}    <monogr>')
        if entry.get("main_entry"):
            out.append(f'{i}      <author>{esc(entry["main_entry"])}</author>')
        if entry.get("title"):
            out.append(f'{i}      <title level="m">{esc(entry["title"])}</title>')
        if entry.get("edition"):
            out.append(f'{i}      <edition>{esc(entry["edition"])}</edition>')
        if entry.get("imprint"):
            out.append(f'{i}      <imprint><publisher>'
                       f'{esc(entry["imprint"])}</publisher></imprint>')
        if entry.get("collation"):
            out.append(f'{i}      <extent>{esc(entry["collation"])}</extent>')
        out.append(f'{i}    </monogr>')
        if copies:
            out.append(f'{i}    <note type="copies" n="{len(copies)}">')
            out.append(f'{i}      <list type="copies">')
            for c in copies:
                out.append(f'{i}        <item n="{c.get("number", 0)}">'
                           f'{_text_with_gaps(c.get("description", ""))}</item>')
            out.append(f'{i}      </list>')
            out.append(f'{i}    </note>')
        if codes:
            joined = ", ".join(f"{esc(c)}." for c in codes)
            out.append(f'{i}    <note type="holdings">{joined}</note>')
        out.append(f'{i}  </biblStruct>')
        out.append(f'{i}</listBibl>')

    if n := data.get("card_number", 0):
        out.append(f'{i}<note type="continuation" n="{n}">'
                   f"Card {n} of a multi-card entry.</note>")
    for w in data.get("with_references", []):
        if w.strip():
            out.append(f'{i}<note type="with">{esc(w)}</note>')
    for note in data.get("notes", []):
        if note.strip():
            out.append(f'{i}<note type="transcription">{esc(note)}</note>')
    out.append(f'{i}<note type="vlmConfidence">{esc(conf)}</note>')
    out.append("        </div>")
    return "\n".join(out)


def parse_source(spec: str) -> tuple[str, range | None]:
    """`test`, `v2`, or `v2:103-106` -> (volume, page range)."""
    volume, _, pages = spec.partition(":")
    if volume not in config.VOLUMES and volume != "test":
        raise SystemExit(f"unknown volume {volume!r} in source {spec!r}")
    from .segment import _parse_pages
    return volume, _parse_pages(pages or None)


def build(sources: list[tuple[str, range | None]],
          vlm_results: list[Path] | None = None) -> str:
    """Build one TEI document spanning any number of volumes and page ranges.

    Card ids already carry their volume (`v2-p0103-r0c0`), so several sources
    coexist in a single document without collision — which is what lets the
    front end hold the whole catalogue rather than one file per volume.
    """
    config.ensure_dirs()
    img_dir = config.OUT_DIR / "site" / "images"
    img_dir.mkdir(parents=True, exist_ok=True)

    vlm: dict[str, dict] = {}
    for path in vlm_results or []:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        got = payload.get("results", {})
        vlm.update(got)
        print(f"  loaded {len(got)} VLM transcriptions from {Path(path).name}")

    facs_blocks: list[str] = []
    page_blocks: list[str] = []

    # Emit pages in catalogue order — by volume, then page — regardless of the
    # order the sources were named on the command line. A drawer runs v3 p537
    # before p703 whichever way the operator asked for them.
    #
    # The position is resolved through config.source_position so an extract like
    # `test` (bergv4 pp.431-438) files where its real pages do. Sorting on the
    # bare key put its W entries ahead of v2's F entries and broke the alphabet
    # across the whole document.
    plan: list[tuple[str, range | None]] = sorted(
        sources,
        key=lambda s: config.source_position(s[0], s[1].start if s[1] else 0))

    # One running sequence across the whole document, so @sortKey is a global
    # position in the catalogue rather than a per-source offset.
    counters: dict = {"cards": 0, "blank": 0, "vlm": 0, "seq": 0}
    for volume, pages in plan:
        _build_source(volume, pages, vlm, img_dir, facs_blocks, page_blocks,
                      counters)

    print(f"\n{counters['cards']} cards ({counters['blank']} blank, "
          f"{counters['vlm']} from VLM, "
          f"{counters['cards'] - counters['vlm']} from OCR)")

    label = ", ".join(
        v + (f" pp.{p.start + 1}-{p.stop}" if p else "") for v, p in plan)
    # xml:id must be an NCName — no spaces, commas or leading digits — so the
    # human-readable label cannot double as one.
    slug = "-".join(
        v + (f"-p{p.start + 1}-{p.stop}" if p else "") for v, p in plan)
    return _document(slug, label, facs_blocks, page_blocks)


def _build_source(volume: str, pages: range | None, vlm: dict,
                  img_dir: Path, facs_blocks: list[str],
                  page_blocks: list[str], counters: dict) -> None:
    """Assemble one source's TEI from artefacts `bergcat.segment` already wrote.

    This deliberately never opens the PDF. Re-rendering every page at 580 DPI
    and re-running deskew on each build cost ~45s/page, which made a rebuild of
    a dozen pages a ten-minute job and would have been unusable at volume
    scale. Segmentation is the expensive step and it runs once per source; the
    TEI step is now just JSON plus a thumbnail resize.
    """
    # NB: counters accumulate across sources — do not reset them here.
    meta_path = config.CARDS_DIR / f"{volume}-grids.json"
    if not meta_path.exists():
        raise SystemExit(
            f"No segmentation metadata at {meta_path}.\n"
            f"Run:  python -m bergcat.segment {volume}"
            + (f" --pages {pages.start + 1}-{pages.stop}" if pages else "")
        )
    grids = {g["pdf_page"]: g for g in
             json.loads(meta_path.read_text(encoding="utf-8"))}

    wanted = sorted(grids) if pages is None else [
        p + 1 for p in pages if (p + 1) in grids]
    missing = [] if pages is None else [
        p + 1 for p in pages if (p + 1) not in grids]
    if missing:
        raise SystemExit(
            f"{volume}: pages {missing} have not been segmented.\n"
            f"Run:  python -m bergcat.segment {volume} "
            f"--pages {missing[0]}-{missing[-1]}"
        )

    card_dir = config.CARDS_DIR / volume
    for pg in wanted:
        grid = grids[pg]
        _emit_page(volume, pg, grid, card_dir, vlm, img_dir,
                   facs_blocks, page_blocks, counters)


def _emit_page(volume: str, pg: int, grid: dict, card_dir: Path, vlm: dict,
               img_dir: Path, facs_blocks: list[str], page_blocks: list[str],
               counters: dict) -> None:
    # Grid coordinates were fitted at SEGMENT_DPI; facsimile zones are quoted
    # in source pixels, so scale by the page's recorded full-resolution size.
    sx = (grid.get("src_width") or grid["width"]) / grid["width"]
    sy = (grid.get("src_height") or grid["height"]) / grid["height"]

    zones: list[str] = []
    cards: list[str] = []
    prev_id: str | None = None

    # Emit in the drawer's filing sequence, not the grid's storage order, so
    # document order IS catalogue order. Continuation linking depends on this
    # too: a "(card 2)" follows its predecessor in filing order.
    by_pos = {(c["row"], c["col"]): c for c in grid["cells"]}
    ordered = [by_pos[p] for p in config.catalog_order() if p in by_pos]

    for cell in ordered:
        row, col = cell["row"], cell["col"]
        counters["seq"] = counters.get("seq", 0) + 1
        seq = counters["seq"]
        card_id = f"{volume}-p{pg:04d}-r{row}c{col}"
        zone_id = f"zone-{card_id}"
        fname = f"{card_id}.png"
        crop_path = card_dir / fname
        web_path = img_dir / fname

        x0, y0 = int(cell["x"] * sx), int(cell["y"] * sy)
        x1 = int((cell["x"] + cell["w"]) * sx)
        y1 = int((cell["y"] + cell["h"]) * sy)

        # Regenerate the web thumbnail only when it is missing or stale.
        if crop_path.exists() and (
            not web_path.exists()
            or web_path.stat().st_mtime < crop_path.stat().st_mtime
        ):
            crop = cv2.imread(str(crop_path), cv2.IMREAD_GRAYSCALE)
            if crop is not None and crop.size:
                scale = min(1.0, 900 / crop.shape[1])
                web = cv2.resize(crop, None, fx=scale, fy=scale,
                                 interpolation=cv2.INTER_AREA)
                cv2.imwrite(str(web_path), web, [cv2.IMWRITE_PNG_COMPRESSION, 9])

        zones.append(
            f'      <zone xml:id="{zone_id}" ulx="{x0}" uly="{y0}" '
            f'lrx="{x1}" lry="{y1}">\n'
            f'        <graphic url="images/{fname}"/>\n'
            f"      </zone>"
        )

        if card_id in vlm:
            data = vlm[card_id]
            is_cont = (data.get("card_number") or 0) > 1
            cards.append(vlm_card_to_tei(data, card_id, zone_id, row, col,
                                         prev_id if is_cont else None, seq))
            counters["blank"] += bool(data.get("is_blank"))
            counters["vlm"] += 1
        else:
            crop = (cv2.imread(str(crop_path), cv2.IMREAD_GRAYSCALE)
                    if crop_path.exists() else None)
            oc = ocr_card(crop) if crop is not None and crop.size else CardOcr(is_blank=True)
            is_cont = bool(oc.card_number and oc.card_number > 1)
            cards.append(card_to_tei(oc, card_id, zone_id, row, col,
                                     prev_id if is_cont else None, seq))
            counters["blank"] += bool(oc.is_blank)
        prev_id = card_id
        counters["cards"] += 1

    facs_blocks.append(
        f'    <surface xml:id="surface-{volume}-p{pg:04d}" ulx="0" uly="0" '
        f'lrx="{int(grid.get("src_width") or grid["width"])}" '
        f'lry="{int(grid.get("src_height") or grid["height"])}">\n'
        + "\n".join(zones) + "\n    </surface>"
    )
    page_blocks.append(
        f'      <div type="page" xml:id="{volume}-p{pg:04d}" n="{pg}">\n'
        f'        <pb n="{pg}" facs="#surface-{volume}-p{pg:04d}"/>\n'
        + "\n".join(cards) + "\n      </div>"
    )
    print(f"  {volume} p{pg:>4}  {len(grid['cells'])} cards")



def _document(slug: str, label: str, facs_blocks: list[str],
              page_blocks: list[str]) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!--
  GENERATED — do not hand-edit; regenerate with `python -m bergcat.tei`, or
  correct cards in the front end and export from there.

  Every transcription block is OCR output at roughly 75-82% accuracy. Blocks
  carry resp="#ocr" cert="low" and cards carry status="unverified" until a
  human corrects them.
-->
<TEI xmlns="{TEI_NS}" xml:id="berg-{slug}">
  <teiHeader>
    <fileDesc>
      <titleStmt>
        <title>Catalog of the Berg Collection — {label}</title>
        <respStmt xml:id="ocr">
          <resp>Uncorrected machine transcription</resp>
          <name>Tesseract 5</name>
        </respStmt>
        <respStmt xml:id="vlm">
          <resp>Machine transcription from card image</resp>
          <name>Claude (vision model)</name>
        </respStmt>
        <respStmt xml:id="human">
          <resp>Human correction and verification</resp>
          <name>Project contributors</name>
        </respStmt>
      </titleStmt>
      <publicationStmt>
        <availability status="free">
          <p>Source scans public domain (CC0), digitized by Google from the
          New York Public Library, delivered via HathiTrust.</p>
        </availability>
      </publicationStmt>
      <sourceDesc>
        <bibl>
          <title>Catalog of the Berg Collection of English and American
          Literature, New York Public Library</title>
          <note>Volume key <ident>{label}</ident>. 1-bit bitonal scan at
          ~580 DPI.</note>
        </bibl>
      </sourceDesc>
    </fileDesc>
    <encodingDesc>
      <editorialDecl>
        <correction status="unfinished" method="silent">
          <p>Cards are seeded from OCR and corrected by hand through the web
          front end. <att>status</att> on each card is "unverified", "verified",
          or "blank"; <att>resp</att> on each transcription block names who
          produced its text.</p>
        </correction>
      </editorialDecl>
    </encodingDesc>
  </teiHeader>

  <facsimile>
{chr(10).join(facs_blocks)}
  </facsimile>

  <text>
    <body>
{chr(10).join(page_blocks)}
    </body>
  </text>
</TEI>
"""


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        epilog="examples:\n"
               "  python -m bergcat.tei test -o out.xml\n"
               "  python -m bergcat.tei test v2:103-106 --vlm a.json --vlm b.json "
               "-o out.xml",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("sources", nargs="+", metavar="SOURCE",
                    help="volume, optionally with pages: 'test', 'v2:103-106'")
    ap.add_argument("--vlm", type=Path, action="append", metavar="RESULTS_JSON",
                    help="VLM transcriptions from bergcat.transcribe; repeatable. "
                         "Cards missing from the results fall back to OCR.")
    ap.add_argument("-o", "--out", type=Path, required=True)
    args = ap.parse_args()

    xml = build([parse_source(s) for s in args.sources], args.vlm)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(xml, encoding="utf-8")
    print(f"wrote {args.out}  ({len(xml)/1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
