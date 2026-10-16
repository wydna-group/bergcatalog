"""The card data model — the *transport* format between the vision model and TEI.

TEI P5 is the archival target (see `tei.py` and `demo/berg_demo.xml`). This
pydantic model exists because the Batch API needs a JSON schema to constrain
output against; every field here maps onto TEI:

    Run(style=underline)   -> <hi rend="underline">
    Line                   -> text + <lb/> inside <ab>
    Tracing                -> <fw type="tracing" place="top-right">
    Copy                   -> <item n="..."> in <note type="copies">
    CrossReference         -> <ref type="see|with|...">
    Continuation           -> @prev on <div type="card">
    Provenance             -> <zone> in <facsimile> + @facs
    Confidence             -> @cert on <unclear>
    ILLEGIBLE              -> <gap reason="illegible"/>

Two design rules, both driven by NOTES.md section 8 (anti-hallucination):

1. **Verbatim before parsed.** `lines` holds what is physically on the card,
   including its indentation and formatting runs. `entry` holds interpretation.
   If the two ever disagree, `lines` wins — it is what a human can check against
   the image in one glance.

2. **Uncertainty is representable.** Any string the model could not read becomes
   an `[illegible]` marker rather than a plausible guess, and `confidence`
   records how sure it was. A schema that forces a value manufactures one.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

ILLEGIBLE = "[illegible]"


class Confidence(str, Enum):
    high = "high"        # every character clearly readable
    medium = "medium"    # readable, but ink loss forced some inference
    low = "low"          # substantially degraded; treat as a candidate reading
    unreadable = "unreadable"


class Style(str, Enum):
    """Typographic treatment of a run of text. Underline is load-bearing on
    these cards — it almost always marks a `With:` link to a related item."""
    plain = "plain"
    underline = "underline"
    strikethrough = "strikethrough"
    superscript = "superscript"
    handwritten = "handwritten"


class Run(BaseModel):
    """A styled span within a line."""
    text: str
    style: Style = Style.plain


class Line(BaseModel):
    """One physical line of the card, preserving indentation and styling."""
    runs: list[Run]
    indent: int = Field(0, description="Leading whitespace in character widths")

    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs)


class Copy(BaseModel):
    """One physical copy held by the collection.

    Cards enumerate these individually, e.g.
    `Two copies: 1. Presentation copy, inscribed, from the author to
    W. T. H. Howe.  2. H.` — each numbered item is one Copy.
    """
    number: int | None = None
    description: str
    holdings_code: str | None = Field(
        None, description="Single-letter location/provenance code: H., B., Y., ..."
    )


class Tracing(BaseModel):
    """An added-entry mark from the card's top-right corner.

    e.g. `(Symons, J.)`, `m.b. (Marsh)`, `+ + + (Shaw)`, `B (Tennyson)`.
    The prefix and the parenthesized heading carry separate meaning, so keep
    them apart rather than flattening to one string.
    """
    prefix: str | None = Field(None, description="Mark before the heading: m.b., B, + + +")
    heading: str = Field(description="Parenthesized added entry")


class CrossReference(BaseModel):
    kind: Literal["see", "see_also", "with", "in_case_with"]
    target: str


class Entry(BaseModel):
    """Parsed bibliographic interpretation of the card. Best-effort — `lines`
    remains the authority."""
    main_entry: str | None = Field(None, description="Author/heading, flush left")
    title: str | None = None
    imprint: str | None = Field(None, description="Place: publisher, date")
    collation: str | None = Field(None, description="Extent, size, e.g. '2 v. 26cm.'")
    edition: str | None = None
    notes: list[str] = Field(default_factory=list)
    copies: list[Copy] = Field(default_factory=list)
    holdings_codes: list[str] = Field(default_factory=list)
    cross_references: list[CrossReference] = Field(default_factory=list)


class Provenance(BaseModel):
    """Where this card physically came from. Every record must be traceable
    back to a specific rectangle of a specific scan."""
    volume: str = Field(description="Volume key, e.g. 'v4'")
    pdf_page: int = Field(description="1-based page index in the source PDF")
    printed_page: str | None = Field(None, description="Page number printed on the leaf")
    row: int = Field(description="0-based grid row")
    col: int = Field(description="0-based grid column")
    image_path: str = Field(description="Path to the card crop, relative to project root")
    bbox: tuple[int, int, int, int] = Field(description="x, y, w, h in source page pixels")


class Continuation(BaseModel):
    """Marks a card that continues an entry begun elsewhere.

    e.g. `Winter, Wm.  Miscellaneous ms. material   (card 2)`. The preceding
    card is resolved in the linking stage, not at transcription time — the model
    sees one card and cannot know where its sibling lives.
    """
    card_number: int = Field(description="The N in '(card N)'")
    continues_card_id: str | None = None


class Card(BaseModel):
    """One catalog card: image, verbatim transcription, and interpretation."""
    card_id: str = Field(description="Stable id: {volume}-p{pdf_page:04d}-r{row}c{col}")
    provenance: Provenance

    tracings: list[Tracing] = Field(default_factory=list)
    lines: list[Line] = Field(description="Verbatim, in reading order. The authority.")
    entry: Entry = Field(default_factory=Entry)
    continuation: Continuation | None = None

    is_blank: bool = Field(False, description="Grid cell held no card")
    confidence: Confidence = Confidence.high
    transcription_notes: list[str] = Field(
        default_factory=list,
        description="Anything ambiguous — damage, obscured text, uncertain readings",
    )


class CrossCheck(BaseModel):
    """Output of the OCR audit stage for one card."""
    card_id: str
    tesseract_text: str
    embedded_text: str = Field(description="From the PDF's Google OCR layer")
    token_agreement: float = Field(description="0-1 overlap of VLM vs. OCR tokens")
    flagged_fields: list[str] = Field(default_factory=list)
    needs_review: bool = False
