"""OCR a card and recover its *structure*, not just its text.

Plain OCR text loses what makes a catalog card readable: which block is the
tracing in the corner, which lines are indented under the main entry, which
single letter at the bottom is a holdings code. Tesseract's TSV output carries
per-word boxes, so that structure is recoverable from geometry even when the
characters themselves are unreliable.

Accuracy here is ~75-82% (NOTES.md s.2). That is fine for this role: OCR seeds
the TEI so a human has something to correct rather than a blank field.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import cv2
import numpy as np
import pytesseract

# Dilating a bitonal scan thins the ink and re-opens the filled letter counters
# that the 1-bit threshold closed. Measured best variant: +6.8 pts over raw.
_THIN_KERNEL = np.ones((2, 2), np.uint8)

HOLDINGS_RE = re.compile(r"^\s*([A-Z])\.\s*$")
CARD_N_RE = re.compile(r"\(\s*card\s+(\d+)\s*\)", re.I)
SEE_RE = re.compile(r"\bSee[:s]\s*(.+)$", re.I)
WITH_RE = re.compile(r"\bWith\s+(?:his[:s]?|her[:s]?)?\s*[:s]?\s*(.+)$", re.I)


@dataclass
class OcrLine:
    text: str
    left: int
    top: int
    width: int
    height: int
    conf: float

    @property
    def right(self) -> int:
        return self.left + self.width


@dataclass
class CardOcr:
    lines: list[OcrLine] = field(default_factory=list)
    tracing: list[OcrLine] = field(default_factory=list)
    main_entry: OcrLine | None = None
    body: list[OcrLine] = field(default_factory=list)
    notes: list[OcrLine] = field(default_factory=list)
    holdings: list[str] = field(default_factory=list)
    card_number: int | None = None
    see_target: str | None = None
    mean_conf: float = 0.0
    is_blank: bool = False
    img_w: int = 0
    img_h: int = 0

    @property
    def raw_text(self) -> str:
        return "\n".join(l.text for l in self.lines)


def preprocess(gray: np.ndarray) -> np.ndarray:
    return cv2.dilate(gray, _THIN_KERNEL, iterations=1)


def _lines_from_tsv(data: dict, min_conf: float = 25.0) -> list[OcrLine]:
    """Group tesseract words into lines by VERTICAL OVERLAP.

    Not by tesseract's own block/par/line numbering: on this material its block
    segmentation fragments a card into pseudo-columns, and grouping by those
    keys emits the lines out of reading order — measurably scrambling the card
    (a mid-sentence wrap could sort ahead of the line it continues). Word boxes
    are reliable even when the characters are not, so cluster on geometry and
    read strictly top-to-bottom, left-to-right.
    """
    words = []
    for i, txt in enumerate(data["text"]):
        if not txt.strip():
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            continue
        if conf < min_conf:
            continue
        words.append(
            {
                "text": txt.strip(),
                "left": data["left"][i],
                "top": data["top"][i],
                "right": data["left"][i] + data["width"][i],
                "bottom": data["top"][i] + data["height"][i],
                "mid": data["top"][i] + data["height"][i] / 2,
                "h": data["height"][i],
                "conf": conf,
            }
        )
    if not words:
        return []

    words.sort(key=lambda w: w["mid"])
    median_h = sorted(w["h"] for w in words)[len(words) // 2] or 1
    band = median_h * 0.6  # two words share a line if their centres are close

    rows: list[list[dict]] = [[words[0]]]
    for w in words[1:]:
        anchor = sum(x["mid"] for x in rows[-1]) / len(rows[-1])
        if abs(w["mid"] - anchor) <= band:
            rows[-1].append(w)
        else:
            rows.append([w])

    lines: list[OcrLine] = []
    for row in rows:
        row.sort(key=lambda w: w["left"])
        lines.append(
            OcrLine(
                text=" ".join(w["text"] for w in row),
                left=min(w["left"] for w in row),
                top=min(w["top"] for w in row),
                width=max(w["right"] for w in row) - min(w["left"] for w in row),
                height=max(w["bottom"] for w in row) - min(w["top"] for w in row),
                conf=sum(w["conf"] for w in row) / len(row),
            )
        )
    lines.sort(key=lambda l: l.top)
    return lines


def classify(lines: list[OcrLine], img_w: int, img_h: int) -> CardOcr:
    """Assign each OCR line a structural role from its geometry.

    The card layout is highly conventional, so position is a stronger signal
    than content: the tracing sits top-right, the main entry is the first line
    flush to the left margin, and the body is indented beneath it.
    """
    card = CardOcr(lines=lines, img_w=img_w, img_h=img_h)
    if not lines:
        card.is_blank = True
        return card

    card.mean_conf = sum(l.conf for l in lines) / len(lines)

    # Tracing: right-aligned, in the top quarter, and short.
    body_pool: list[OcrLine] = []
    for l in lines:
        right_aligned = l.left > img_w * 0.55
        near_top = l.top < img_h * 0.30
        if right_aligned and near_top and len(l.text) < 40:
            card.tracing.append(l)
        else:
            body_pool.append(l)

    if not body_pool:
        card.is_blank = True
        return card

    # Main entry: first line at the left margin.
    left_margin = min(l.left for l in body_pool)
    indent_threshold = left_margin + img_w * 0.04
    for l in body_pool:
        if l.left <= indent_threshold:
            card.main_entry = l
            break
    if card.main_entry is None:
        card.main_entry = body_pool[0]

    rest = [l for l in body_pool if l is not card.main_entry]

    # Holdings codes are a lone capital + period, usually low on the card.
    for l in list(rest):
        if HOLDINGS_RE.match(l.text):
            card.holdings.append(HOLDINGS_RE.match(l.text).group(1))
            rest.remove(l)

    # Split the remainder into blocks by VERTICAL GAP, never by indentation.
    # Indentation splits reading order: a wrapped line returning to the left
    # margin is a continuation of the sentence above it, not a later block, and
    # grouping by indent files it after every indented line — which silently
    # scrambles the card. A blank line is what actually separates blocks here.
    if rest:
        pitches = [
            b.top - a.top for a, b in zip(rest, rest[1:]) if b.top > a.top
        ]
        median_pitch = sorted(pitches)[len(pitches) // 2] if pitches else 0
        gap_limit = median_pitch * 1.75 if median_pitch else float("inf")

        blocks: list[list[OcrLine]] = [[rest[0]]]
        for prev, cur in zip(rest, rest[1:]):
            if cur.top - prev.top > gap_limit:
                blocks.append([cur])
            else:
                blocks[-1].append(cur)

        card.body = blocks[0]
        for b in blocks[1:]:
            card.notes.extend(b)

    joined = " ".join(l.text for l in lines)
    if m := CARD_N_RE.search(joined):
        card.card_number = int(m.group(1))
    if m := SEE_RE.search(joined):
        card.see_target = m.group(1).strip(" .")

    return card


def ocr_card(gray: np.ndarray, psm: int = 3) -> CardOcr:
    """OCR one card crop.

    psm 3 (full automatic page segmentation) measured best for *reading order*
    on this material; psm 4 recovers marginally more characters but fragments
    the card into pseudo-columns, which costs more than it gains.
    """
    img = preprocess(gray)
    data = pytesseract.image_to_data(
        img, lang="eng", config=f"--psm {psm}",
        output_type=pytesseract.Output.DICT,
    )
    lines = _lines_from_tsv(data)
    return classify(lines, img.shape[1], img.shape[0])
