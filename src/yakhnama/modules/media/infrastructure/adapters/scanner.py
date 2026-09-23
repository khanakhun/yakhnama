r"""Malware scanner adapters: a development no-op and a clamd ``INSTREAM`` client.

``NoOpMalwareScanner`` scans nothing. The production guard in
``platform/settings.py`` refuses it, and it logs a warning when it is built so a
deployment that uses it cannot miss the fact.

``ClamAvScanner`` speaks clamd's TCP protocol (``clamd(8)``): it sends
``zINSTREAM\0``, then the file as chunks each prefixed with its length as a 4-byte
big-endian integer, then a zero-length chunk, and reads one NUL-terminated reply:
``stream: OK``, ``stream: <signature> FOUND`` or ``... ERROR``. The framing is unit
tested against a fake stream; it has **not been verified against a real clamd**
(no daemon runs in this repository's tests; open question Q-M12). clamd's
``StreamMaxLength`` defaults to 25 MB, below ``MAX_MEDIA_BYTES``; a larger file is
answered with a size-limit error, which this adapter reports as ``unavailable``,
so operators must raise that limit to at least 50 MiB.

Patterns: Adapter + Anti-Corruption Layer.
"""

import asyncio
import struct
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import aclosing
from typing import Final, Literal, Protocol

import structlog

from yakhnama.modules.media.domain.value_objects import ScanStatus

INSTREAM_COMMAND: Final = b"zINSTREAM\x00"
END_OF_STREAM: Final = struct.pack(">I", 0)
REPLY_TERMINATOR: Final = b"\x00"
CLEAN_REPLY: Final = "stream: OK"
INFECTED_SUFFIX: Final = " FOUND"
# clamd reads each chunk into memory; 64 KiB keeps that small while keeping the
# per-chunk overhead (4 bytes and one write) negligible.
CLAMD_CHUNK_BYTES: Final = 64 * 1024
DEFAULT_CLAMD_PORT: Final = 3310

type ChunkReader = Callable[[str], AsyncGenerator[bytes]]
"""Streams the original at ``key`` in chunks, for example
``S3StoragePort.iter_original``."""


class ClamdReader(Protocol):
    """The read half of a clamd connection; ``asyncio.StreamReader`` fits.

    Implements: Adapter (port side).
    """

    async def readuntil(self, separator: bytes = b"\n") -> bytes:
        """Read up to and including ``separator``.

        Args:
            separator: The terminator.

        Returns:
            The bytes read, the terminator included.
        """
        ...


class ClamdWriter(Protocol):
    """The write half of a clamd connection; ``asyncio.StreamWriter`` fits.

    Implements: Adapter (port side).
    """

    def write(self, data: bytes) -> None:
        """Buffer ``data`` for sending.

        Args:
            data: The bytes.
        """
        ...

    async def drain(self) -> None:
        """Wait until the buffer may be written to again."""
        ...

    def close(self) -> None:
        """Close the connection."""
        ...

    async def wait_closed(self) -> None:
        """Wait until the connection is closed."""
        ...


type ClamdConnector = Callable[[], Awaitable[tuple[ClamdReader, ClamdWriter]]]
"""Opens one connection to clamd."""


class NoOpMalwareScanner:
    """``MalwareScanner`` that scans nothing, for development and tests only.

    It answers ``unavailable`` by default, as ``ScanStatus`` documents for it (Q-M9), so
    nothing it "scanned" can be published. A developer who needs the publication
    flow locally can build it with ``verdict=ScanStatus.CLEAN``; the production
    guard refuses ``malware_scanner="noop"`` whatever the verdict.

    Implements: Adapter.
    """

    def __init__(
        self,
        verdict: Literal[ScanStatus.CLEAN, ScanStatus.UNAVAILABLE] = (
            ScanStatus.UNAVAILABLE
        ),
    ) -> None:
        """Create the scanner and warn that uploads go unscanned.

        Args:
            verdict: What every scan answers.
        """
        self._verdict = verdict
        structlog.get_logger(__name__).warning(
            "malware_scanner_disabled",
            scanner="noop",
            verdict=verdict.value,
            detail="uploaded files are not scanned for malware",
        )

    async def scan(self, key: str) -> ScanStatus:
        """Return the configured verdict without reading anything.

        Args:
            key: The original's object key; unused.

        Returns:
            The configured verdict.
        """
        del key
        return self._verdict


def interpret_reply(reply: bytes) -> ScanStatus:
    """Map one clamd ``INSTREAM`` reply to a verdict.

    Args:
        reply: The reply, with or without its NUL terminator.

    Returns:
        ``clean`` for ``stream: OK``, ``infected`` for ``stream: <name> FOUND``,
        ``unavailable`` for anything else (errors, size limits, garbage).
    """
    text = reply.rstrip(REPLY_TERMINATOR).decode("utf-8", errors="replace").strip()
    if text == CLEAN_REPLY:
        return ScanStatus.CLEAN
    if text.startswith("stream: ") and text.endswith(INFECTED_SUFFIX):
        return ScanStatus.INFECTED
    return ScanStatus.UNAVAILABLE


def frame_chunk(chunk: bytes) -> bytes:
    """Prefix ``chunk`` with its length as clamd's ``INSTREAM`` expects.

    Args:
        chunk: At most ``CLAMD_CHUNK_BYTES`` bytes; an empty chunk ends the stream.

    Returns:
        The 4-byte big-endian length followed by the chunk.
    """
    return struct.pack(">I", len(chunk)) + chunk


class ClamAvScanner:
    """``MalwareScanner`` streaming the original to clamd with ``INSTREAM``.

    Unverified against a real ClamAV daemon (see the module docstring).

    Implements: Adapter.
    """

    def __init__(
        self,
        *,
        connect: ClamdConnector,
        read_chunks: ChunkReader,
        timeout_seconds: float,
    ) -> None:
        """Create the scanner.

        Args:
            connect: Opens a connection to clamd; see ``tcp_connector``.
            read_chunks: Streams an original from storage.
            timeout_seconds: Upper bound for one whole scan, connect included.
        """
        self._connect = connect
        self._read_chunks = read_chunks
        self._timeout_seconds = timeout_seconds

    async def scan(self, key: str) -> ScanStatus:
        """Stream the original at ``key`` to clamd and return its verdict.

        Args:
            key: The original's object key.

        Returns:
            ``clean`` or ``infected`` as clamd answers; ``unavailable`` if clamd
            cannot be reached, times out, closes early or answers with an error.

        Raises:
            StorageError: If storage cannot deliver the original; the task retries.
        """
        logger = structlog.get_logger(__name__)
        try:
            async with asyncio.timeout(self._timeout_seconds):
                reply = await self._exchange(key)
        except (TimeoutError, OSError, EOFError, asyncio.LimitOverrunError) as error:
            # EOFError covers asyncio.IncompleteReadError (clamd hung up).
            logger.warning("malware_scan_unavailable", reason=type(error).__name__)
            return ScanStatus.UNAVAILABLE
        verdict = interpret_reply(reply)
        if verdict is ScanStatus.UNAVAILABLE:
            logger.warning("malware_scan_unavailable", reason="clamd_error_reply")
        elif verdict is ScanStatus.INFECTED:
            logger.warning("malware_scan_infected")
        return verdict

    async def _exchange(self, key: str) -> bytes:
        reader, writer = await self._connect()
        try:
            writer.write(INSTREAM_COMMAND)
            # aclosing releases the storage stream even when clamd fails mid-file.
            async with aclosing(self._read_chunks(key)) as chunks:
                async for chunk in chunks:
                    for start in range(0, len(chunk), CLAMD_CHUNK_BYTES):
                        piece = chunk[start : start + CLAMD_CHUNK_BYTES]
                        writer.write(frame_chunk(piece))
                        # Draining per piece bounds the buffer to one piece.
                        await writer.drain()
            writer.write(END_OF_STREAM)
            await writer.drain()
            return await reader.readuntil(REPLY_TERMINATOR)
        finally:
            writer.close()
            await writer.wait_closed()


def tcp_connector(host: str, port: int = DEFAULT_CLAMD_PORT) -> ClamdConnector:
    """Return a connector opening a TCP connection to clamd at ``host:port``.

    Args:
        host: clamd's host name or address.
        port: clamd's TCP port.

    Returns:
        A connector for ``ClamAvScanner``.
    """

    async def connect() -> tuple[ClamdReader, ClamdWriter]:
        return await asyncio.open_connection(host, port)

    return connect
