"""Neatlogs-only transient capture; durable evidence is verified by readback."""

from contextlib import AbstractContextManager
from enum import StrEnum
from types import TracebackType
from typing import Protocol, cast
from uuid import UUID

import neatlogs  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field


class CaptureState(StrEnum):
    FLUSHED_UNVERIFIED = "flushed_unverified"
    BLOCKED = "blocked"


class RunLink(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    operation_id: UUID
    attempt_id: UUID
    run_id: UUID
    task_id: str = Field(min_length=1)


class CaptureReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    link: RunLink
    state: CaptureState
    reason: str
    trace_ref: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    expected_span_refs: tuple[str, ...] = ()


class SpanContext(Protocol):
    trace_id: int
    span_id: int


class SdkSpan(Protocol):
    def get_span_context(self) -> SpanContext: ...


class CaptureSdk(Protocol):
    def init(self, *, workflow_name: str, register_shutdown_handlers: bool) -> None: ...

    def trace(
        self, name: str, *, kind: str, **attributes: str
    ) -> AbstractContextManager[SdkSpan, bool | None]: ...

    def flush(self) -> None: ...

    def shutdown(self) -> None: ...


class _TrackedSpan(AbstractContextManager[SdkSpan]):
    def __init__(
        self,
        context: AbstractContextManager[SdkSpan, bool | None],
        capture: "NeatlogsCapture",
    ) -> None:
        self._context = context
        self._capture = capture

    def __enter__(self) -> SdkSpan:
        span = self._context.__enter__()
        self._capture._record(span.get_span_context())
        return span

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        return self._context.__exit__(exception_type, exception, traceback)


class NeatlogsCapture:
    """Creates SDK spans without storing trace payloads on disk."""

    def __init__(self, workflow_name: str, sdk: CaptureSdk | None = None) -> None:
        self.workflow_name = workflow_name
        self._sdk = sdk if sdk is not None else cast(CaptureSdk, neatlogs)
        self._trace_ref: str | None = None
        self._span_refs: list[str] = []
        self._blocked = False

    def start(self) -> None:
        self._trace_ref = None
        self._span_refs = []
        self._blocked = False
        self._sdk.init(
            workflow_name=self.workflow_name, register_shutdown_handlers=False
        )

    def span(self, link: RunLink, name: str) -> AbstractContextManager[SdkSpan]:
        return _TrackedSpan(
            self._sdk.trace(
                name,
                kind="TOOL",
                operation_id=str(link.operation_id),
                attempt_id=str(link.attempt_id),
                run_id=str(link.run_id),
                task_id=link.task_id,
            ),
            self,
        )

    def flush(self, link: RunLink) -> CaptureReceipt:
        if self._blocked or self._trace_ref is None or not self._span_refs:
            return CaptureReceipt(
                link=link,
                state=CaptureState.BLOCKED,
                reason="Neatlogs span identity is missing, mixed, or duplicate",
            )
        self._sdk.flush()
        return CaptureReceipt(
            link=link,
            state=CaptureState.FLUSHED_UNVERIFIED,
            reason="Neatlogs flush completed; persisted readback required",
            trace_ref=self._trace_ref,
            expected_span_refs=tuple(self._span_refs),
        )

    def shutdown(self) -> None:
        self._sdk.shutdown()

    def _record(self, context: SpanContext) -> None:
        trace_ref = self._id(context.trace_id, 32)
        span_ref = self._id(context.span_id, 16)
        if trace_ref is None or span_ref is None:
            self._blocked = True
            return
        if self._trace_ref is None:
            self._trace_ref = trace_ref
        elif self._trace_ref != trace_ref or span_ref in self._span_refs:
            self._blocked = True
            return
        self._span_refs.append(span_ref)

    def _id(self, value: int, width: int) -> str | None:
        if value <= 0 or value >= 16**width:
            return None
        return f"{value:0{width}x}"
