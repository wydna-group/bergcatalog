"""Stages 2-3: page extraction, deskew, and card-grid segmentation.

The grid is known to be exactly 7 x 3 and near-regular, so we *fit a model*
rather than detect cells. Contour-finding gets 11-15 of 21 cards because faint
or broken rules break individual cells (NOTES.md s.5); a fitted grid only needs
enough rules to agree on an offset and a pitch, so missing ones cost nothing.

    python -m bergcat.segment test --qa
    python -m bergcat.segment v4 --pages 100-120
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, asdict
from pathlib import Path

import cv2
import numpy as np
import pymupdf

from . import config


@dataclass
class Cell:
    row: int
    col: int
    x: int
    y: int
    w: int
    h: int

    @property
    def card_ref(self) -> str:
        return f"r{self.row}c{self.col}"


@dataclass
class PageGrid:
    pdf_page: int          # 1-based
    printed_page: str | None
    skew_deg: float
    width: int             # at SEGMENT_DPI, the resolution the grid was fitted at
    height: int
    xs: list[int]
    ys: list[int]
    score: float           # grid-fit confidence, 0-1
    cells: list[Cell]
    # Full-resolution page size, recorded so later stages can place facsimile
    # zones in source pixels without re-opening the PDF.
    src_width: int = 0
    src_height: int = 0


# --- page loading ---------------------------------------------------------

def load_page_gray(doc: pymupdf.Document, pno: int, dpi: int) -> np.ndarray:
    pix = doc[pno].get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width).copy()


def deskew(gray: np.ndarray) -> tuple[np.ndarray, float]:
    """Estimate rotation from the long horizontal card rules and correct it.

    Pages measure -0.5 to +1.0 degrees. Small, but at 5600 px wide a degree is
    ~100 px of vertical drift across the page — enough to shear a row boundary
    through a card.
    """
    ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    h, w = ink.shape
    horiz = cv2.morphologyEx(
        ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (w // 8, 1))
    )
    lines = cv2.HoughLinesP(horiz, 1, np.pi / 720, threshold=200,
                            minLineLength=w // 4, maxLineGap=40)
    if lines is None:
        return gray, 0.0
    lines = np.asarray(lines).reshape(-1, 4)  # cv2 4.x -> (N,1,4); 5.x -> (N,4)
    angles = [
        np.degrees(np.arctan2(y2 - y1, x2 - x1))
        for x1, y1, x2, y2 in lines
        if x2 != x1 and abs(np.degrees(np.arctan2(y2 - y1, x2 - x1))) < 5
    ]
    if not angles:
        return gray, 0.0
    ang = float(np.median(angles))
    m = cv2.getRotationMatrix2D((w / 2, h / 2), ang, 1.0)
    rot = cv2.warpAffine(gray, m, (w, h), flags=cv2.INTER_LINEAR,
                         borderMode=cv2.BORDER_CONSTANT, borderValue=255)
    return rot, ang


# --- grid fitting ---------------------------------------------------------

def _line_profile(gray: np.ndarray, axis: int) -> np.ndarray:
    """Ink energy of long straight rules, projected onto one axis.

    axis=0 -> vertical rules collapsed to a per-column profile
    axis=1 -> horizontal rules collapsed to a per-row profile
    """
    ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    h, w = ink.shape
    if axis == 0:
        k = cv2.getStructuringElement(cv2.MORPH_RECT, (1, h // 12))
        mask = cv2.morphologyEx(ink, cv2.MORPH_OPEN, k)
        return (mask > 0).sum(axis=0).astype(np.float64)
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (w // 6, 1))
    mask = cv2.morphologyEx(ink, cv2.MORPH_OPEN, k)
    return (mask > 0).sum(axis=1).astype(np.float64)


def fit_regular_lines(
    profile: np.ndarray, n_lines: int,
    min_span_frac: float = 0.60, max_span_frac: float = 0.99,
    tol: int = 6,
) -> tuple[list[int], float]:
    """Find n_lines equally-spaced peaks maximizing total profile energy.

    Brute-force over (offset, pitch). For each candidate the score sums the
    profile in a small window around every predicted line, so a grid scores well
    when *most* of its rules are present — a missing or broken rule just
    contributes zero instead of derailing the fit.

    The search is bounded by how much of the page the grid must *span*, not by
    pitch directly. Span is the physically meaningful constraint (the card grid
    fills the plate), and it rules out the degenerate fit this originally hit:
    a compressed grid that stacks every line onto the densest band of the page
    and ignores the rest.

    Returns the fitted line positions and a 0-1 confidence.
    """
    n = len(profile)
    min_pitch = n * min_span_frac / (n_lines - 1)
    max_pitch = n * max_span_frac / (n_lines - 1)
    # Smooth so a rule one pixel off still lands in the window.
    smooth = cv2.GaussianBlur(profile.reshape(-1, 1), (1, 9), 0).ravel()
    # Integral image makes each window sum O(1).
    cum = np.concatenate([[0.0], np.cumsum(smooth)])

    def window(c: int) -> float:
        a, b = max(0, c - tol), min(n, c + tol + 1)
        return cum[b] - cum[a]

    best_score, best = -1.0, None
    for pitch in np.arange(min_pitch, max_pitch, 0.5):
        span = pitch * (n_lines - 1)
        if span >= n:
            break
        for off in range(0, int(n - span)):
            centers = [int(round(off + i * pitch)) for i in range(n_lines)]
            s = sum(window(c) for c in centers)
            if s > best_score:
                best_score, best = s, centers

    if best is None:
        step = n / (n_lines - 1)
        return [int(i * step) for i in range(n_lines)], 0.0

    # Snap each fitted line onto the strongest nearby actual rule.
    snapped = []
    for c in best:
        a, b = max(0, c - tol), min(n, c + tol + 1)
        seg = smooth[a:b]
        snapped.append(int(a + np.argmax(seg)) if seg.size and seg.max() > 0 else c)

    # Confidence: fraction of fitted lines sitting on real ink.
    thresh = max(smooth.max() * 0.25, 1.0)
    hits = sum(1 for c in snapped if smooth[c] >= thresh)
    return sorted(snapped), hits / n_lines


def fit_grid(gray: np.ndarray) -> tuple[list[int], list[int], float]:
    col_prof = _line_profile(gray, axis=0)
    row_prof = _line_profile(gray, axis=1)

    # Columns fill nearly the full width; rows leave room for the running head
    # at the top and the "Digitized by Google" footer at the bottom.
    xs, cs = fit_regular_lines(col_prof, config.NCOL + 1, 0.70, 0.99)
    ys, rs = fit_regular_lines(row_prof, config.NROW + 1, 0.62, 0.95)
    return xs, ys, round((cs + rs) / 2, 3)


def segment_page(gray: np.ndarray) -> tuple[PageGrid, np.ndarray]:
    rot, skew = deskew(gray)
    xs, ys, score = fit_grid(rot)
    h, w = rot.shape

    pad_x = int((xs[-1] - xs[0]) / config.NCOL * config.CARD_PAD_FRAC)
    pad_y = int((ys[-1] - ys[0]) / config.NROW * config.CARD_PAD_FRAC)

    cells = []
    for r in range(config.NROW):
        for c in range(config.NCOL):
            x0 = max(0, xs[c] - pad_x)
            y0 = max(0, ys[r] - pad_y)
            x1 = min(w, xs[c + 1] + pad_x)
            y1 = min(h, ys[r + 1] + pad_y)
            cells.append(Cell(r, c, x0, y0, x1 - x0, y1 - y0))

    grid = PageGrid(0, None, round(skew, 3), w, h, xs, ys, score, cells)
    return grid, rot


# --- driver ---------------------------------------------------------------

def is_blank(card: np.ndarray, ink_floor: float = 0.004) -> bool:
    """A grid cell with almost no ink held no card (or an empty one)."""
    ink = cv2.threshold(card, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    return float((ink > 0).mean()) < ink_floor


def run(volume: str, pages: range | None = None, write_cards: bool = True) -> list[PageGrid]:
    src = config.TEST_PDF if volume == "test" else config.VOLUMES[volume]
    doc = pymupdf.open(src)
    pages = pages or range(len(doc))
    config.ensure_dirs()

    out_root = config.CARDS_DIR / volume
    out_root.mkdir(parents=True, exist_ok=True)

    grids: list[PageGrid] = []
    for pno in pages:
        lo_gray = load_page_gray(doc, pno, config.SEGMENT_DPI)
        grid, _ = segment_page(lo_gray)
        grid.pdf_page = pno + 1

        if write_cards:
            # Re-render at full resolution once, then slice. Scaling the fitted
            # low-res grid up is far cheaper than fitting at 580 DPI.
            hi_gray = load_page_gray(doc, pno, config.SOURCE_DPI)
            hs, ws = hi_gray.shape
            hi_rot, _ = deskew(hi_gray)
            sx, sy = ws / grid.width, hs / grid.height
            grid.src_width, grid.src_height = ws, hs

            for cell in grid.cells:
                x0, y0 = int(cell.x * sx), int(cell.y * sy)
                x1, y1 = int((cell.x + cell.w) * sx), int((cell.y + cell.h) * sy)
                crop = hi_rot[y0:y1, x0:x1]
                if crop.size == 0:
                    continue
                name = f"{volume}-p{grid.pdf_page:04d}-{cell.card_ref}.png"
                cv2.imwrite(str(out_root / name), crop)

        grids.append(grid)
        print(f"  p{grid.pdf_page:>4}  skew={grid.skew_deg:+.2f}  fit={grid.score:.2f}")

    doc.close()

    # Merge rather than overwrite: page ranges are segmented incrementally
    # (`--pages 703-715`, then `--pages 537-538`), and a plain write would
    # discard everything cut in an earlier run.
    meta = config.CARDS_DIR / f"{volume}-grids.json"
    merged: dict[int, dict] = {}
    if meta.exists():
        for g in json.loads(meta.read_text(encoding="utf-8")):
            merged[g["pdf_page"]] = g
    for g in grids:
        merged[g.pdf_page] = asdict(g)
    meta.write_text(
        json.dumps([merged[k] for k in sorted(merged)], indent=1), encoding="utf-8"
    )
    if write_cards:
        print(f"\nwrote {len(grids)*config.CARDS_PER_PAGE} cards -> {out_root}")
    else:
        print(f"\nfitted {len(grids)} grids; no cards written (--no-cards)")
    print(f"wrote grid metadata -> {meta}")
    return grids


def _parse_pages(spec: str | None) -> range | None:
    if not spec:
        return None
    if "-" in spec:
        a, b = spec.split("-")
        return range(int(a) - 1, int(b))
    return range(int(spec) - 1, int(spec))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("volume", choices=[*config.VOLUMES, "test"])
    ap.add_argument("--pages", help="1-based, e.g. '5' or '100-120'")
    ap.add_argument("--no-cards", action="store_true", help="fit only, write nothing")
    args = ap.parse_args()

    run(args.volume, _parse_pages(args.pages), write_cards=not args.no_cards)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
