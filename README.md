# bergcatalog

Digitizing the card catalog of the Berg Collection (New York Public Library) from
scanned volumes, preserving the structure, formatting, and typography of each card.

Research findings and the measurements behind these decisions: **[NOTES.md](NOTES.md)**.

## The shape of the problem

Each page holds a 7 × 3 grid of photographed catalog cards. Each card is one item
in the collection, with a consistent but rich internal format — tracing marks,
main entry, indented body, underlined cross-references, holdings codes, and
enumerated copies with individual descriptions. Entries can continue onto a second
card elsewhere in the volume.

The scans are 580 DPI but **1-bit bitonal**, with typewriter strokes bled together.
Tesseract tops out around 82% token recall on a *good* card, with errors landing on
exactly the proper names and numbers a catalog exists to record. A VLM reads the
same card cleanly. So:

> **Transcribe with a vision model. Use OCR as an independent auditor, not as the
> transcript. Keep every card image as ground truth.**

## Output format

**TEI P5 is the archival format**; the HTML front end is generated from it.

This turns out to fit the source unusually well. TEI already has native machinery
for the exact problem this scan creates — a transcription you cannot fully trust:

| Need | TEI |
| --- | --- |
| An uncertain reading | `<unclear cert="medium" resp="#vlm">1938</unclear>` |
| Text that cannot be read | `<gap reason="illegible"/>` — never a guess |
| Who read it: model or human | `@resp`, declared in `<respStmt>` |
| Original lineation | `<lb/>` |
| Underlined `With:` links | `<hi rend="underline">`, `<ref type="with">` |
| Tracing marks | `<fw type="tracing" place="top-right">` |
| Card image + coordinates | `<facsimile><surface><zone>` |
| `(card 2)` continuations | `@prev` / `@next` between card `<div>`s |
| Manuscripts vs. printed books | `<msDesc>` vs. `<biblStruct>` |

Each card is a `<div type="card">` holding two parallel views: a
`<div type="transcription">` (what is physically on the card — authoritative) and
a `<listBibl type="description">` (bibliographic interpretation). The renderer
uses the first for the card face and the second for the structured panel.

See **[demo/berg_demo.xml](demo/berg_demo.xml)** for the worked design target and
**[demo/index.html](demo/index.html)** for what it renders to.

## Pipeline

```text
  bergv*.pdf
      │
  [1] probe      inspect structure, resolution, encoding                → reports/
      │
  [2] pages      pull native-res page images straight from the CCITT
      │          streams (no re-render), deskew, crop scan margins      → data/pages/
      │
  [3] segment    fit the known 7x3 grid, emit 21 padded card crops
      │          with full provenance                                   → data/cards/
      │
  [4] qa         contact sheets for human verification of segmentation  → data/qa/
      │              ── MANUAL GATE: eyeball before spending on [5] ──
      │
  [5] transcribe Batch API, one request per card, strict JSON schema,
      │          verbatim-first, cached system prompt                   → data/out/raw/
      │
  [6] crosscheck fresh Tesseract + embedded Google text layer as two
      │          independent signals; flag disagreements on names,
      │          dates, numbers                                         → data/out/flagged/
      │
  [7] link       resolve "(card 2)" continuations, "See:" cross-refs,
      │          "With:" links; preserve genuine duplicates             → data/out/linked/
      │
  [8] tei        serialize to TEI P5                                    → data/out/tei/
      │
  [9] render     TEI → HTML front end with card styling                 → data/out/site/
```

Stages 1–4 are deterministic and cheap; rerun freely. Stage 5 costs money and is
the only one that needs an API key. Stage 4 is a deliberate human checkpoint —
mis-segmentation is the one error that silently poisons everything downstream.

## Layout

```text
src/bergcat/
  config.py           paths, grid constants, DPI settings        [done]
  probe.py            [1] PDF structure inspection               [done]
  pages.py            [2] page extraction + deskew               [todo]
  segment.py          [3] grid fitting + card cropping           [todo]
  qa.py               [4] contact sheet generation               [todo]
  schema.py           VLM output contract, maps onto TEI         [done]
  transcribe.py       [5] Batch API transcription                [todo]
  crosscheck.py       [6] OCR cross-validation                   [todo]
  link.py             [7] continuation + xref resolution         [todo]
  tei.py              [8] TEI P5 serialization                   [todo]
  render.py           [9] TEI → HTML                             [done]
  static/cards.css    card styling (light + dark)                [done]
demo/
  berg_demo.xml       hand-encoded TEI design target, 4 cards
  index.html          rendered output
  images/             card crops for the demo
data/                 derived output (gitignored — all regenerable)
reports/              probe output, audit summaries
```

Source PDFs stay at the project root; nothing writes to them.

## Setup

Already provisioned. The package is installed into `.venv` in editable mode, so
`python -m bergcat.*` works with **no `PYTHONPATH`** — just activate the venv:

```powershell
.\.venv\Scripts\Activate.ps1
python -m bergcat.checkkey
```

If `python` resolves to `C:\Python311\python.exe` you are outside the venv and
will get `ModuleNotFoundError: No module named 'bergcat'`. Either activate it as
above, or call the interpreter directly:

```powershell
.\.venv\Scripts\python.exe -m bergcat.checkkey
```

To recreate from scratch on another machine:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

### API key (stage 5 only)

Transcription bills to the **Anthropic API** (`console.anthropic.com`), which is
a separate account from a Claude Code / claude.ai subscription. Stages 1–4 and
8–9 cost nothing and need no key.

```powershell
setx ANTHROPIC_API_KEY "sk-ant-api03-..."   # then open a NEW terminal
python -m bergcat.checkkey                  # confirm, no network call
python -m bergcat.checkkey --verify         # one ~$0.0001 live call
```

> ⚠️ **This project lives inside OneDrive.** Use the environment variable, not a
> `.env` file — anything written into the project folder is synced to the cloud.
> `config.py` will read `.env` if you have one (it is gitignored), but on a
> synced folder that means uploading your key.

## Two pages — which to open

| Open this | What it is |
| --- | --- |
| `data/out/site/index.html` | **The real thing.** All 168 cards of `wise.pdf`, OCR-seeded, with the correction editor. Regenerate from the PDF any time. |
| `demo/index.html` | 4 cards, **hand-encoded** in `demo/berg_demo.xml`. The design target: shows `<msDesc>`/`<biblStruct>` structured descriptions that the OCR seed does not yet produce. |

Both are generated by the same renderer and both carry the editor. The demo is a
specification, not a sample of pipeline output — don't judge OCR quality by it.

## Search

Client-side, no backend. **Results filter in place, so surviving cards keep the
drawer's filing order** — a catalogue reordered by relevance is not a catalogue.

- **Terms are AND-ed**; `"quoted phrases"` stay intact
- **Accent-folded** — `bronte` finds `Brontë`
- **Scope**: whole card / main entry only / notes only
- **Filters**: unverified, verified, **flagged uncertain**, continuations,
  cross-references
- Matches highlighted in the card face; the TEI source is never touched
- `/` focuses the box, `Esc` clears it
- Query is mirrored to the URL hash, so a search is shareable:
  `index.html#q=byron&facet=uncertain`

**"Flagged uncertain" is the correction worklist** — cards where the model
recorded doubt rather than guessing. On the current 735 that is 595 cards.

## The correction workflow

Cards are seeded from OCR and corrected by hand through the browser. Every card
carries `status` (`unverified` / `verified` / `blank`) and every transcription
block carries `@resp` naming who produced it. Correcting a card in the front end
rewrites both — `resp="#ocr"` becomes `resp="#human"`, the `<unclear>` hedges the
machine added are stripped, and a `@change` date is stamped.

That means the file records not just *what* it says but *who read it and how much
to trust them*, and the pool of unverified cards shrinks as people use the site.

Build and open it:

```powershell
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH = "src"
python -m bergcat.segment test          # fit grids, cut 168 card crops
python -m bergcat.qa test --overlay     # LOOK at these before going further
python -m bergcat.tei test -o data/out/site/wise.xml
python -m bergcat.render data/out/site/wise.xml -o data/out/site/index.html
```

Then open `data/out/site/index.html`. Click **Edit TEI** on any card: the card
face is replaced in place by a textarea holding that card's TEI. Save with the
button or Ctrl/⌘+Enter. Invalid XML is refused rather than silently swallowed.
Work persists in `localStorage`; **Export corrected TEI** splices every
correction back into the full document and downloads it.

Verified in-browser: 168 cards render, 0 console errors, edit → save → re-render
→ provenance rewrite → persistence → reload → export all pass.

## Status

| Stage | State |
| --- | --- |
| 1 probe | done |
| 2–3 pages + segment | done — **21/21 cards on all 8 test pages** |
| 4 QA overlay / contact sheet | done |
| 5 VLM transcription | done — **168/168, 0 errors, $2.81** on `wise.pdf` |
| 6 crosscheck | not built |
| 7 continuation linking | partial — `@prev` set within a page only |
| 8 TEI | done — multi-source, VLM- or OCR-seeded |
| 9 render + correction UI | done |
| — `migrate_grids` | backfills `src_width`/`src_height` into old metadata |

Demo covers `wise.pdf` — 8 pages, 168 cards, 163 with content.

**Measured rate: $0.0145–0.0167/card** (Opus 5, `--effort low`, Batch API) across
three runs on three volumes; larger batches are cheaper because they amortise the
cached system prompt. Six volumes are on disk — **4,914 pages, 103,194 cards,
≈$1,500** to transcribe the lot. Full numbers in [NOTES.md §10](NOTES.md).

Adding a volume, or a page range from one, to the site:

```powershell
python -m bergcat.segment v2 --pages 103-106      # cut card crops
python -m bergcat.qa      v2 --pages 103-106 --overlay   # LOOK at these
python -m bergcat.transcribe v2 --dry-run         # price it, free
python -m bergcat.transcribe v2 --submit          # -> batch id
python -m bergcat.transcribe --collect <batch_id> # -> data/out/vlm/<id>.json

# One TEI document spanning any number of sources and batches:
python -m bergcat.tei test v2:103-106 `
    --vlm data/out/vlm/<batch-a>.json `
    --vlm data/out/vlm/<batch-b>.json `
    -o data/out/site/catalog.xml
python -m bergcat.render data/out/site/catalog.xml -o data/out/site/index.html
```

Card ids carry their volume (`v2-p0103-r0c0`), so sources coexist in one
document without collision. `--vlm` is repeatable and falls back to OCR for any
card missing from the results, so a partial batch still yields a complete site.

**Order is the catalogue's, not the command line's.** Sources are sequenced by
their real volume and page — including extract aliases, so `test` (= bergv4
pp.431–438) files after `v3`, not first — and cards run **down each column, then
across**. Name the sources in any order; the document comes out F → P → S → W.
See [NOTES.md §13](NOTES.md).

**Currently built** in `data/out/site/` — **735 cards, 35 pages**, all
VLM-transcribed, $11.14 spent:

| Source | Pages | Cards | Batch |
| --- | --- | --- | --- |
| `wise.pdf` | 8 | 168 | $2.81 |
| `bergv2` pp.103–106 | 4 | 84 | $1.34 |
| `bergv3` pp.537–538, 540–547, 703–715 | 23 | 483 | $6.99 |

Segmentation is the slow step (~45s/page) and runs once per source. The TEI
build reads only what segmentation wrote and never opens a PDF, so rebuilding
the whole site takes seconds.
