from __future__ import annotations

from dataclasses import dataclass

from dubstick import camelot

# Two analyses with at most this BPM difference agree.
BPM_TOLERANCE = 1.0

# Full-length audio, best first: a local file is the exact version, a recording is the
# exact pressing, a YouTube video may be another mix. The label value comes from these.
FULL_SOURCES = ("essentia_local", "essentia_recording", "essentia_youtube")

# A 30 s preview of the same track: a second opinion, and the value of last resort.
PREVIEW_SOURCES = ("essentia_deezer",)

# Essentia's own confidence scales. Below these, a lone analysis is not trusted blindly:
# RhythmExtractor2013 rates 0–5.32 (under 1.5 is "low"), KeyExtractor's strength is 0–1.
MIN_BPM_CONFIDENCE = 1.5
MIN_KEY_STRENGTH = 0.4


@dataclass(frozen=True)
class Opinion:
    source: str
    bpm: float | None
    camelot: str | None
    bpm_confidence: float | None = None
    key_strength: float | None = None

    @property
    def weak(self) -> bool:
        low_bpm = self.bpm_confidence is not None and self.bpm_confidence < MIN_BPM_CONFIDENCE
        low_key = self.key_strength is not None and self.key_strength < MIN_KEY_STRENGTH
        return low_bpm or low_key


@dataclass(frozen=True)
class Verdict:
    bpm: float | None
    camelot: str | None
    agreement: str  # agree | disagree | single | weak | none


def fold_bpm(bpm: float | None, low: float, high: float) -> float | None:
    """Fold half/double-time readings into the expected tempo window."""
    if bpm is None or bpm <= 0:
        return None
    while bpm < low:
        bpm *= 2
    while bpm > high:
        bpm /= 2
    return bpm


def _first(opinions: dict[str, Opinion], sources: tuple[str, ...]) -> Opinion | None:
    for source in sources:
        opinion = opinions.get(source)
        if opinion and (opinion.bpm is not None or opinion.camelot):
            return opinion
    return None


def judge(opinions: list[Opinion], low: float, high: float) -> Verdict:
    """Pick the label value from the best audio, and cross-check it against the preview."""
    by_source = {o.source: o for o in opinions}
    main = _first(by_source, FULL_SOURCES)
    preview = _first(by_source, PREVIEW_SOURCES)

    if main is None and preview is None:
        return Verdict(None, None, "none")

    if main is None or preview is None:
        only = main or preview
        return Verdict(fold_bpm(only.bpm, low, high), only.camelot, "weak" if only.weak else "single")

    main_bpm, preview_bpm = fold_bpm(main.bpm, low, high), fold_bpm(preview.bpm, low, high)
    both_bpm = main_bpm is not None and preview_bpm is not None
    both_key = bool(main.camelot and preview.camelot)

    bpm_ok = not both_bpm or abs(main_bpm - preview_bpm) <= BPM_TOLERANCE
    key_ok = not both_key or camelot.adjacent_or_equal(main.camelot, preview.camelot)

    # The full track wins: the preview only checks it and fills gaps.
    bpm = main_bpm if main_bpm is not None else preview_bpm
    key = main.camelot or preview.camelot

    if not (both_bpm or both_key):
        return Verdict(bpm, key, "weak" if main.weak else "single")
    return Verdict(bpm, key, "agree" if bpm_ok and key_ok else "disagree")


# Agreement values that leave a track waiting for a human decision.
NEEDS_REVIEW = ("disagree", "weak", "none")
