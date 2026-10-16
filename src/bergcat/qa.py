"""Stage 4: visual QA of segmentation — the one manual gate in the pipeline.

Mis-segmentation is the failure that silently poisons everything downstream, and
it is trivially visible to a human and awkward to detect automatically. So the
pipeline stops here and asks someone to look.

    python -m bergcat.qa test --overlay      # grid drawn on the page
    python -m bergcat.qa test --contact      # all 21 crops tiled per page
"""
from __future__ import annotations

import argparse

import cv2
import numpy as np
import pymupdf

from . import config
from .segment import load_page_gray, segment_page


def overlay_page(gray: np.ndarray, grid, out_path) -> None:
    """Draw the fitted grid over the page so a bad fit is obvious at a glance."""
    vis = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    for x in grid.xs:
        cv2.line(vis, (x, 0), (x, vis.shape[0]), (0, 140, 255), 2)
    for y in grid.ys:
        cv2.line(vis, (0, y), (vis.shape[1], y), (0, 140, 255), 2)
    # Number in filing order (down each column), not storage order, so the
    # overlay shows the sequence the cards are actually read in.
    by_pos = {(c.row, c.col): c for c in grid.cells}
    for i, pos in enumerate(config.catalog_order()):
        cell = by_pos.get(pos)
        if cell is None:
            continue
        cv2.rectangle(vis, (cell.x, cell.y),
                      (cell.x + cell.w, cell.y + cell.h), (0, 0, 220), 2)
        cv2.putText(vis, str(i + 1), (cell.x + 8, cell.y + 34),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (220, 0, 0), 3)
    cv2.putText(vis, f"p{grid.pdf_page} fit={grid.score:.2f} skew={grid.skew_deg:+.2f}",
                (10, vis.shape[0] - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 220), 2)
    cv2.imwrite(str(out_path), vis)


def contact_sheet(gray: np.ndarray, grid, out_path, tile_w: int = 460) -> None:
    """Tile the 21 crops in grid order. Any crop that clips a card, or holds two
    half-cards, jumps out immediately here."""
    tiles = []
    for cell in grid.cells:
        crop = gray[cell.y:cell.y + cell.h, cell.x:cell.x + cell.w]
        if crop.size == 0:
            crop = np.full((10, 10), 255, np.uint8)
        scale = tile_w / crop.shape[1]
        t = cv2.resize(crop, (tile_w, max(1, int(crop.shape[0] * scale))))
        tiles.append(cv2.cvtColor(t, cv2.COLOR_GRAY2BGR))

    tile_h = max(t.shape[0] for t in tiles)
    pad = 8
    sheet = np.full(
        ((tile_h + pad) * config.NROW + pad,
         (tile_w + pad) * config.NCOL + pad, 3), 235, np.uint8
    )
    for i, t in enumerate(tiles):
        r, c = divmod(i, config.NCOL)
        y = pad + r * (tile_h + pad)
        x = pad + c * (tile_w + pad)
        sheet[y:y + t.shape[0], x:x + tile_w] = t
        cv2.rectangle(sheet, (x - 1, y - 1), (x + tile_w, y + tile_h), (150, 150, 150), 1)
        cv2.putText(sheet, f"r{r}c{c}", (x + 6, y + tile_h - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 90, 220), 2)
    cv2.imwrite(str(out_path), sheet)


def run(volume: str, pages: range | None, overlay: bool, contact: bool) -> None:
    src = config.TEST_PDF if volume == "test" else config.VOLUMES[volume]
    doc = pymupdf.open(src)
    pages = pages or range(len(doc))
    config.ensure_dirs()
    out = config.QA_DIR / volume
    out.mkdir(parents=True, exist_ok=True)

    for pno in pages:
        gray = load_page_gray(doc, pno, config.SEGMENT_DPI)
        grid, rot = segment_page(gray)
        grid.pdf_page = pno + 1
        if overlay:
            overlay_page(rot, grid, out / f"overlay-p{grid.pdf_page:04d}.png")
        if contact:
            contact_sheet(rot, grid, out / f"contact-p{grid.pdf_page:04d}.png")
        print(f"  p{grid.pdf_page:>4} fit={grid.score:.2f}")
    doc.close()
    print(f"\nwrote QA images -> {out}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("volume", choices=[*config.VOLUMES, "test"])
    ap.add_argument("--pages")
    ap.add_argument("--overlay", action="store_true")
    ap.add_argument("--contact", action="store_true")
    args = ap.parse_args()

    from .segment import _parse_pages
    if not (args.overlay or args.contact):
        args.overlay = True
    run(args.volume, _parse_pages(args.pages), args.overlay, args.contact)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
