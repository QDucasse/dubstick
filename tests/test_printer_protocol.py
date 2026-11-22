import pytest
from PIL import Image

from dubstick.printer import protocol
from dubstick.printer.protocol import Battery, LabelStatus, PrinterState

# Captured from a real P21 with a white 14 × 40 mm gapped roll.
REAL_STATUS = bytes.fromhex("000c011203000301121215280f0eed03")


def test_crc_matches_real_block():
    assert protocol.crc16_modbus(REAL_STATUS[:14]) == int.from_bytes(REAL_STATUS[14:], "big")


def test_parse_real_status():
    status = LabelStatus.parse(REAL_STATUS)
    assert status.state is PrinterState.READY
    assert (status.width_mm, status.length_mm) == (14, 40)


def test_parse_rejects_corrupted_block():
    corrupted = bytearray(REAL_STATUS)
    corrupted[11] = 50
    with pytest.raises(ValueError):
        LabelStatus.parse(bytes(corrupted))


@pytest.mark.parametrize("byte, state", [(0x01, PrinterState.COVER_OPEN), (0x04, PrinterState.NO_PAPER), (0x20, PrinterState.BUSY), (0x42, PrinterState.UNKNOWN)])
def test_state_bytes(byte, state):
    assert PrinterState.from_byte(byte) is state


def test_battery_is_bcd():
    assert Battery.parse(b"BATTERY \x99\x01\r\n") == Battery(99, True)
    assert Battery.parse(b"BATTERY \x42\x00\r\n") == Battery(42, False)


def test_label_dots_for_standard_roll():
    assert protocol.label_dots(14, 40) == (96, 284)


def test_job_layout():
    image = Image.new("1", (96, 284), 1)
    image.putpixel((0, 0), 0)
    job = protocol.build_job(image, width_mm=14, length_mm=40, gap_mm=5)
    assert job.trigger == b"PRINT 1\r\n"

    header, _, rest = job.body.partition(b"BITMAP 0,0,12,284,1,")
    assert header.startswith(b"SIZE 14.0 mm,40.0 mm\r\nGAP 5.0 mm,0 mm\r\n")
    assert b"CLS\r\n" in header

    data, trailer = rest[: 12 * 284], rest[12 * 284 :]
    assert trailer == b"\r\n"
    # Black pixel at the top-left corner is a cleared MSB; everything else is white.
    assert data[0] == 0x7F
    assert set(data[1:]) == {0xFF}
