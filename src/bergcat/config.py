"""Project-wide paths and constants.

Everything downstream reads its settings from here so a change of source volume
or DPI is a one-line edit rather than a grep.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv() -> None:
    """Read `.env` at the project root into the environment, if present.

    A real environment variable always wins — this only fills gaps, so an
    exported key is never silently overridden by a stale file. `.env` is
    gitignored; the key must not enter version control.
    """
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


def api_key() -> str:
    """The Anthropic API key, or a clear error saying how to provide one.

    Note this is API billing (console.anthropic.com), which is separate from a
    Claude Code / claude.ai subscription.
    """
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set.\n"
            "  PowerShell (persistent): setx ANTHROPIC_API_KEY \"sk-ant-...\"\n"
            "                           then open a new terminal\n"
            f"  or create {ROOT / '.env'} containing:\n"
            "                           ANTHROPIC_API_KEY=sk-ant-...\n"
            "Get a key at https://console.anthropic.com/settings/keys "
            "(API billing is separate from a Claude subscription)."
        )
    return key

# --- source ---------------------------------------------------------------
# The scanned volumes, discovered from disk rather than hardcoded — volumes
# arrive over time and a stale literal list silently rejects the new ones.
# Read-only; nothing in the pipeline writes here.
VOLUMES = {
    p.stem.removeprefix("berg"): p
    for p in sorted(ROOT.glob("bergv*.pdf"))
}
TEST_PDF = ROOT / "wise.pdf"  # pages 431-438 of v4, for fast iteration

# `wise.pdf` is an extract of bergv4 pp.431-438, so in the catalogue it files
# where those pages do — after v3, not before v2. Sorting on the bare source key
# puts "test" first (t < v) and lands its W entries ahead of v2's F entries,
# which breaks the alphabet across the whole site.
SOURCE_ALIASES = {"test": ("v4", 431)}


def source_position(volume: str, first_page: int = 0) -> tuple[str, int]:
    """Where a source sits in the catalogue, resolving extract aliases."""
    if volume in SOURCE_ALIASES:
        vol, page = SOURCE_ALIASES[volume]
        return vol, page + first_page
    return volume, first_page

# --- derived data ---------------------------------------------------------
DATA = ROOT / "data"
PAGES_DIR = DATA / "pages"
CARDS_DIR = DATA / "cards"
QA_DIR = DATA / "qa"
OUT_DIR = DATA / "out"
REPORTS_DIR = ROOT / "reports"

# --- the card grid --------------------------------------------------------
NROW, NCOL = 7, 3
CARDS_PER_PAGE = NROW * NCOL


def catalog_order(nrow: int = NROW, ncol: int = NCOL) -> list[tuple[int, int]]:
    """Grid cells in the drawer's filing sequence: down each column, then across.

    Determined by measurement, not assumption — see NOTES.md s.13. Reading the
    grid row-major scrambles the sequence (`Winship, Winter, Winters, Winslow,
    Winter, Wintle, Wingate...`); column-major recovers it (`Winship, Winslow,
    Winslow, Winter, Winter, Winter garden, winter owl, Winterich, Winters,
    Wintle, Wirt, Wisdom, Wise x3`).

    This order is the catalogue's own and must be PRESERVED, never recomputed:
    library filing rules collate `Wise, T. J.` and `Wise, Thomas James` together
    and sequence them by subordinate heading, which no string sort reproduces.
    """
    return [(r, c) for c in range(ncol) for r in range(nrow)]

# Card crops are padded outward from the fitted grid line. Cards carry wide
# margins, so over-cropping is nearly free while under-cropping loses text.
# Raised from 0.02 after bergv2 p103 (skew -1.91 deg) showed heading lines
# grazing the row boundary: a clipped heading is unrecoverable, a little
# neighbouring ink costs a handful of image tokens.
CARD_PAD_FRAC = 0.04

# --- resolution -----------------------------------------------------------
# The scans are ~580 DPI natively. Segmentation runs on a downscaled copy
# (fast, and the ruling lines survive); crops are taken at native resolution.
SOURCE_DPI = 580
SEGMENT_DPI = 150

# Long-edge ceiling for images sent to the API. Opus 5 / Sonnet 5 accept up to
# 2576 px on the long edge; beyond that the image is downscaled server-side
# anyway, so there is nothing to gain by sending more.
VLM_MAX_LONG_EDGE = 2576

# --- transcription --------------------------------------------------------
# Accuracy is the binding constraint, not cost — see NOTES.md section 7.
MODEL = "claude-opus-5"

# Fields where an OCR/VLM disagreement is worth a human's time. Everything else
# can differ without meaning much, because OCR is unreliable on this scan.
HIGH_RISK_FIELDS = ("names", "dates", "numbers", "copy_count", "holdings")


def ensure_dirs() -> None:
    """Create every derived-data directory. Safe to call repeatedly."""
    for d in (PAGES_DIR, CARDS_DIR, QA_DIR, OUT_DIR, REPORTS_DIR):
        d.mkdir(parents=True, exist_ok=True)
