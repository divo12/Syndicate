"""Transient Neatlogs capture identity contracts."""

from contextlib import AbstractContextManager
from dataclasses import dataclass
from types import TracebackType
from typing import cast
from uuid import UUID

from syndicate.observability.neatlogs_capture import (
    CaptureState,
    NeatlogsCapture,
    RunLink,
    SdkSpan,
)


@dataclass(frozen=True)
class Context:
    trace_id: int
    span_id: int


@dataclass(frozen=True)
class Span:
    context: Context

    def get_span_context(self) -> Context:
        return self.context


class SpanManager(AbstractContextManager[Span]):
    def __init__(self, span: Span) -> None:
        self._span = span

    def __enter__(self) -> Span:
        return self._span

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        return None


class Sdk:
    def __init__(self, spans: tuple[Span, ...]) -> None:
        self.spans = list(spans)
        self.flushed = False

    def init(self, *, workflow_name: str, register_shutdown_handlers: bool) -> None:
        return None

    def trace(
        self, name: str, *, kind: str, **attributes: str
    ) -> AbstractContextManager[SdkSpan, bool | None]:
        return cast(
            AbstractContextManager[SdkSpan, bool | None], SpanManager(self.spans.pop(0))
        )

    def flush(self) -> bool:
        self.flushed = True
        return True

    def shutdown(self) -> None:
        return None


def link() -> RunLink:
    return RunLink(
        operation_id=UUID(int=1),
        attempt_id=UUID(int=2),
        run_id=UUID(int=3),
        task_id="task",
    )


def test_flush_receipt_has_remote_identity_in_emission_order() -> None:
    sdk = Sdk((Span(Context(1, 2)), Span(Context(1, 3)), Span(Context(1, 4))))
    capture = NeatlogsCapture("test", sdk)
    capture.start()
    with capture.span(link(), "first"):
        pass
    with capture.span(link(), "second"):
        pass
    receipt = capture.flush(link())
    assert receipt.state is CaptureState.FLUSHED_UNVERIFIED
    assert receipt.trace_ref == "0" * 31 + "1"
    assert receipt.expected_span_refs == (
        "0" * 15 + "2",
        "0" * 15 + "3",
        "0" * 15 + "4",
    )
    assert sdk.flushed


def test_failed_flush_blocks_receipt() -> None:
    sdk = Sdk((Span(Context(1, 2)), Span(Context(1, 3))))
    sdk.flush = lambda: False  # type: ignore[method-assign]
    capture = NeatlogsCapture("test", sdk)
    capture.start()
    with capture.span(link(), "tool"):
        pass
    assert capture.flush(link()).state is CaptureState.BLOCKED


def test_mixed_duplicate_or_missing_identity_blocks_without_flush() -> None:
    for spans in (
        (Span(Context(1, 2)), Span(Context(2, 3)), Span(Context(2, 4))),
        (Span(Context(1, 2)), Span(Context(1, 2)), Span(Context(1, 3))),
        (Span(Context(0, 2)), Span(Context(0, 3))),
    ):
        sdk = Sdk(spans)
        capture = NeatlogsCapture("test", sdk)
        capture.start()
        for index in range(len(spans) - 1):
            with capture.span(link(), str(index)):
                pass
        assert capture.flush(link()).state is CaptureState.BLOCKED
        assert not sdk.flushed
