from __future__ import annotations

from dataclasses import dataclass, fields

from dubstick.db import Database


@dataclass
class Settings:
    printer_address: str = ""
    density: int = 15
    gap_mm: float = 5.0
    min_cap_mm: float = 2.0
    bpm_min: float = 90.0
    bpm_max: float = 180.0
    # Last roll the printer reported, so previews match without a BLE round trip.
    roll_width_mm: int = 14
    roll_length_mm: int = 40

    @classmethod
    def load(cls, db: Database) -> Settings:
        stored = {row["key"]: row["value"] for row in db.query("SELECT key, value FROM setting")}
        settings = cls()
        for f in fields(cls):
            if f.name in stored:
                # Values are stored as text; cast back using the default's type.
                setattr(settings, f.name, type(getattr(settings, f.name))(stored[f.name]))
        return settings

    def save(self, db: Database) -> None:
        with db.connect() as conn:
            conn.executemany(
                "INSERT INTO setting (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                [(f.name, str(getattr(self, f.name))) for f in fields(self)],
            )

    def update(self, **values: str) -> list[str]:
        """Apply form values; returns error messages for the ones that don't parse."""
        errors = []
        for f in fields(self):
            if f.name not in values:
                continue
            try:
                setattr(self, f.name, type(getattr(self, f.name))(values[f.name].strip()))
            except ValueError:
                errors.append(f"invalid value for {f.name}: {values[f.name]!r}")

        if not 1 <= self.density <= 15:
            errors.append("density must be between 1 and 15")
        if self.bpm_max < self.bpm_min * 2 - 1:
            # The window must span an octave, or some tempos can't be folded into it.
            errors.append("BPM range must span at least a factor of two (e.g. 90–180)")
        return errors
