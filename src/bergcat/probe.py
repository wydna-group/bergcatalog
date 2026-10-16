"""Stage 1: inspect a source PDF's structure, resolution, and encoding.

Run this against any new volume before anything else — it answers "is this the
same kind of scan we built the pipeline for?" in one pass.

    python -m bergcat.probe v4
    python -m bergcat.probe test --pages 3
"""
from __future__ import annotations

import argparse
import json
import sys

import pymupdf

from . import config


def probe_page(doc: pymupdf.Document, pno: int) -> dict:
    page = doc[pno]
    rect = page.rect
    images = []
    for info in page.get_images(full=True):
        xref = info[0]
        d = doc.extract_image(xref)
        images.append(
            {
                "xref": xref,
                "width": d["width"],
                "height": d["height"],
                "bpc": d["bpc"],
                "colorspace": d["colorspace"],
                "filter": str(doc.xref_get_key(xref, "Filter")),
                "kb": round(len(d["image"]) / 1024, 1),
                "dpi_x": round(d["width"] / (rect.width / 72)),
                "dpi_y": round(d["height"] / (rect.height / 72)),
            }
        )
    return {
        "page": pno + 1,
        "size_pt": [round(rect.width), round(rect.height)],
        "rotation": page.rotation,
        "text_chars": len(page.get_text().strip()),
        "images": images,
    }


def probe(volume: str, n_pages: int = 5) -> dict:
    path = config.TEST_PDF if volume == "test" else config.VOLUMES[volume]
    doc = pymupdf.open(path)

    # Sample from the middle — front matter is not representative of the plates.
    mid = len(doc) // 2
    sample = range(mid, min(mid + n_pages, len(doc)))

    report = {
        "volume": volume,
        "path": str(path),
        "pages": len(doc),
        "metadata": {k: v for k, v in doc.metadata.items() if v},
        "sampled": [probe_page(doc, p) for p in sample],
    }

    # The page image is the largest one; the small repeated xrefs are Google's
    # watermark and the gutter shadow strip (NOTES.md section 6).
    page_images = [
        max(p["images"], key=lambda i: i["width"] * i["height"])
        for p in report["sampled"]
        if p["images"]
    ]
    if page_images:
        report["summary"] = {
            "bpc": sorted({i["bpc"] for i in page_images}),
            "filters": sorted({i["filter"] for i in page_images}),
            "dpi_range": [
                min(i["dpi_x"] for i in page_images),
                max(i["dpi_x"] for i in page_images),
            ],
            "bitonal": all(i["bpc"] == 1 for i in page_images),
        }
    doc.close()
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("volume", choices=[*config.VOLUMES, "test"])
    ap.add_argument("--pages", type=int, default=5, help="pages to sample")
    ap.add_argument("--save", action="store_true", help="write to reports/")
    args = ap.parse_args()

    report = probe(args.volume, args.pages)
    text = json.dumps(report, indent=2)
    print(text)

    if args.save:
        config.ensure_dirs()
        out = config.REPORTS_DIR / f"probe_{args.volume}.json"
        out.write_text(text, encoding="utf-8")
        print(f"\nwrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
