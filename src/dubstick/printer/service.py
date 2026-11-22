from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from PIL import Image

from dubstick.printer import protocol, render, transport
from dubstick.printer.protocol import Battery, LabelStatus

log = logging.getLogger(__name__)

# One BLE job at a time, process-wide.
_job_lock = asyncio.Lock()


class PrinterNotReady(Exception):
    pass


@dataclass(frozen=True)
class PrinterReport:
    address: str
    reachable: bool
    label: LabelStatus | None = None
    battery: Battery | None = None
    error: str | None = None


async def _read_label_status(link: transport.Link) -> LabelStatus:
    block = await link.query(protocol.CMD_LABEL_STATUS, protocol.STATUS_BLOCK_LEN)
    return LabelStatus.parse(block)


async def status(address: str) -> PrinterReport:
    async with _job_lock:
        try:
            async with transport.connect(address) as link:
                label = await _read_label_status(link)
                try:
                    battery = Battery.parse(await link.query(protocol.CMD_BATTERY))
                except (TimeoutError, ValueError) as exc:
                    log.warning("battery query failed: %s", exc)
                    battery = None
                return PrinterReport(address, True, label, battery)
        except transport.PrinterUnreachable as exc:
            return PrinterReport(address, False, error=str(exc))
        except (TimeoutError, ValueError) as exc:
            return PrinterReport(address, True, error=f"printer did not answer properly: {exc}")


def render_for_roll(rows: list[render.Row], label: LabelStatus, min_cap_mm: float) -> list[Image.Image]:
    width, length = protocol.label_dots(label.width_mm, label.length_mm)
    return render.render_labels(rows, width_dots=width, length_dots=length, min_cap_mm=min_cap_mm)


async def print_rows(
    address: str,
    rows: list[render.Row],
    *,
    density: int,
    gap_mm: float,
    min_cap_mm: float,
) -> int:
    """Print a record's labels, sized for whatever roll is loaded. Returns the label count."""
    async with _job_lock:
        async with transport.connect(address) as link:
            try:
                label = await _read_label_status(link)
            except (TimeoutError, ValueError) as exc:
                raise PrinterNotReady(f"printer did not report its status: {exc}") from exc
            if not label.ready:
                raise PrinterNotReady(f"printer is {label.state.message}")

            pages = render_for_roll(rows, label, min_cap_mm)
            for page in pages:
                job = protocol.build_job(
                    render.to_portrait(page),
                    width_mm=label.width_mm,
                    length_mm=label.length_mm,
                    gap_mm=gap_mm,
                    density=density,
                )
                await _expect_state(link, protocol.PrinterState.READY)
                await link.write(job.body)
                await link.write(job.trigger)
                await _wait_for_label(link)
            return len(pages)


async def _read_state(link: transport.Link) -> protocol.PrinterState:
    reply = await link.query(protocol.CMD_STATE, size=1)
    return protocol.PrinterState.from_byte(reply[0])


async def _expect_state(link: transport.Link, expected: protocol.PrinterState) -> None:
    state = await _read_state(link)
    if state is not expected:
        raise PrinterNotReady(f"printer is {state.message}")


async def _wait_for_label(link: transport.Link, start_timeout: float = 3.0, timeout: float = 30.0) -> None:
    """Follow the printer from busy back to ready; it sends nothing on its own once done."""
    loop = asyncio.get_running_loop()
    started = loop.time()
    seen_busy = False
    while True:
        await asyncio.sleep(0.3)
        state = await _read_state(link)
        elapsed = loop.time() - started

        if state is protocol.PrinterState.BUSY:
            seen_busy = True
        elif state is protocol.PrinterState.READY:
            if seen_busy:
                return
            if elapsed > start_timeout:
                # The printer took the job without ever starting it.
                raise PrinterNotReady("printer ignored the job")
        else:
            raise PrinterNotReady(f"printer is {state.message}")

        if elapsed > timeout:
            raise PrinterNotReady("label did not finish printing")
