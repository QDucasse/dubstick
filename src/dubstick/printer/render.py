from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from dubstick.printer.protocol import mm_to_dots

FONT_CANDIDATES = {
    "regular": [
        "/usr/share/fonts/truetype/liberation/LiberationSansNarrow-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ],
    "bold": [
        "/usr/share/fonts/truetype/liberation/LiberationSansNarrow-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ],
}

MISSING = "–"
MARGIN_DOTS = 4
DEFAULT_MIN_CAP_MM = 2.0


@dataclass(frozen=True)
class Entry:
    bpm: float | None
    camelot: str | None

    @property
    def text(self) -> str:
        if self.bpm is None and not self.camelot:
            return MISSING
        bpm = str(round(self.bpm)) if self.bpm is not None else MISSING
        return f"{bpm} {self.camelot or MISSING}"


@dataclass(frozen=True)
class Row:
    side: str
    entries: tuple[Entry, ...]
    # A wrapped side's extra lines leave the side letter out but keep its indent.
    continued: bool = False


def _font_path(weight: str) -> str:
    override = os.environ.get(f"DUBSTICK_FONT_{weight.upper()}")
    if override:
        return override
    for candidate in FONT_CANDIDATES[weight]:
        if Path(candidate).exists():
            return candidate
    raise FileNotFoundError(f"no {weight} font found, set DUBSTICK_FONT_{weight.upper()}")


@lru_cache(maxsize=256)
def _font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(_font_path(weight), size)


@dataclass(frozen=True)
class _Metrics:
    size: int
    cap: int
    line_gap: int
    side_gap: int
    entry_gap: int


def _metrics(size: int) -> _Metrics:
    top, bottom = _font("bold", size).getbbox("8", anchor="ls")[1::2]
    cap = bottom - top
    return _Metrics(
        size=size,
        cap=cap,
        line_gap=max(2, round(cap * 0.35)),
        side_gap=max(3, round(size * 0.35)),
        entry_gap=max(4, round(size * 0.45)),
    )


def _row_width(row: Row, m: _Metrics) -> float:
    width = _font("bold", m.size).getlength(row.side) + m.side_gap
    regular = _font("regular", m.size)
    width += sum(regular.getlength(entry.text) for entry in row.entries)
    width += m.entry_gap * max(0, len(row.entries) - 1)
    return width


def _fits(rows: list[Row], size: int, width: int, height: int) -> bool:
    m = _metrics(size)
    block = len(rows) * m.cap + (len(rows) - 1) * m.line_gap
    if block > height - 2 * MARGIN_DOTS:
        return False
    return all(_row_width(row, m) <= width - 2 * MARGIN_DOTS for row in rows)


def _largest_size(rows: list[Row], width: int, height: int) -> int | None:
    low, high, best = 6, 200, None
    while low <= high:
        mid = (low + high) // 2
        if _fits(rows, mid, width, height):
            best, low = mid, mid + 1
        else:
            high = mid - 1
    return best


def _cap_for(size: int | None) -> int:
    return _metrics(size).cap if size else 0


def _smallest_readable_size(min_cap: int) -> int:
    size = 6
    while _metrics(size).cap < min_cap:
        size += 1
    return size


def _wrap(row: Row, width: int, min_cap: int) -> list[Row]:
    """Split one side's entries over as few rows as possible at the smallest readable size."""
    m = _metrics(_smallest_readable_size(min_cap))
    rows, current = [], []
    for entry in row.entries:
        candidate = Row(row.side, tuple(current + [entry]))
        if current and _row_width(candidate, m) > width - 2 * MARGIN_DOTS:
            rows.append(Row(row.side, tuple(current), continued=bool(rows)))
            current = [entry]
        else:
            current.append(entry)
    rows.append(Row(row.side, tuple(current), continued=bool(rows)))
    return rows


def _readable(rows: list[Row], width: int, height: int, min_cap: int) -> bool:
    return _cap_for(_largest_size(rows, width, height)) >= min_cap


def paginate(rows: list[Row], width: int, height: int, min_cap: int) -> list[list[Row]]:
    """Group rows into labels: two sides per label, spilling only when text gets too small."""
    pages: list[list[Row]] = []
    for start in range(0, len(rows), 2):
        pair = rows[start : start + 2]
        if _readable(pair, width, height, min_cap):
            pages.append(pair)
            continue

        # Too many entries for one line per side: wrap long sides, then fill
        # each label with as many rows as stay readable.
        page: list[Row] = []
        for row in (r for side in pair for r in _wrap(side, width, min_cap)):
            if page and not _readable(page + [row], width, height, min_cap):
                pages.append(page)
                page = []
            page.append(row)
        pages.append(page)
    return pages


@lru_cache(maxsize=16)
def _standard_size(width: int, height: int) -> int:
    """The size every label uses: whatever fits two sides of three tracks with the widest values."""
    widest = Row("A", (Entry(188, "12A"),) * 3)
    return _largest_size([widest, widest], width, height) or 6


def draw_page(rows: list[Row], width: int, height: int) -> Image.Image:
    """Draw one landscape label: one row per side, at the standard size unless that doesn't fit."""
    image = Image.new("1", (width, height), 1)
    if not rows:
        return image

    # Short records keep the standard size, so every sleeve in the crate reads the same.
    size = min(_largest_size(rows, width, height) or 6, _standard_size(width, height))
    m = _metrics(size)
    draw = ImageDraw.Draw(image)
    bold, regular = _font("bold", size), _font("regular", size)

    block = len(rows) * m.cap + (len(rows) - 1) * m.line_gap
    baseline = (height - block) // 2 + m.cap

    for row in rows:
        x = MARGIN_DOTS
        if not row.continued:
            draw.text((x, baseline), row.side, font=bold, fill=0, anchor="ls")
        x += bold.getlength(row.side) + m.side_gap

        for index, entry in enumerate(row.entries):
            if index:
                x += m.entry_gap
            draw.text((x, baseline), entry.text, font=regular, fill=0, anchor="ls")
            x += regular.getlength(entry.text)

        baseline += m.cap + m.line_gap
    return image


def render_labels(
    rows: list[Row],
    *,
    width_dots: int,
    length_dots: int,
    min_cap_mm: float = DEFAULT_MIN_CAP_MM,
) -> list[Image.Image]:
    """Render landscape label images (length × width) ready for preview."""
    min_cap = mm_to_dots(min_cap_mm)
    pages = paginate(rows, length_dots, width_dots, min_cap)
    return [draw_page(page, length_dots, width_dots) for page in pages]


def to_portrait(landscape: Image.Image) -> Image.Image:
    # The head prints across the narrow side; text reads along the label after a clockwise turn.
    return landscape.transpose(Image.Transpose.ROTATE_270)
