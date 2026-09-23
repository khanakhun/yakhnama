"""Unit tests for the metering decorators around byte streams."""

import hashlib

import pytest

from tests.unit.modules.exchange.application.support import artifact_of
from yakhnama.modules.exchange.application.streams import (
    INTEGRITY_REASON,
    SIZE_LIMIT_REASON,
    MeteredSink,
    MeteredSource,
)
from yakhnama.shared_kernel.errors import ValidationError


class _ListSink:
    """Collects chunks.

    Implements: Fake.
    """

    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    async def write(self, data: bytes) -> None:
        self.chunks.append(data)


class _BytesSource:
    """Hands out fixed bytes.

    Implements: Fake.
    """

    def __init__(self, data: bytes) -> None:
        self._data = data

    async def read(self, size: int) -> bytes:
        chunk, self._data = self._data[:size], self._data[size:]
        return chunk


async def test_metered_sink_forwards_counts_and_hashes_bytes() -> None:
    inner = _ListSink()
    sink = MeteredSink(inner, max_bytes=10)

    await sink.write(b"abc")
    await sink.write(b"de")

    assert inner.chunks == [b"abc", b"de"]
    assert sink.byte_size == 5
    assert sink.sha256 == hashlib.sha256(b"abcde").hexdigest()


async def test_metered_sink_over_limit_raises_and_forwards_nothing() -> None:
    inner = _ListSink()
    sink = MeteredSink(inner, max_bytes=4)
    await sink.write(b"abc")

    with pytest.raises(ValidationError) as caught:
        await sink.write(b"de")

    assert caught.value.details["reason"] == SIZE_LIMIT_REASON
    assert inner.chunks == [b"abc"]
    assert sink.byte_size == 3


async def test_metered_source_drain_then_verify_accepts_declared_file() -> None:
    content = b"title\nsynthetic\n"
    source = MeteredSource(_BytesSource(content), max_bytes=len(content))

    first = await source.read(3)
    await source.drain()

    assert first == b"tit"
    assert source.byte_size == len(content)
    source.verify(artifact_of(content))


async def test_metered_source_larger_than_allowed_raises_size_limit() -> None:
    source = MeteredSource(_BytesSource(b"abcdef"), max_bytes=4)

    with pytest.raises(ValidationError) as caught:
        await source.drain()

    assert caught.value.details["reason"] == SIZE_LIMIT_REASON


async def test_metered_source_verify_of_other_content_raises_integrity() -> None:
    source = MeteredSource(_BytesSource(b"abcd"), max_bytes=4)
    await source.drain()

    with pytest.raises(ValidationError) as caught:
        source.verify(artifact_of(b"abce"))

    assert caught.value.details["reason"] == INTEGRITY_REASON
