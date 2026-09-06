"""Transient Neatlogs capture identity contracts."""

from contextlib import AbstractContextManager
from dataclasses import dataclass
from types import TracebackType
from typing import cast
from uuid import UUID

from syndicate.observability.neatlogs_capture import (
    CaptureState,
    NeatlogsCapture,
    RedactedEvidence,
    RunLink,
    SdkSpan,
)


@dataclass(frozen=True)
class Context:
    trace_id: int
    span_id: int


@dataclass
class Span:
    context: Context
    attributes: list[tuple[str, str | bool]]

    def get_span_context(self) -> Context:
        return self.context

    def set_attribute(self, key: str, value: str | bool) -> None:
        self.attributes.append((key, value))


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

    def flush(self) -> None:
        self.flushed = True

    def shutdown(self) -> None:
        return None


def link() -> RunLink:
    return RunLink(
        operation_id=UUID(int=1),
        attempt_id=UUID(int=2),
        run_id=UUID(int=3),
        task_id="task",
    )


def evidence() -> RedactedEvidence:
    return RedactedEvidence(
        provider_input="[redacted-provider-input]",
        provider_output="[redacted-provider-output]",
        model_input="[redacted-model-input]",
        model_output="[redacted-model-output]",
    )


def test_flush_receipt_has_remote_identity_in_emission_order() -> None:
    sdk = Sdk((Span(Context(1, 2), []), Span(Context(1, 3), [])))
    capture = NeatlogsCapture("test", sdk)
    capture.start()
    with capture.span(link(), "first", evidence()):
        pass
    with capture.span(link(), "second", evidence()):
        pass
    receipt = capture.flush(link())
    assert receipt.state is CaptureState.FLUSHED_UNVERIFIED
    assert receipt.trace_ref == "0" * 31 + "1"
    assert receipt.expected_span_refs == ("0" * 15 + "2", "0" * 15 + "3")
    assert sdk.flushed


def test_mixed_duplicate_or_missing_identity_blocks_without_flush() -> None:
    for spans in (
        (Span(Context(1, 2), []), Span(Context(2, 3), [])),
        (Span(Context(1, 2), []), Span(Context(1, 2), [])),
        (Span(Context(0, 2), []),),
    ):
        sdk = Sdk(spans)
        capture = NeatlogsCapture("test", sdk)
        capture.start()
        for index in range(len(spans)):
            with capture.span(link(), str(index), evidence()):
                pass
        assert capture.flush(link()).state is CaptureState.BLOCKED
        assert not sdk.flushed


def test_sdk_receives_only_redacted_and_distinct_evidence() -> None:
    span = Span(Context(1, 2), [])
    capture = NeatlogsCapture("test", Sdk((span,)))
    capture.start()
    with capture.span(link(), "redacted", evidence()):
        pass
    values = tuple(value for _, value in span.attributes if isinstance(value, str))
    assert "secret" not in " ".join(values)
    assert values[:2] == ("[redacted-model-input]", "[redacted-model-output]")
