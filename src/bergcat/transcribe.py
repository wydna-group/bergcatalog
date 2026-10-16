"""Stage 5: VLM transcription of card crops via the Batch API.

Design notes worth knowing before running this at scale:

* **Verbatim first.** The model is told to transcribe what is physically on the
  card and *not* to correct it. A catalog's value is in exact names, dates and
  copy counts; a plausible-looking correction is worse than a visible error
  because nothing downstream can detect it.
* **Uncertainty is expressible.** `[?]` for an unreadable character,
  `[illegible]` for a word. The schema never forces a guess.
* **Structured output** (`output_config.format`) guarantees the JSON parses, so
  there is no regex salvage step over 72,000 responses.
* **The system prompt is cached.** It is identical for every card, so it bills
  at ~0.1x on read.
* **Thinking is billed as output.** Opus 5 thinks by default; at 72k cards that
  is the single biggest cost lever, so effort defaults to `low` and every run
  reports the token usage it actually observed.

    python -m bergcat.transcribe test --dry-run          # cost, no spend
    python -m bergcat.transcribe test --limit 8 --submit # small live run
    python -m bergcat.transcribe --collect <batch_id>
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from . import config

MEDIA_TYPE = "image/png"

# --- output contract -------------------------------------------------------
# Hand-written rather than derived from the pydantic model: strict mode needs
# `additionalProperties: false` and every key in `required`, and optional values
# are expressed as empty string / empty list rather than null, which keeps the
# schema inside the supported subset (no nullable unions to negotiate).

CARD_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "is_blank", "confidence", "tracings", "blocks", "holdings_codes",
        "card_number", "see_reference", "with_references", "copies",
        "entry", "notes",
    ],
    "properties": {
        "is_blank": {
            "type": "boolean",
            "description": "True only if the crop shows no card at all.",
        },
        "confidence": {
            "type": "string",
            "enum": ["high", "medium", "low", "unreadable"],
            "description": "How legible the card was overall.",
        },
        "tracings": {
            "type": "array",
            "description": "Added-entry marks in the top-right corner.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["prefix", "heading"],
                "properties": {
                    "prefix": {
                        "type": "string",
                        "description": "Mark before the heading (m.b., B, + + +). Empty if none.",
                    },
                    "heading": {
                        "type": "string",
                        "description": "Parenthesised heading, e.g. '(Symons, J.)'.",
                    },
                },
            },
        },
        "blocks": {
            "type": "array",
            "description": "The card's text in reading order, top to bottom.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["role", "indent", "lines"],
                "properties": {
                    "role": {
                        "type": "string",
                        "enum": ["mainEntry", "body", "note", "holdings"],
                    },
                    "indent": {
                        "type": "integer",
                        "description": "Leading indent in character widths (0, 2, 4...).",
                    },
                    "lines": {
                        "type": "array",
                        "description": "One entry per physical line. Preserve lineation.",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["text", "underline"],
                            "properties": {
                                "text": {
                                    "type": "string",
                                    "description": "Verbatim text of this line.",
                                },
                                "underline": {
                                    "type": "boolean",
                                    "description": "True if any of the line is underlined.",
                                },
                            },
                        },
                    },
                },
            },
        },
        "holdings_codes": {
            "type": "array",
            "description": "Single-letter location codes: H., B., Y.",
            "items": {"type": "string"},
        },
        "card_number": {
            "type": "integer",
            "description": "N from '(card N)'. 0 if the card is not a continuation.",
        },
        "see_reference": {
            "type": "string",
            "description": "Target of a 'See:' cross-reference. Empty if none.",
        },
        "with_references": {
            "type": "array",
            "description": "Targets of 'With:' / 'In case with:' links.",
            "items": {"type": "string"},
        },
        "copies": {
            "type": "array",
            "description": "Enumerated copies with their individual descriptions.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["number", "description"],
                "properties": {
                    "number": {"type": "integer"},
                    "description": {"type": "string"},
                },
            },
        },
        "entry": {
            "type": "object",
            "additionalProperties": False,
            "required": ["main_entry", "title", "imprint", "collation", "edition"],
            "description": "Parsed interpretation. Empty strings where absent.",
            "properties": {
                "main_entry": {"type": "string"},
                "title": {"type": "string"},
                "imprint": {"type": "string"},
                "collation": {"type": "string"},
                "edition": {"type": "string"},
            },
        },
        "notes": {
            "type": "array",
            "description": "Transcription difficulties: damage, obscured text, ambiguity.",
            "items": {"type": "string"},
        },
    },
}

SYSTEM_PROMPT = """\
You transcribe catalog cards from the Berg Collection of English and American \
Literature at the New York Public Library. Each image is one typewritten card \
photographed in a 1-bit scan; ink has bled and letter counters are often filled \
in, so characters can be genuinely ambiguous.

Crops are cut with a margin, so a sliver of a NEIGHBOURING card may appear along \
an edge — usually a narrow vertical strip of truncated words at the left or \
right, or a line or two above or below the card's ruled border. Transcribe only \
the card occupying the centre of the image. Ignore fragments outside its border.

Transcribe VERBATIM. This is the single most important instruction.

- Reproduce exactly what is on the card, including apparent typos, unusual \
spacing, and archaic or inconsistent punctuation.
- Do NOT correct spelling, expand abbreviations, normalise dates, or tidy \
punctuation, even when you are confident you know the intended word. A silent \
correction is undetectable downstream and corrupts the catalogue.
- Preserve original lineation: one entry in `lines` per physical line.

Where the card is unreadable, say so rather than guessing:

- A single illegible character: `[?]`
- An illegible word or longer run: `[illegible]`
- Set `confidence` to reflect the whole card, and add anything ambiguous to \
`notes` (e.g. "date could be 1938 or 1958").

Structure:

- `tracings` are the marks in the TOP-RIGHT corner (`m.b. (Marsh)`, `+ + + \
(Shaw)`, `B (Byron)`). Split the leading mark from the parenthesised heading.
- `blocks` holds the card's text in reading order: the flush-left `mainEntry` \
heading, then indented `body`, then any `note` blocks separated by blank lines, \
then `holdings`.
- Underlining is meaningful — it usually marks a `With:` link. Set `underline` \
on any line that carries it.
- `holdings_codes` are the lone capitals near the foot (`H.`, `B.`, `Y.`).
- If the card shows `(card 2)` or similar, set `card_number`; otherwise 0.
- Enumerated copies ("Two copies: 1. Presentation copy... 2. H.") go in \
`copies`, one object per numbered item.

`entry` is your interpretation and may be left empty where the card does not \
supply a field. `blocks` is the authoritative record; when the two would \
disagree, keep `blocks` faithful to the card.

If the crop contains no card, set `is_blank` true and leave the rest empty."""


@dataclass
class CardRequest:
    card_id: str
    image_path: Path
    printed_page: str | None = None


def encode_image(path: Path, max_long_edge: int = config.VLM_MAX_LONG_EDGE) -> tuple[str, int, int]:
    """Read a card crop, cap its long edge, return base64 PNG plus dimensions.

    The cap matters for cost as well as limits: image tokens scale with area,
    and anything past the model's high-resolution ceiling is downscaled server
    side anyway, so sending more is pure waste.
    """
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise FileNotFoundError(f"could not read {path}")
    h, w = img.shape[:2]
    if max(h, w) > max_long_edge:
        scale = max_long_edge / max(h, w)
        img = cv2.resize(img, (int(w * scale), int(h * scale)),
                         interpolation=cv2.INTER_AREA)
        h, w = img.shape[:2]
    ok, buf = cv2.imencode(".png", img, [cv2.IMWRITE_PNG_COMPRESSION, 6])
    if not ok:
        raise RuntimeError(f"PNG encode failed for {path}")
    return base64.standard_b64encode(buf.tobytes()).decode("ascii"), w, h


def build_params(req: CardRequest, effort: str, max_tokens: int) -> dict:
    """The per-card request body, shared by dry-run costing and live submission."""
    b64, w, h = encode_image(req.image_path)
    where = f"Card {req.card_id}"
    if req.printed_page:
        where += f", printed page {req.printed_page}"
    return {
        "model": config.MODEL,
        "max_tokens": max_tokens,
        # cache_control on the system block: identical across every card, so it
        # is written once and read at ~0.1x for the rest of the run.
        "system": [
            {"type": "text", "text": SYSTEM_PROMPT,
             "cache_control": {"type": "ephemeral"}}
        ],
        "output_config": {
            "format": {"type": "json_schema", "schema": CARD_SCHEMA},
            "effort": effort,
        },
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": MEDIA_TYPE, "data": b64}},
                    {"type": "text", "text": f"{where}. Transcribe this card."},
                ],
            }
        ],
    }


def collect_cards(volume: str, limit: int | None = None) -> list[CardRequest]:
    """Find card crops written by `bergcat.segment`."""
    card_dir = config.CARDS_DIR / volume
    if not card_dir.exists():
        raise SystemExit(
            f"No card crops at {card_dir}.\n"
            f"Run:  python -m bergcat.segment {volume}"
        )
    paths = sorted(card_dir.glob(f"{volume}-p*.png"))
    if not paths:
        raise SystemExit(f"No card images found in {card_dir}")
    reqs = [CardRequest(card_id=p.stem, image_path=p) for p in paths]
    return reqs[:limit] if limit else reqs


# --- costing ---------------------------------------------------------------

PRICES = {  # USD per million tokens, (input, output)
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}


def dry_run(reqs: list[CardRequest], effort: str, max_tokens: int,
            sample: int = 3) -> None:
    """Price the run using the real token counter, without submitting anything.

    count_tokens is free, so this is an exact input-side measurement rather than
    an estimate. Output tokens (including thinking) cannot be known in advance —
    that is what a small live run is for.
    """
    try:
        import anthropic
    except ImportError:
        raise SystemExit("anthropic SDK not installed: pip install -e .")

    client = anthropic.Anthropic(api_key=config.api_key())
    n = len(reqs)
    print(f"  cards            : {n:,}")
    print(f"  model            : {config.MODEL}")
    print(f"  effort           : {effort}")
    print(f"  max_tokens       : {max_tokens:,}")

    counts = []
    for r in reqs[:sample]:
        p = build_params(r, effort, max_tokens)
        counted = client.messages.count_tokens(
            model=p["model"], system=p["system"], messages=p["messages"]
        )
        counts.append(counted.input_tokens)
        print(f"    {r.card_id}: {counted.input_tokens:,} input tokens")

    mean_in = sum(counts) / len(counts)
    inp, outp = PRICES.get(config.MODEL, (5.0, 25.0))

    print(f"\n  mean input/card  : {mean_in:,.0f} tokens (measured)")
    print("\n  Output tokens cannot be measured without running. Thinking is")
    print("  billed as output, so this is the number to watch:")
    print(f"\n  {'assumed out/card':>18} {'batch cost':>12} {'x72,240 cards':>15}")
    for assumed_out in (400, 800, 1500, 3000):
        per_card = (mean_in * inp + assumed_out * outp) / 1e6 * 0.5
        print(f"  {assumed_out:>18,} {'$'+format(per_card*n, ',.2f'):>12} "
              f"{'$'+format(per_card*72240, ',.0f'):>15}")
    print("\n  Run a small --submit batch and read the reported actuals before")
    print("  committing to a volume.")


# --- batch lifecycle -------------------------------------------------------

def submit(reqs: list[CardRequest], effort: str, max_tokens: int) -> str:
    import anthropic
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    client = anthropic.Anthropic(api_key=config.api_key())
    print(f"  building {len(reqs)} requests…")
    batch_reqs = [
        Request(
            custom_id=r.card_id,
            params=MessageCreateParamsNonStreaming(
                **build_params(r, effort, max_tokens)
            ),
        )
        for r in reqs
    ]
    batch = client.messages.batches.create(requests=batch_reqs)
    print(f"  batch id : {batch.id}")
    print(f"  status   : {batch.processing_status}")

    config.ensure_dirs()
    marker = config.OUT_DIR / "batches" / f"{batch.id}.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({
        "batch_id": batch.id,
        "model": config.MODEL,
        "effort": effort,
        "max_tokens": max_tokens,
        "cards": [r.card_id for r in reqs],
        "submitted": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }, indent=1), encoding="utf-8")
    print(f"  recorded : {marker}")
    print(f"\n  Collect with:  python -m bergcat.transcribe --collect {batch.id}")
    return batch.id


def collect(batch_id: str, poll_every: int = 30) -> Path:
    import anthropic

    client = anthropic.Anthropic(api_key=config.api_key())
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        c = batch.request_counts
        print(f"  {batch.processing_status}: "
              f"{c.succeeded} ok / {c.errored} errored / {c.processing} pending")
        if batch.processing_status == "ended":
            break
        time.sleep(poll_every)

    results, errors = {}, {}
    tok_in = tok_out = tok_cache_read = tok_cache_write = 0

    # Results arrive in arbitrary order — key by custom_id, never by position.
    for item in client.messages.batches.results(batch_id):
        cid = item.custom_id
        if item.result.type != "succeeded":
            errors[cid] = getattr(item.result, "error", str(item.result.type))
            continue
        msg = item.result.message
        u = msg.usage
        tok_in += u.input_tokens
        tok_out += u.output_tokens
        tok_cache_read += getattr(u, "cache_read_input_tokens", 0) or 0
        tok_cache_write += getattr(u, "cache_creation_input_tokens", 0) or 0
        text = next((b.text for b in msg.content if b.type == "text"), "")
        try:
            results[cid] = json.loads(text)
        except json.JSONDecodeError as e:
            errors[cid] = f"unparseable JSON despite schema: {e}"

    config.ensure_dirs()
    out = config.OUT_DIR / "vlm" / f"{batch_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {"batch_id": batch_id, "results": results, "errors": errors},
        indent=1, ensure_ascii=False), encoding="utf-8")

    n = max(len(results), 1)
    inp, outp = PRICES.get(config.MODEL, (5.0, 25.0))
    # Batch is 50% off; cache reads bill at ~0.1x, writes at ~1.25x.
    cost = 0.5 * (
        (tok_in * inp + tok_cache_write * inp * 1.25 + tok_cache_read * inp * 0.1
         + tok_out * outp) / 1e6
    )
    print(f"\n  transcribed : {len(results)}   errors: {len(errors)}")
    print(f"  tokens in   : {tok_in:,} uncached, {tok_cache_read:,} cache-read, "
          f"{tok_cache_write:,} cache-write")
    print(f"  tokens out  : {tok_out:,}  ({tok_out/n:,.0f}/card — includes thinking)")
    print(f"  actual cost : ${cost:.4f}  (${cost/n:.5f}/card)")
    print(f"  extrapolated: ${cost/n*72240:,.0f} for all 72,240 cards")
    print(f"\n  wrote {out}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("volume", nargs="?", choices=[*config.VOLUMES, "test"])
    ap.add_argument("--limit", type=int, help="only the first N cards")
    ap.add_argument("--effort", default="low",
                    choices=["low", "medium", "high", "xhigh", "max"],
                    help="thinking depth; billed as output tokens (default: low)")
    ap.add_argument("--max-tokens", type=int, default=4000,
                    help="caps thinking AND response together (default: 4000)")
    ap.add_argument("--dry-run", action="store_true",
                    help="measure input tokens and price the run; no spend")
    ap.add_argument("--submit", action="store_true", help="submit the batch")
    ap.add_argument("--collect", metavar="BATCH_ID",
                    help="poll an existing batch and write its results")
    args = ap.parse_args()

    if args.collect:
        collect(args.collect)
        return 0
    if not args.volume:
        ap.error("volume is required unless --collect is given")

    reqs = collect_cards(args.volume, args.limit)
    if args.submit:
        submit(reqs, args.effort, args.max_tokens)
    else:
        dry_run(reqs, args.effort, args.max_tokens)
        if not args.dry_run:
            print("\n  (dry run — add --submit to actually send this batch)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
