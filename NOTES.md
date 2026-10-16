# Berg Catalog Digitization — Research Notes

Findings from probing `wise.pdf` (pages 431–438 of `bergv4.pdf`) on 2026-08-06.
These drive every architectural decision in `README.md`.

## 1. What the source actually is

| Property | Value |
| --- | --- |
| Provenance | Google Books scan, delivered via HathiTrust, **CC0** (no rights constraint) |
| Handle | `https://hdl.handle.net/2027/nyp.33433076812993` |
| Page images | **1-bit bitonal**, CCITT Group 4 fax encoding |
| Resolution | **~580 DPI** (e.g. 5624 × 8324 px on a 699 × 1033 pt page) |
| Volumes on disk | `bergv1.pdf` (915 pp), `bergv4.pdf` (793 pp) — both 1-bit |
| Cards per page | **7 rows × 3 columns = 21** |
| Existing text layer | Yes, ~4–5k chars/page (Google OCR) |

### The critical constraint: the tonal information is gone

The scan is 1-bit at a threshold set too aggressively for typewriter text. Strokes
bleed together and letter counters (the holes in `e`, `a`, `o`, `s`, `8`) fill in
solid. `Noel` reads as `Boel`; `150` reads as `180`; `author` reads as `aather`.

**This cannot be undone.** There is no grayscale to recover from — morphological
cleanup can thin strokes but cannot reconstruct destroyed information. Confirmed
identical in both volumes, so there is no better copy locally.

*Open avenue:* HathiTrust/IA may hold a higher-bit-depth original. Worth one
check before committing to the pipeline, but plan as if 1-bit is all we get.

## 2. Measured: OCR is not good enough

Tesseract 5 on a **clean, well-formed** card at native 580 DPI, scored as token
recall against a human reading of the same card:

| Preprocessing | psm 6 | psm 4 |
| --- | --- | --- |
| raw | 68.2% | 75.0% |
| dilate 2×2 (thin the ink) | 68.2% | **81.8%** |
| dilate 3×3 | 68.2% | 70.5% |
| open+close | 70.5% | 79.5% |
| downsample to 300 DPI | 61.4% | 65.9% |
| downsample + blur | 27.3% | 47.7% |

Ceiling is ~82% on a *good* card, and the errors are semantically destructive
exactly where a catalog must be exact — proper names, dates, edition numbers,
copy counts. Preprocessing moves this by a few points; it does not change the verdict.

The **embedded Google text layer** is no better (same class of errors: `Boston:`→
`Bostons`, `See:`→`Sees`, `pseud.`→`peoud`, `Fitz-James`→`Pits-James`, plus Greek/
Cyrillic/Arabic characters substituted at random) **and its reading order is
scrambled across card boundaries** — text from card 1 sorts into the middle of
card 2. It is unusable as a transcript.

It is, however, valuable for two other things: it carries **per-block bounding
boxes**, and it is an **independent signal** for cross-checking the VLM.

## 3. Measured: a VLM reads it correctly

The same card, read from a 300-DPI crop by Claude, transcribed cleanly including
`Gordon Noel`, `Dunedin press`, `150 copies printed`, and the two-copy structure
that Tesseract mangled. This is the decision: **VLM-primary, OCR-as-auditor.**

## 4. Card structure to preserve

Observed on page 1 of the test file. Any schema must carry all of these:

- **Tracing / added-entry marks**, top-right: `(Symons, J.)`, `m.b. (Marsh)`,
  `+ + + (Shaw)`, `B (Tennyson)`, `(Henry, Patrick)`
- **Main entry** heading, flush left
- **Indented body** — title, imprint, collation, edition
- **Underlined runs** — nearly always `With:` links to related items
- **Holdings codes**: `H.`, `B.`, `Y.` (single letters, bottom-left)
- **Enumerated copies with per-copy descriptions** — e.g.
  `Two copies: 1. Presentation copy, inscribed, from the author to W. T. H. Howe. 2. H.`
- **Continuation cards**: `Winter, Wm. Miscellaneous ms. material   (card 2)` —
  an entry spans multiple cards, and the continuation may sit in a different
  column, or on a later page
- **`See:` cross-references** — `Wintle, E. D., pseud.   See: Kipling, Rudyard.`
- **Genuine duplicates** — `Winslow, Octavius, Life in Jesus` appears twice on
  one page, near-identical. Real in the source; must be preserved, not deduped.

## 5. Segmentation

The card grid has ruled borders, but two naive approaches both fail:

| Approach | Result on 8 test pages |
| --- | --- |
| Contour holes in a morphological line mask | 15/21, 11/21, 14/21 cells — misses cells with faint or broken borders |
| Projection-profile peak-picking | Found 5–9 horizontal and 3–5 vertical rules (expect 8 and 4); spurious verticals from the scan's spine strip and margin watermark |

The fix is to **fit a model instead of detecting cells**: the grid is known to be
exactly 7 × 3 and near-regular. Crop the scan margins first, then search for the
offset+pitch that maximizes ruling-line energy, and take the cartesian product.
Pad the crops generously — cards have wide margins, so a few px of grid error is
harmless, while a missed card is not.

Pages carry a measured skew of −0.44° to +0.95°, plus mild non-linear warp near
the gutter (page-top border visibly curves). Deskew is required; full dewarp
probably is not, given the padding.

**Segmentation must be human-verified.** It is cheap to eyeball a contact sheet
and catastrophic to transcribe 17,000 mis-cropped cards. This is the one manual
gate in the pipeline.

## 6. Scan artifacts to strip

Three images repeat on every page (same xrefs — 20, 22, 23):

- `154 × 34 px` and `272 × 31 px` — "Digitized by Google" / "Original from NYPL"
- `67 × 1344 px` — gutter shadow strip down the left edge

Plus a vertical HathiTrust watermark in the left margin. All are outside the card
grid and fall away once the grid is cropped.

## 6b. Segmentation, solved

Fitting the known 7 × 3 grid works where cell detection did not. Two bugs had to
be found by *looking at the overlay*, not by reading the score:

1. **Row pitch search range was too narrow.** Bounded at `h*0.10` when the true
   pitch is `h*0.121`, so the correct fit was outside the search space. The
   optimiser did the only thing it could: crammed all 8 rules into the top 45%
   of the page. Fixed by bounding the search by **span** (the grid must cover
   most of the plate) rather than by pitch — span is the physically meaningful
   constraint and rejects compressed fits automatically.
2. The `fit` score is a weak quality signal — page 3 scores lowest (0.62) purely
   because its rules are fainter, yet segments perfectly. **Do not gate on it.**

Result on the 8 test pages: **21/21 cards on every page**, skew −0.44° to +0.95°.
Human QA overlay remains the real gate.

## 6c. OCR reading order — a trap worth documenting

Character accuracy is the obvious OCR problem. **Line order is the subtle one,
and it is more damaging.** Two independent causes, both fixed:

- **Grouping words by tesseract's `block_num/par_num/line_num`.** On this
  material its block segmentation fragments a card into pseudo-columns, so
  lines emerge interleaved. Fixed by clustering words into lines on **vertical
  overlap** of their boxes — geometry is reliable even when characters are not.
- **Splitting body-vs-notes by indentation.** A wrapped line returning to the
  left margin is a *continuation*, not a later block; grouping by indent files
  it after every indented line. Fixed by splitting on **vertical gap** instead.
- `--psm 3` beats `--psm 4` for order (psm 4 recovers marginally more characters
  but fragments the layout).

After the fix the Byron card comes out in perfect line order — with every
character error still present (`James`→`Janes`, `George`→`Ocorge`, `Noel`→`Boel`,
`150`→`180`, and `author` simply dropped).

## 7. Scale and cost

All four volumes are now on disk:

| Volume | Pages | Cards |
| --- | --- | --- |
| bergv1 | 915 | 19,215 |
| bergv2 | 835 | 17,535 |
| bergv3 | 897 | 18,837 |
| bergv4 | 793 | 16,653 |
| **Total** | **3,440** | **72,240** |

Batch API (50% off) + a cached system prompt, at ~2,400 input / ~450 output
tokens per card:

| Model | Per volume | All four volumes |
| --- | --- | --- |
| Claude Opus 5 | $218 | **$872** |
| Claude Sonnet 5 | $131 | $523 |
| Claude Sonnet 5 (intro, thru 2026-08-31) | $87 | $349 |
| Claude Haiku 4.5 | $44 | $174 |

Without batching or caching, Opus 5 would be $1,744. The `wise.pdf` test file
(8 pages, 168 cards) is **$2.03**.

**Cost is not the binding constraint at this scale — human correction time is.**
See §10.

### Correction, 2026-08-06: measured input is higher, and thinking dominates

The table above assumed ~2,400 input and 450 output tokens per card. `count_tokens`
(free) against real crops measures **3,233 input tokens/card** — the card image is
~2,500 and the system prompt ~700.

More importantly, **Claude Opus 5 thinks by default and thinking bills as output
tokens**, which the original estimate ignored entirely. Output volume is now the
dominant term and it cannot be known without running:

| Output tokens/card | `wise.pdf` (168) | All 72,240 cards |
| --- | --- | --- |
| 400 | $2.20 | $945 |
| 800 | $3.04 | $1,306 |
| 1,500 | $4.51 | $1,938 |
| 3,000 | $7.66 | $3,293 |

So the honest figure is **$950–$1,900**, not $872, and it hinges on `--effort`.
`transcribe.py` therefore defaults to `--effort low` and reports observed token
usage plus a full-corpus extrapolation after every run. Measure on a small batch
before committing to a volume.

### Measured, 2026-08-06: full `wise.pdf` run

Batch `msgbatch_01D8qcp9ZnRvNj61BKMrQXfF`, 168 cards, Opus 5, `--effort low`,
completed in about three minutes.

| | |
| --- | --- |
| Succeeded | **168 / 168** (0 errors, 0 unparseable) |
| Input | 419,930 uncached + 158,631 cache-read + 227,601 cache-write |
| Output | 80,658 — **480 tokens/card**, thinking included |
| Cost | **$2.81** ($0.01672/card) |
| Extrapolated | **$1,208** for all 72,240 cards |

Output landed at the low end of the projection, so `--effort low` is doing its
job. **$1,208 is the number to plan against** — 1.7¢/card.

Prompt caching worked: 158,631 tokens served from cache at 0.1x. The write
(227,601) is larger than the read because a batch fans out faster than the cache
warms; on a bigger batch the ratio improves.

Model self-assessment across the 168: **31 high / 137 medium / 0 low**.

### Second run: bergv2 pp.103-106

Batch `msgbatch_01CA32gQwwBiut1hm2cbo1SG`, 84 cards. **84/84, 0 errors, $1.34**
($0.0160/card, again 480 output tokens/card). Different volume, different
subject matter (forgeries, fore-edge paintings), same per-card economics — the
rate is stable enough to plan against.

### The corpus is six volumes, not four

v5 and v6 arrived. `config.VOLUMES` now discovers volumes from disk rather than
listing them, because a hardcoded list silently rejected `v2` the first time.

| Volume | Pages | Cards | At $0.0160/card |
| --- | --- | --- | --- |
| v1 | 915 | 19,215 | $307 |
| v2 | 835 | 17,535 | $281 |
| v3 | 897 | 18,837 | $301 |
| v4 | 793 | 16,653 | $266 |
| v5 | 787 | 16,527 | $264 |
| v6 | 687 | 14,427 | $231 |
| **All** | **4,914** | **103,194** | **$1,651** |

### Two bugs the second run exposed

1. **Holdings rendered twice on 58% of cards** (98/168). The VLM correctly
   returns the holdings letter *both* as verbatim text inside `blocks` and as a
   parsed `holdings_codes` entry; the TEI builder emitted both as transcription,
   so `H.` appeared at the foot of the card and again below it. The model was
   right and the builder was wrong: verbatim text belongs in the transcription,
   parsed codes in the description layer.
2. **`xml:id` is an NCName.** Building one document from several sources, the
   human-readable label `test, v2 pp.103-106` was used as the document id, and
   lxml rejected it — no spaces or commas allowed. Labels and ids now come from
   separate functions.

Also raised `CARD_PAD_FRAC` 0.02 -> 0.04 after bergv2 p103 (skew -1.91 deg,
worse than anything in wise.pdf) showed heading lines grazing the row boundary.

### Third run: bergv3 pp.537-538, 540-547, 703-715

Batch `msgbatch_0192fB45RAMqnB7eThhUKvfr`, 483 cards. **483/483, 0 errors,
$6.99** — and **$0.01446/card**, cheaper than the two smaller runs because a
bigger batch amortises the cached system prompt better (942k tokens cache-read
against 219k cache-write, versus roughly 1:1.4 on the 168-card run).

Rate observed across three runs on three volumes:

| Run | Cards | Out/card | $/card |
| --- | --- | --- | --- |
| wise.pdf | 168 | 480 | $0.01672 |
| bergv2 | 84 | 480 | $0.01600 |
| bergv3 | 483 | 473 | **$0.01446** |

Bigger batches are cheaper. Planning figure for the full 103,194 cards:
**~$1,500**, and lower if run in large batches.

Prompt addition for this run: crops carry a 4% margin, so a sliver of the
neighbouring card can appear along an edge. The prompt now says to transcribe
only the card at the centre and ignore fragments outside its border.

## 11. Rebuild cost: the TEI stage must not touch the PDF

The first multi-source build took **~10 minutes for 12 pages** and a foreground
call timed out mid-run, leaving a half-written site. Cause: `tei.py` re-opened
each PDF, re-rendered the page at 580 DPI and re-ran deskew — roughly 45s/page —
work `segment.py` had already done.

Fixed by making `tei.py` read only what earlier stages wrote: the grid metadata
and card crops. It never opens a PDF now.

**The same 12-page build: 205s -> 2.8s.** The 35-page build is 205s, and most of
that is one-off web-thumbnail generation for newly added cards (regenerated only
when missing or older than their crop).

Two supporting changes were needed:

- `segment.py` records `src_width`/`src_height` (the 580 DPI page size) so
  facsimile zones can be placed in source pixels without the PDF. Grids written
  before this are repaired by `bergcat.migrate_grids`, which recovers the scale
  exactly from a crop's pixel width over its cell width.
- `segment.py` now **merges** its metadata rather than overwriting it, since
  page ranges are cut incrementally (`--pages 703-715`, then `--pages 537-538`).

## 13. Filing order: cards run DOWN each column, not across

A card catalog *is* its filing sequence, so the traversal of the 7 × 3 grid is
not a presentation detail — get it wrong and the artefact is no longer a
catalogue. Determined by measurement against 735 transcribed main entries.

**Column-major** (`r0c0 r1c0 ... r6c0`, then `r0c1 ...`) recovers the drawer:

```text
Winship, George Parker      Winter, William (trip)     Winters, Ivor
Winslow, Edward             Winter, William            Wintle, E. D.
Wingate, Sir Francis        Winter, William            Wirt, William
WINSLOW, MARY               Winter, Wm. (card 2)       Wisdom; the magazine
Winslow, Octavius           Winter garden, London      Wise, Thomas James
Winter, John Keith          The winter owl             [Wise, T. J.] Tennyson
Winter, Keith               Winterich, John T.         Wise, Thomas James
```

Read down, then across, that is alphabetical. Read **row-major** it is not:
`Winship, Winter, Winters, Winslow, Winter, Wintle, Wingate…`

### Why the aggregate metric nearly misled me

Scoring adjacent pairs gave only 66.3% vs 61.3% — a weak signal — and a strict
inversion count said column-major was cleaner on just 20/35 pages. Inspecting the
"worst" pages showed the metric, not the traversal, was at fault: they are runs
where *every* card shares one main entry.

```text
v3 p709 : Scott, Sir Walter  x21
test p2 : Wise, Thomas James / WISE, T. J.  x21
```

`wise, t. j.` sorts before `wise, thomas james` by string comparison, but the
catalogue files them **together** and sequences them by subordinate heading.

**This is the reason the order must be preserved and never recomputed.** No
string sort reproduces library filing rules — abbreviated vs expanded forenames
collating as one heading, leading articles ignored, subject subdivisions
interleaved. The drawer's own sequence is the authority.

### How it is recorded

- `config.catalog_order()` is the single definition of the traversal; `tei.py`
  and the QA overlay both use it, so numbering and emission cannot drift apart.
- Every card carries `@sortKey` (zero-padded, running across the whole
  document). Document order already implies sequence, but a consumer that
  merges fragments or re-sorts would lose it silently.
- Pages are emitted by (volume, page) regardless of the order sources were named
  on the command line.

### The traversal was right; the *source* order was wrong

Column-major fixed the sequence within a page, but the site still read out of
order — because the sources themselves were sequenced by their bare key. Each
source covers a different stretch of the alphabet:

| Source | Letters |
| --- | --- |
| `test` (= bergv4 pp.431-438) | **W** — Winship … Wise |
| `v2` pp.103-106 | **F** — Ford, Forest, Forgeries |
| `v3` pp.537-547 | **P** — poets … Pollard |
| `v3` pp.703-715 | **S** — Scott |

Sorting on the key put `test` first (`t` < `v`), so the catalogue ran
**W → F → P → S**. `wise.pdf` is an *extract* of bergv4 pp.431-438 and files
where those pages do — last, not first.

`config.source_position()` resolves extract aliases (`SOURCE_ALIASES`) to their
real volume and page before sorting. The document now runs `[Ford, Simon]` …
`Wither, G.` — **F → P → S → W**.

Two lessons, both about verification rather than the fix:

- **A correct component does not make a correct whole.** Every within-page check
  passed while the document-level sequence was wrong, because nothing tested the
  spine. The check that would have caught it is one line: does the first entry
  of each page ascend?
- **The alphabetical-sortedness metric is close to useless here** and nearly
  sent the traversal work the wrong way twice. Main entries repeat verbatim for
  whole pages (`Scott, Sir Walter` x21), abbreviate inconsistently (`Wise, T. J.`
  vs `Wise, Thomas James`), carry subject subdivisions, and continuation cards
  open mid-sentence. Of 245 flagged "descents" across the corpus, essentially all
  are artefacts of the key. **Read the sequence; do not score it.**
- Continuation `@prev` links follow filing order, so a "(card 2)" points at the
  card that actually precedes it in the drawer.

## 14. Search, and a badge bug it exposed

Search filters **in place** rather than reordering, so results stay in filing
order (s.13). Everything is client-side over a static file.

Building it surfaced a bug nothing else had: the **cross-reference facet matched
zero cards** out of 735. The two encoding paths record the same fact
differently — OCR writes `<note type="cardFunction">`, the VLM writes
`<ab type="crossReference">` — and the badge only looked for the OCR form. Since
every current card is VLM-seeded, no card was ever badged. Continuations were
under-counted the same way (66 badged against 69 in the TEI, the missing three
being first-on-page cards with no `@prev`).

Fixed by badging from **content** rather than from one path's artefacts. General
lesson: when two producers write the same fact in different shapes, any consumer
that checks only one shape fails silently on the other's output.

Measured behaviour on the 735-card corpus:

| Query / filter | Cards |
| --- | --- |
| `byron` | 10 |
| `scott` | 275 |
| `scott waverley` (AND) | 33 |
| `"anne of geierstein"` | 2 |
| `bronte` (accent-folded) | 13 |
| `forgeries`, whole card | 63 |
| `forgeries`, main entry only | 48 |
| flagged uncertain | 595 |
| continuations | 69 |
| cross-references | 54 |

Highlighting walks text nodes in the rendered face only; the TEI textarea is
never touched, verified by asserting the editor's contents contain no `<mark`.

## 12. A whitespace bug the reflow toggle introduced

Hiding `<br class="lb">` to reflow the card relied on the source XML happening to
have a newline after each `<lb/>` — true for generated, pretty-printed output.
A card **hand-edited in the front end** and saved as compact XML has none, so
after a save the reflowed card read `GeorgeGordon`, `forprivate`, `B(Byron)`.

Caught by the browser test, which exercises edit -> save -> re-render rather than
just loading the page. The renderer now emits `<br class="lb"/>` followed by an
explicit space character, in both the Python and JavaScript paths, so separation
never depends on how the XML happens to be formatted.

Per card at native resolution: a crop is ~1660 × 1032 px, ≈ 2,300 image tokens
(well inside the 2576 px long-edge / ~4,784 token high-resolution ceiling that
Opus 5 and Sonnet 5 support). Budget ~2,400 input + ~400 output tokens/card.

Per ~17,000-card volume, via the **Batch API (50% off)**:

| Model | Input $/MTok | Output $/MTok | Est. per volume |
| --- | --- | --- | --- |
| Claude Opus 5 | 5.00 | 25.00 | **~$190** |
| Claude Sonnet 5 | 3.00 | 15.00 | ~$110 |
| Claude Sonnet 5 (intro, thru 2026-08-31) | 2.00 | 10.00 | ~$75 |

**Cost is not the binding constraint** — accuracy is. Default to Opus 5; a few
hundred dollars per volume is noise against the cost of re-checking bad data.

Prompt caching cuts this further: the schema/instruction system prompt is
identical across every card, so it caches at ~0.1× on read.

## 8. Anti-hallucination

A VLM will confidently "correct" a damaged word. On a bibliographic catalog that
is the primary risk — worse than an obvious OCR garble, because it looks right.

Controls, in order of value:

1. **Keep the card image.** It is ~30 KB and it is the ground truth. Every record
   links to its crop.
2. **Cross-check against two independent signals** — fresh Tesseract, and the
   embedded Google layer. Agreement on a proper name or number is strong evidence;
   disagreement flags the field for review.
3. **Require verbatim-first output.** Make the model emit the literal lines before
   any parsed interpretation, and forbid silent normalization.
4. **Make uncertainty expressible.** The schema must have a place to say
   "illegible" rather than forcing a guess.
5. **Sample audit.** Human-check a random n per batch and track the error rate.

## 9. Environment as provisioned

- Python 3.11.4 in `.venv/` at the project root
- `pymupdf` 1.28, `opencv-python` 5.0, `numpy` 2.4, `pillow`, `anthropic` 0.120,
  `pytesseract`, `rapidfuzz`, `pydantic`, `jinja2`, `tqdm`
- Tesseract 5 at `C:\Program Files\Tesseract-OCR\tesseract.exe`
- Poppler utils (`pdftoppm`, `pdfimages`) present via MiKTeX
- **`ANTHROPIC_API_KEY` is not set** — required before the transcription stage

Note: OpenCV 5.0 changed `HoughLinesP` output shape from `(N,1,4)` to `(N,4)`.
Reshape with `np.asarray(lines).reshape(-1, 4)` to stay compatible.

## 10. OCR-seed vs VLM-seed for the correction workflow

The correction front end (`data/out/site/`) works against either baseline, and
building it was the right call regardless — it is the durable asset. The open
question is only **what to seed it with**, and cost turns out not to decide it.

Measured on the same card, in the same pipeline (VLM column is the real
2026-08-06 batch, not a projection):

| | OCR seed | VLM seed |
| --- | --- | --- |
| Cost, all 4 volumes | $0 | **$1,208** |
| Line order | correct after fixes | correct |
| `Wise, Thomas James.` | `Wise, Thomas Janes.` | correct |
| `of George` | `of Ocorge` | correct |
| `Gordon Noel, baron` | `Gordon Boel, baroa` | correct |
| `150 copies printed.` | `180 copies printed,` | correct |
| `from the author` | `from the` (word dropped) | correct |
| Per-card human effort | retype nearly every line | spot-check flagged fields |

At ~75–82% token accuracy, essentially **every** card needs correction and most
lines contain at least one error, so a corrector is retyping rather than
correcting. The scarce resource in a crowdsourced project is volunteer patience,
not dollars: $1,208 across 72,240 cards is **1.7 cents per card** to move the
starting point from "retype this" to "confirm this".

The VLM also does something OCR structurally cannot: **it reports its own
doubt**. On the Byron card it transcribed `180 copies printed.` and attached a
note, *"'180 copies printed' could be '150'"* — flagging the exact digit a human
needs to check rather than silently committing to a reading. Across 168 cards it
emitted 176 such notes. That turns "check every card" into a worklist.

The synthesis, and the recommendation:

1. **Seed with the VLM.** One pass, $872, most cards land correct.
2. **Keep the OCR pass anyway** — as the independent cross-check (§8), not as
   the transcript. Where OCR and VLM agree on a proper name or a number, that
   is strong evidence; where they disagree, flag the field for a human.
3. **Route humans to the flagged minority** rather than the whole corpus. The
   `status`/`resp` machinery already supports this; it only needs the queue.

The user's insight stands either way: **the correction UI plus TEI provenance is
the durable asset**, and the count of cards needing review does fall over time.
Seeding with the VLM just means it starts an order of magnitude lower.
