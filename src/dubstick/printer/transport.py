from __future__ import annotations

import asyncio
import warnings
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

PRINT_SERVICE = "0000ff00-0000-1000-8000-00805f9b34fb"
WRITE_CHAR = "0000ff02-0000-1000-8000-00805f9b34fb"
NOTIFY_CHAR = "0000ff01-0000-1000-8000-00805f9b34fb"
ADVERTISED_SERVICE = "0000af30-0000-1000-8000-00805f9b34fb"

# The printer discards jobs sent in small writes, so writes must be large;
# 182 bytes every 20 ms is what the official app uses.
CHUNK_PAUSE_S = 0.02
MAX_CHUNK = 182
MIN_CHUNK = 100


class PrinterUnreachable(Exception):
    pass


@dataclass(frozen=True)
class FoundPrinter:
    address: str
    name: str
    rssi: int | None


async def scan(timeout: float = 6.0) -> list[FoundPrinter]:
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    printers = []
    for device, adv in found.values():
        name = device.name or adv.local_name or ""
        services = {uuid.lower() for uuid in adv.service_uuids}
        if ADVERTISED_SERVICE in services or name.upper().startswith("P21"):
            printers.append(FoundPrinter(device.address, name, adv.rssi))
    return sorted(printers, key=lambda p: -(p.rssi or -999))


class Link:
    """One connected session with the printer: write commands, read replies."""

    def __init__(self, client: BleakClient):
        self._client = client
        self._replies: asyncio.Queue[bytes] = asyncio.Queue()
        self._buffer = bytearray()
        self._chunk = MAX_CHUNK

    def _on_notify(self, _sender, data: bytearray) -> None:
        self._replies.put_nowait(bytes(data))

    async def start(self) -> None:
        await self._client.start_notify(NOTIFY_CHAR, self._on_notify)
        self._chunk = min(await _negotiated_mtu(self._client) - 3, MAX_CHUNK)
        if self._chunk < MIN_CHUNK:
            raise PrinterUnreachable(f"Bluetooth link too narrow for printing (MTU {self._chunk + 3})")

    async def write(self, payload: bytes) -> None:
        for start in range(0, len(payload), self._chunk):
            chunk = payload[start : start + self._chunk]
            await self._client.write_gatt_char(WRITE_CHAR, chunk, response=False)
            # Without pacing, the printer silently drops chunks.
            await asyncio.sleep(CHUNK_PAUSE_S)

    def drain(self) -> None:
        self._buffer.clear()
        while not self._replies.empty():
            self._replies.get_nowait()

    async def read_exact(self, size: int, timeout: float) -> bytes:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while len(self._buffer) < size:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError(f"expected {size} bytes, got {len(self._buffer)}")
            self._buffer += await asyncio.wait_for(self._replies.get(), remaining)

        reply = bytes(self._buffer[:size])
        del self._buffer[:size]
        return reply

    async def read_line(self, timeout: float) -> bytes:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while b"\r\n" not in self._buffer:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError(f"no complete line, got {bytes(self._buffer)!r}")
            self._buffer += await asyncio.wait_for(self._replies.get(), remaining)

        end = self._buffer.index(b"\r\n") + 2
        line = bytes(self._buffer[:end])
        del self._buffer[:end]
        return line

    async def query(self, command: bytes, size: int | None = None, timeout: float = 3.0) -> bytes:
        self.drain()
        await self.write(command)
        if size is None:
            return await self.read_line(timeout)
        return await self.read_exact(size, timeout)


async def _negotiated_mtu(client: BleakClient) -> int:
    """The MTU BlueZ actually negotiated.

    bleak reports a placeholder of 23 until the MTU is explicitly fetched, and that
    fetch is only exposed on the BlueZ backend.
    """
    acquire = getattr(client._backend, "_acquire_mtu", None)
    if acquire is not None:
        try:
            await acquire()
        except (BleakError, OSError):
            pass
    with warnings.catch_warnings():
        # Without the fetch above, BlueZ warns that it is using the default.
        warnings.simplefilter("ignore", UserWarning)
        return client.mtu_size or 23


async def _open(address: str, timeout: float, attempts: int) -> BleakClient:
    # The first connection attempt after the printer has idled often times out; a retry gets through.
    error: Exception | None = None
    for attempt in range(attempts):
        if attempt:
            await asyncio.sleep(2)
        client = BleakClient(address, timeout=timeout)
        try:
            await client.connect()
            return client
        except (BleakError, TimeoutError, OSError) as exc:
            error = exc

    reason = str(error) or type(error).__name__
    raise PrinterUnreachable(f"cannot connect to printer {address}: {reason}") from error


@asynccontextmanager
async def connect(address: str, timeout: float = 10.0, attempts: int = 3) -> AsyncIterator[Link]:
    client = await _open(address, timeout, attempts)

    try:
        link = Link(client)
        await link.start()
        yield link
    finally:
        # Disconnect every time, so the phone app can still reach the printer.
        try:
            await client.disconnect()
        except BleakError:
            pass
