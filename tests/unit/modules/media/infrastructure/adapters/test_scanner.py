"""Unit tests for the malware scanners: clamd framing and replies, the no-op."""

import asyncio
import struct
from collections.abc import AsyncGenerator, Sequence

import pytest
from structlog.testing import capture_logs

from yakhnama.modules.media.domain.errors import MediaContentChangedError
from yakhnama.modules.media.domain.value_objects import ScanStatus
from yakhnama.modules.media.infrastructure.adapters.scanner import (
    CLAMD_CHUNK_BYTES,
    END_OF_STREAM,
    INSTREAM_COMMAND,
    ClamAvScanner,
    ClamdReader,
    ClamdWriter,
    NoOpMalwareScanner,
    frame_chunk,
    interpret_reply,
    tcp_connector,
)


class FakeClamdReader:
    """Answers ``readuntil`` with a fixed reply, an error, or never.

    Implements: Fake (of Adapter).
    """

    def __init__(
        self, reply: bytes = b"stream: OK\x00", error: BaseException | None = None
    ) -> None:
        """Create the reader.

        Args:
            reply: What clamd answers.
            error: Raised instead of answering, if set.
        """
        self._reply = reply
        self._error = error
        self.hang = False

    async def readuntil(self, separator: bytes = b"\n") -> bytes:
        """Return the reply, raise the error, or wait forever when hanging."""
        del separator
        if self.hang:
            await asyncio.Event().wait()
        if self._error is not None:
            raise self._error
        return self._reply


class FakeClamdWriter:
    """Records everything written and whether the connection was closed.

    Implements: Fake (of Adapter).
    """

    def __init__(self) -> None:
        """Create the writer."""
        self.written = bytearray()
        self.drains = 0
        self.is_closed = False

    def write(self, data: bytes) -> None:
        """Record ``data``."""
        self.written.extend(data)

    async def drain(self) -> None:
        """Count the drain."""
        self.drains += 1

    def close(self) -> None:
        """Mark the connection closed."""
        self.is_closed = True

    async def wait_closed(self) -> None:
        """Return at once."""


class ChunkSource:
    """Streams fixed chunks and remembers whether the stream was closed.

    Implements: Fake (of Adapter).
    """

    def __init__(self, parts: Sequence[bytes]) -> None:
        """Create the source.

        Args:
            parts: The chunks to yield.
        """
        self._parts = tuple(parts)
        self.keys: list[str] = []
        self.expected: list[str | None] = []
        self.is_closed = False

    async def __call__(
        self, key: str, expected_sha256: str | None = None
    ) -> AsyncGenerator[bytes]:
        """Yield the chunks of ``key``, recording the digest it was asked for."""
        self.keys.append(key)
        self.expected.append(expected_sha256)
        try:
            for part in self._parts:
                yield part
        finally:
            self.is_closed = True


def _scanner(
    reader: FakeClamdReader,
    writer: FakeClamdWriter,
    chunks: ChunkSource,
    timeout_seconds: float = 5.0,
) -> ClamAvScanner:
    async def connect() -> tuple[ClamdReader, ClamdWriter]:
        return reader, writer

    return ClamAvScanner(
        connect=connect, read_chunks=chunks, timeout_seconds=timeout_seconds
    )


def _decode_frames(stream: bytes) -> list[bytes]:
    assert stream.startswith(INSTREAM_COMMAND)
    rest = stream[len(INSTREAM_COMMAND) :]
    frames = []
    while rest:
        (length,) = struct.unpack(">I", rest[:4])
        frames.append(rest[4 : 4 + length])
        rest = rest[4 + length :]
    return frames


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (b"stream: OK\x00", ScanStatus.CLEAN),
        (b"stream: OK", ScanStatus.CLEAN),
        (b"stream: Win.Test.EICAR_HDB-1 FOUND\x00", ScanStatus.INFECTED),
        (b"INSTREAM size limit exceeded. ERROR\x00", ScanStatus.UNAVAILABLE),
        (b"stream: OK FOUNDATION", ScanStatus.UNAVAILABLE),
        (b"\xff\xfe", ScanStatus.UNAVAILABLE),
        (b"", ScanStatus.UNAVAILABLE),
    ],
)
def test_interpret_reply_maps_clamd_answers(reply: bytes, expected: ScanStatus) -> None:
    verdict = interpret_reply(reply)

    assert verdict is expected


def test_frame_chunk_prefixes_the_big_endian_length() -> None:
    framed = frame_chunk(b"abc")

    assert framed == b"\x00\x00\x00\x03abc"
    assert frame_chunk(b"") == END_OF_STREAM


async def test_clamav_scanner_frames_the_file_and_ends_the_stream() -> None:
    big = b"x" * (CLAMD_CHUNK_BYTES + 10)
    chunks = ChunkSource([b"head", b"", big])
    writer = FakeClamdWriter()

    verdict = await _scanner(FakeClamdReader(), writer, chunks).scan(
        "media/o/1", expected_sha256="a" * 64
    )

    frames = _decode_frames(bytes(writer.written))
    assert verdict is ScanStatus.CLEAN
    assert chunks.expected == ["a" * 64]
    assert frames == [b"head", b"x" * CLAMD_CHUNK_BYTES, b"x" * 10, b""]
    assert chunks.keys == ["media/o/1"]
    assert writer.is_closed
    assert chunks.is_closed


async def test_clamav_scanner_infected_reply_is_infected_and_logged() -> None:
    reader = FakeClamdReader(b"stream: Eicar-Signature FOUND\x00")

    with capture_logs() as logs:
        verdict = await _scanner(
            reader, FakeClamdWriter(), ChunkSource([b"X5O!"])
        ).scan("media/o/1")

    assert verdict is ScanStatus.INFECTED
    assert [log["event"] for log in logs] == ["malware_scan_infected"]
    assert "media/o/1" not in str(logs)


async def test_clamav_scanner_error_reply_is_unavailable() -> None:
    reader = FakeClamdReader(b"INSTREAM size limit exceeded. ERROR\x00")

    with capture_logs() as logs:
        verdict = await _scanner(reader, FakeClamdWriter(), ChunkSource([])).scan(
            "media/o/1"
        )

    assert verdict is ScanStatus.UNAVAILABLE
    assert logs[0]["reason"] == "clamd_error_reply"


@pytest.mark.parametrize(
    "error",
    [
        ConnectionResetError("reset"),
        asyncio.IncompleteReadError(b"", 1),
        asyncio.LimitOverrunError("too long", 0),
    ],
)
async def test_clamav_scanner_broken_connection_is_unavailable(
    error: BaseException,
) -> None:
    writer = FakeClamdWriter()
    chunks = ChunkSource([b"data"])

    verdict = await _scanner(FakeClamdReader(error=error), writer, chunks).scan(
        "media/o/1"
    )

    assert verdict is ScanStatus.UNAVAILABLE
    assert writer.is_closed


async def test_clamav_scanner_unreachable_daemon_is_unavailable() -> None:
    async def refuse() -> tuple[ClamdReader, ClamdWriter]:
        raise ConnectionRefusedError

    scanner = ClamAvScanner(
        connect=refuse, read_chunks=ChunkSource([]), timeout_seconds=1.0
    )

    with capture_logs() as logs:
        verdict = await scanner.scan("media/o/1")

    assert verdict is ScanStatus.UNAVAILABLE
    assert logs[0]["reason"] == "ConnectionRefusedError"


async def test_clamav_scanner_slow_daemon_times_out_as_unavailable() -> None:
    reader = FakeClamdReader()
    reader.hang = True
    writer = FakeClamdWriter()

    verdict = await _scanner(
        reader, writer, ChunkSource([b"data"]), timeout_seconds=0.01
    ).scan("media/o/1")

    assert verdict is ScanStatus.UNAVAILABLE
    assert writer.is_closed


async def test_clamav_scanner_storage_failure_propagates_and_closes() -> None:
    writer = FakeClamdWriter()

    async def failing(key: str, expected: str | None) -> AsyncGenerator[bytes]:
        del key, expected
        yield b"part"
        message = "storage down"
        raise RuntimeError(message)

    async def connect() -> tuple[ClamdReader, ClamdWriter]:
        return FakeClamdReader(), writer

    scanner = ClamAvScanner(connect=connect, read_chunks=failing, timeout_seconds=1)

    with pytest.raises(RuntimeError, match="storage down"):
        await scanner.scan("media/o/1")

    assert writer.is_closed


def test_tcp_connector_returns_a_connector_without_connecting() -> None:
    connector = tcp_connector("clamd.internal", 3310)

    assert callable(connector)


async def test_noop_scanner_default_verdict_is_unavailable_and_warns() -> None:
    with capture_logs() as logs:
        scanner = NoOpMalwareScanner()

    verdict = await scanner.scan("media/o/1")

    assert verdict is ScanStatus.UNAVAILABLE
    assert logs[0]["event"] == "malware_scanner_disabled"
    assert logs[0]["log_level"] == "warning"


async def test_noop_scanner_can_answer_clean_for_local_publication() -> None:
    scanner = NoOpMalwareScanner(ScanStatus.CLEAN)

    verdict = await scanner.scan("media/o/1")

    assert verdict is ScanStatus.CLEAN


async def test_clamav_scanner_changed_content_propagates_and_closes() -> None:
    writer = FakeClamdWriter()

    async def changed(key: str, expected: str | None) -> AsyncGenerator[bytes]:
        del key, expected
        yield b"part"
        raise MediaContentChangedError.detected()

    async def connect() -> tuple[ClamdReader, ClamdWriter]:
        return FakeClamdReader(), writer

    scanner = ClamAvScanner(connect=connect, read_chunks=changed, timeout_seconds=1)

    with pytest.raises(MediaContentChangedError):
        await scanner.scan("media/o/1", expected_sha256="a" * 64)

    assert writer.is_closed
