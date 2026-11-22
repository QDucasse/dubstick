from __future__ import annotations

import re

# Pitch classes, with every spelling an API or analyser might use.
_PITCH = {
    "C": 0, "B#": 0,
    "C#": 1, "DB": 1,
    "D": 2,
    "D#": 3, "EB": 3,
    "E": 4, "FB": 4,
    "F": 5, "E#": 5,
    "F#": 6, "GB": 6,
    "G": 7,
    "G#": 8, "AB": 8,
    "A": 9,
    "A#": 10, "BB": 10,
    "B": 11, "CB": 11,
}

# Camelot number for each major tonic (B side); the relative minor shares the number.
_MAJOR_NUMBER = {11: 1, 6: 2, 1: 3, 8: 4, 3: 5, 10: 6, 5: 7, 0: 8, 7: 9, 2: 10, 9: 11, 4: 12}

_CAMELOT_RE = re.compile(r"^\s*(1[0-2]|[1-9])\s*([AB])\s*$", re.IGNORECASE)
_KEY_RE = re.compile(
    r"^\s*([A-G])\s*([#♯b♭]?)\s*(m|min|minor|maj|major)?\s*$",
    re.IGNORECASE,
)


def from_key(tonic: str, scale: str) -> str | None:
    """Camelot code for a tonic and scale, e.g. ("A", "minor") -> "8A"."""
    # Upper-casing turns a flat "b" into "B", which _PITCH spells that way.
    note = tonic.strip().upper().replace("♯", "#").replace("♭", "B")
    pitch = _PITCH.get(note)
    if pitch is None:
        return None

    minor = scale.strip().lower() in ("minor", "min", "m")
    # A minor key's relative major sits three semitones up.
    major_pitch = (pitch + 3) % 12 if minor else pitch
    return f"{_MAJOR_NUMBER[major_pitch]}{'A' if minor else 'B'}"


def parse(text: str | None) -> str | None:
    """Normalise a Camelot code or a key name ("F#m", "Ab", "8a") to Camelot."""
    if not text:
        return None
    match = _CAMELOT_RE.match(text)
    if match:
        return f"{int(match.group(1))}{match.group(2).upper()}"

    match = _KEY_RE.match(text)
    if not match:
        return None
    letter, accidental, quality = match.groups()
    tonic = letter.upper() + {"♯": "#", "#": "#", "b": "B", "♭": "B"}.get(accidental, "")

    # A lone lowercase "m" means minor; "maj"/"major" or nothing means major.
    minor = quality is not None and quality.lower() in ("m", "min", "minor")
    return from_key(tonic, "minor" if minor else "major")


def split(code: str) -> tuple[int, str]:
    return int(code[:-1]), code[-1]


def compatible(code: str) -> list[str]:
    """Keys that mix harmonically with this one: itself, ±1 on the wheel, relative major/minor."""
    number, letter = split(code)
    other = "B" if letter == "A" else "A"
    up = number % 12 + 1
    down = (number - 2) % 12 + 1
    return [code, f"{up}{letter}", f"{down}{letter}", f"{number}{other}"]


def adjacent_or_equal(first: str, second: str) -> bool:
    return second in compatible(first)


def sort_key(code: str) -> tuple[int, str]:
    return split(code)
