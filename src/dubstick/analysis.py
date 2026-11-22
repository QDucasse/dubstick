from __future__ import annotations

import subprocess
from dataclasses import asdict, dataclass

import numpy as np

SAMPLE_RATE = 44100

# Long local files are trimmed to their middle part: intros and outros of
# DJ tools are often beatless and pull the key estimate towards silence.
MAX_SECONDS = 240


@dataclass(frozen=True)
class AudioAnalysis:
    bpm: float
    bpm_confidence: float
    key: str
    scale: str
    key_strength: float

    def as_dict(self) -> dict:
        return asdict(self)


def decode(path: str) -> np.ndarray:
    """Mono float samples at SAMPLE_RATE, decoded by ffmpeg.

    Essentia's own loader lacks codecs such as Opus (YouTube audio), so ffmpeg does all decoding.
    """
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-i", path, "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg could not decode {path}: {result.stderr.decode(errors='replace').strip()}")
    return np.frombuffer(result.stdout, dtype=np.float32)


def analyse_file(path: str) -> AudioAnalysis:
    """BPM and key of an audio file. Runs in a worker process: Essentia is CPU bound."""
    # Imported here so the web process never pays for loading Essentia.
    import essentia
    import essentia.standard as es

    essentia.log.infoActive = False
    essentia.log.warningActive = False

    audio = decode(path)
    if len(audio) < SAMPLE_RATE * 5:
        raise RuntimeError(f"{path}: less than 5 seconds of audio")
    limit = MAX_SECONDS * SAMPLE_RATE
    if len(audio) > limit:
        start = (len(audio) - limit) // 2
        audio = audio[start : start + limit]

    bpm, _beats, confidence, _estimates, _intervals = es.RhythmExtractor2013(method="multifeature")(audio)
    # The EDMA profile is trained on electronic dance music, which fits a record crate.
    key, scale, strength = es.KeyExtractor(profileType="edma", sampleRate=SAMPLE_RATE)(audio)

    return AudioAnalysis(
        bpm=float(bpm),
        bpm_confidence=float(confidence),
        key=key,
        scale=scale,
        key_strength=float(strength),
    )
