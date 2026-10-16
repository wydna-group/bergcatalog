"""Backfill `src_width`/`src_height` into grid metadata written before they existed.

Facsimile zones are quoted in source pixels. Without the full-resolution page
size, `tei.py` falls back to the segmentation-resolution size and the zones come
out ~3.9x too small — wrong in the archival record even though the displayed
image is unaffected.

The scale is recovered exactly from a card crop: a crop's pixel width divided by
its cell's width at segmentation resolution IS the scale factor. No PDF needed.

    python -m bergcat.migrate_grids
"""
from __future__ import annotations

import json

import cv2

from . import config


def migrate(volume: str) -> str:
    meta = config.CARDS_DIR / f"{volume}-grids.json"
    if not meta.exists():
        return f"  {volume}: no metadata, skipped"

    grids = json.loads(meta.read_text(encoding="utf-8"))
    card_dir = config.CARDS_DIR / volume
    patched = already = failed = 0

    for g in grids:
        if g.get("src_width"):
            already += 1
            continue
        scale = None
        for cell in g["cells"]:
            p = card_dir / f"{volume}-p{g['pdf_page']:04d}-r{cell['row']}c{cell['col']}.png"
            if not p.exists():
                continue
            img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
            if img is None or not cell["w"]:
                continue
            scale = img.shape[1] / cell["w"]
            break
        if scale is None:
            failed += 1
            continue
        g["src_width"] = int(round(g["width"] * scale))
        g["src_height"] = int(round(g["height"] * scale))
        patched += 1

    meta.write_text(json.dumps(grids, indent=1), encoding="utf-8")
    return (f"  {volume}: {patched} patched, {already} already had it, "
            f"{failed} unresolved")


def main() -> int:
    for volume in [*config.VOLUMES, "test"]:
        print(migrate(volume))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
