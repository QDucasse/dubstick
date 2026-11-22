from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _paths(value: str) -> list[Path]:
    return [Path(part) for part in value.split(os.pathsep) if part]


@dataclass(frozen=True)
class Config:
    data_dir: Path
    discogs_token: str | None
    music_roots: list[Path] = field(default_factory=list)
    essentia_workers: int = 2

    @property
    def db_path(self) -> Path:
        return self.data_dir / "dubstick.db"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @classmethod
    def from_env(cls) -> Config:
        data_dir = Path(os.environ.get("DUBSTICK_DATA", "./data")).resolve()
        return cls(
            data_dir=data_dir,
            discogs_token=os.environ.get("DISCOGS_TOKEN") or None,
            # Colon-separated, like PATH.
            music_roots=_paths(os.environ.get("DUBSTICK_MUSIC", "")),
            essentia_workers=int(os.environ.get("DUBSTICK_ESSENTIA_WORKERS", "2")),
        )

    def prepare(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
