from dubstick.printer import render
from dubstick.printer.render import Entry, Row

WIDTH, LENGTH = 96, 284


def rows_with(counts: dict[str, int]) -> list[Row]:
    return [Row(side, tuple(Entry(120 + i, f"{i % 12 + 1}A") for i in range(n))) for side, n in counts.items()]


def test_entry_text():
    assert Entry(139.6, "8A").text == "140 8A"
    assert Entry(None, "8A").text == "– 8A"
    assert Entry(140, None).text == "140 –"
    assert Entry(None, None).text == "–"


def test_two_short_sides_fit_one_label():
    pages = render.render_labels(rows_with({"A": 3, "B": 2}), width_dots=WIDTH, length_dots=LENGTH)
    assert len(pages) == 1
    assert pages[0].size == (LENGTH, WIDTH)


def test_four_sides_use_two_labels():
    pages = render.render_labels(rows_with({"A": 2, "B": 2, "C": 2, "D": 2}), width_dots=WIDTH, length_dots=LENGTH)
    assert len(pages) == 2


def test_long_side_spills_but_keeps_every_entry():
    rows = rows_with({"A": 12, "B": 5})
    pages = render.paginate(rows, LENGTH, WIDTH, min_cap=render.mm_to_dots(2.5))
    assert len(pages) > 1
    printed = [entry for page in pages for row in page for entry in row.entries]
    assert printed == [entry for row in rows for entry in row.entries]


def test_portrait_rotation():
    page = render.render_labels(rows_with({"A": 1}), width_dots=WIDTH, length_dots=LENGTH)[0]
    assert render.to_portrait(page).size == (WIDTH, LENGTH)


def test_something_is_drawn():
    page = render.render_labels(rows_with({"A": 1}), width_dots=WIDTH, length_dots=LENGTH)[0]
    assert page.getextrema()[0] == 0


def ink_height(page):
    # Height of the drawn text block, in dots.
    left, top, right, bottom = page.point(lambda v: 255 - v).getbbox()
    return bottom - top


def test_short_records_use_the_standard_size():
    sparse = rows_with({"A": 1, "B": 1})
    widest = [Row(side, (Entry(188, "12A"),) * 3) for side in "AB"]
    sparse_page = render.render_labels(sparse, width_dots=WIDTH, length_dots=LENGTH)[0]
    widest_page = render.render_labels(widest, width_dots=WIDTH, length_dots=LENGTH)[0]
    assert ink_height(sparse_page.convert("L")) == ink_height(widest_page.convert("L"))


def test_continuation_rows_skip_the_side_letter():
    rows = rows_with({"A": 12})
    wrapped = [row for page in render.paginate(rows, LENGTH, WIDTH, min_cap=render.mm_to_dots(2.0)) for row in page]
    assert len(wrapped) > 1
    assert [row.continued for row in wrapped] == [False] + [True] * (len(wrapped) - 1)
