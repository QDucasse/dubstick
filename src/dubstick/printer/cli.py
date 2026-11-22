from __future__ import annotations

import argparse
import asyncio
import os
import re
from pathlib import Path

from dubstick.printer import protocol, render, service, transport

# Your printer's Bluetooth address; without it, --address is required.
DEFAULT_ADDRESS = os.environ.get("DUBSTICK_PRINTER") or None

# Parsed from strings like "A:140/8A,138/9A B:132/7A,140/-".
SIDE_PATTERN = re.compile(r"([A-Z]+):(\S+)")


def parse_rows(spec: str) -> list[render.Row]:
    rows = []
    for side, entries in SIDE_PATTERN.findall(spec):
        parsed = []
        for item in entries.split(","):
            bpm, _, key = item.partition("/")
            parsed.append(
                render.Entry(
                    bpm=float(bpm) if bpm not in ("", "-") else None,
                    camelot=key if key not in ("", "-") else None,
                )
            )
        rows.append(render.Row(side, tuple(parsed)))
    return rows


async def _scan(_args) -> None:
    for printer in await transport.scan():
        print(f"{printer.address}  {printer.name}  rssi={printer.rssi}")


async def _status(args) -> None:
    report = await service.status(args.address)
    if not report.reachable or report.label is None:
        print(f"unavailable: {report.error}")
        return
    print(f"state:   {report.label.state.message} (0x{report.label.raw_state:02x})")
    print(f"roll:    {report.label.roll}")
    if report.battery:
        charging = ", charging" if report.battery.charging else ""
        print(f"battery: {report.battery.percent} %{charging}")


def _preview(args) -> None:
    width, length = protocol.label_dots(args.width, args.length)
    pages = render.render_labels(
        parse_rows(args.rows), width_dots=width, length_dots=length, min_cap_mm=args.min_cap
    )
    out = Path(args.output)
    for index, page in enumerate(pages):
        path = out.with_name(f"{out.stem}-{index + 1}{out.suffix}") if len(pages) > 1 else out
        page.save(path)
        print(path)


async def _print(args) -> None:
    count = await service.print_rows(
        args.address,
        parse_rows(args.rows),
        density=args.density,
        gap_mm=args.gap,
        min_cap_mm=args.min_cap,
    )
    print(f"printed {count} label(s)")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="dubstick-printer", description="P21 label printer tools")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("scan", help="find nearby printers")

    status = sub.add_parser("status", help="query state, roll and battery")
    status.add_argument("--address", default=DEFAULT_ADDRESS, required=DEFAULT_ADDRESS is None)

    example = "A:140/8A,138/9A,141/8B B:132/7A,140/-"
    for name, help_text in (("preview", "render labels to PNG"), ("print", "print labels")):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("rows", nargs="?", default=example, help=f'e.g. "{example}"')
        cmd.add_argument("--min-cap", type=float, default=render.DEFAULT_MIN_CAP_MM)
        if name == "preview":
            cmd.add_argument("--output", "-o", default="label.png")
            cmd.add_argument("--width", type=int, default=14)
            cmd.add_argument("--length", type=int, default=40)
        else:
            cmd.add_argument("--address", default=DEFAULT_ADDRESS, required=DEFAULT_ADDRESS is None)
            cmd.add_argument("--density", type=int, default=15)
            cmd.add_argument("--gap", type=float, default=5.0)

    args = parser.parse_args(argv)
    if args.command == "preview":
        _preview(args)
        return
    handlers = {"scan": _scan, "status": _status, "print": _print}
    try:
        asyncio.run(handlers[args.command](args))
    except (transport.PrinterUnreachable, service.PrinterNotReady) as exc:
        raise SystemExit(f"error: {exc}") from None


if __name__ == "__main__":
    main()
