from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from PIL import Image

DPI = 203
HEAD_WIDTH_DOTS = 96

CMD_STATE = b"\x1b!?\r\n"
CMD_LABEL_STATUS = b"\x1b!o\r\n"
CMD_BATTERY = b"BATTERY?\r\n"
CMD_CONFIG = b"CONFIG?\r\n"

STATUS_BLOCK_LEN = 16


class PrinterState(Enum):
    READY = 0x00
    COVER_OPEN = 0x01
    NO_PAPER = 0x04
    BUSY = 0x20
    UNKNOWN = -1

    @classmethod
    def from_byte(cls, value: int) -> PrinterState:
        try:
            return cls(value)
        except ValueError:
            return cls.UNKNOWN

    @property
    def message(self) -> str:
        return {
            PrinterState.READY: "ready",
            PrinterState.COVER_OPEN: "cover is open",
            PrinterState.NO_PAPER: "out of labels",
            PrinterState.BUSY: "busy",
            PrinterState.UNKNOWN: "in an unknown state",
        }[self]


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            # Reflected polynomial 0x8005.
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


@dataclass(frozen=True)
class LabelStatus:
    state: PrinterState
    raw_state: int
    color: int
    paper_type: int
    length_mm: int
    width_mm: int

    @property
    def ready(self) -> bool:
        return self.state is PrinterState.READY

    @property
    def roll(self) -> str:
        return f"{self.width_mm} × {self.length_mm} mm"

    @classmethod
    def parse(cls, block: bytes) -> LabelStatus:
        if len(block) < STATUS_BLOCK_LEN:
            raise ValueError(f"status block too short ({len(block)} bytes)")
        block = block[:STATUS_BLOCK_LEN]

        expected = int.from_bytes(block[14:16], "big")
        if crc16_modbus(block[:14]) != expected:
            raise ValueError("status block CRC mismatch")

        return cls(
            state=PrinterState.from_byte(block[0]),
            raw_state=block[0],
            color=block[4],
            paper_type=block[7],
            # Byte 8 does not match the physical gap on real rolls, so the gap
            # comes from settings instead.
            length_mm=block[11],
            width_mm=block[13],
        )


@dataclass(frozen=True)
class Battery:
    percent: int
    charging: bool

    @classmethod
    def parse(cls, reply: bytes) -> Battery:
        prefix = b"BATTERY "
        start = reply.find(prefix)
        if start < 0 or len(reply) < start + len(prefix) + 2:
            raise ValueError(f"unexpected battery reply: {reply!r}")
        level, flag = reply[start + len(prefix) : start + len(prefix) + 2]

        # The level is BCD: 0x99 means 99 %.
        percent = (level >> 4) * 10 + (level & 0x0F)
        return cls(percent=percent, charging=bool(flag))


def mm_to_dots(mm: float) -> int:
    return round(mm * DPI / 25.4)


def label_dots(width_mm: int, length_mm: int) -> tuple[int, int]:
    """Bitmap size (width, height) in dots for a roll, as sent to the head."""
    width = min(mm_to_dots(width_mm), HEAD_WIDTH_DOTS)
    width = (width + 7) // 8 * 8

    # The official app sends 284 dots for 40 mm labels, a bit shorter than the
    # theoretical 320 so nothing runs into the gap.
    height = round(mm_to_dots(length_mm) * 284 / 320)
    return width, height


def pack_bitmap(image: Image.Image) -> bytes:
    """Pack a portrait image into TSPL BITMAP rows: MSB first, 1 = white."""
    mono = image.convert("1", dither=Image.Dither.NONE)
    if mono.width % 8:
        padded = Image.new("1", ((mono.width + 7) // 8 * 8, mono.height), 1)
        padded.paste(mono, (0, 0))
        mono = padded

    # Pillow's mode "1" already packs MSB first with 1 for white pixels.
    return mono.tobytes()


@dataclass(frozen=True)
class PrintJob:
    """A TSPL job: the setup and bitmap, then the PRINT command that triggers it."""

    body: bytes
    trigger: bytes


def build_job(
    image: Image.Image,
    *,
    width_mm: int,
    length_mm: int,
    gap_mm: float,
    density: int = 15,
    copies: int = 1,
) -> PrintJob:
    """Build a complete TSPL job for one portrait bitmap."""
    data = pack_bitmap(image)
    width_bytes = (image.width + 7) // 8

    header = (
        f"SIZE {width_mm:.1f} mm,{length_mm:.1f} mm\r\n"
        f"GAP {gap_mm:.1f} mm,0 mm\r\n"
        "DIRECTION 1,1\r\n"
        f"DENSITY {density}\r\n"
        "CLS\r\n"
        f"BITMAP 0,0,{width_bytes},{image.height},1,"
    ).encode()

    # Raw bitmap bytes follow the header directly, then a CRLF closes the command.
    # PRINT travels in a write of its own, as the official app sends it.
    return PrintJob(body=header + data + b"\r\n", trigger=f"PRINT {copies}\r\n".encode())
