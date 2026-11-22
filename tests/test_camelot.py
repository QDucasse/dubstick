import pytest

from dubstick import camelot

# The full wheel, both modes.
WHEEL = {
    1: ("Abm", "B"), 2: ("Ebm", "F#"), 3: ("Bbm", "Db"), 4: ("Fm", "Ab"),
    5: ("Cm", "Eb"), 6: ("Gm", "Bb"), 7: ("Dm", "F"), 8: ("Am", "C"),
    9: ("Em", "G"), 10: ("Bm", "D"), 11: ("F#m", "A"), 12: ("Dbm", "E"),
}


@pytest.mark.parametrize("number", WHEEL)
def test_wheel(number):
    minor, major = WHEEL[number]
    assert camelot.parse(minor) == f"{number}A"
    assert camelot.parse(major) == f"{number}B"


@pytest.mark.parametrize("text, code", [
    ("8a", "8A"), (" 12B ", "12B"), ("G#m", "1A"), ("C♯ minor", "12A"),
    ("E♭", "5B"), ("A major", "11B"), ("Bb min", "3A"),
])
def test_spellings(text, code):
    assert camelot.parse(text) == code


@pytest.mark.parametrize("text", [None, "", "13A", "H", "8C"])
def test_invalid(text):
    assert camelot.parse(text) is None


def test_from_essentia_output():
    assert camelot.from_key("F#", "minor") == "11A"
    assert camelot.from_key("Bb", "major") == "6B"


def test_compatible_wraps():
    assert camelot.compatible("12A") == ["12A", "1A", "11A", "12B"]
    assert camelot.compatible("1B") == ["1B", "2B", "12B", "1A"]
    assert camelot.adjacent_or_equal("8A", "9A")
    assert not camelot.adjacent_or_equal("8A", "9B")
