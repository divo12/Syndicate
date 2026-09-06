"""Neatlogs-only transient capture; durable evidence is verified by readback."""

from contextlib import AbstractContextManager
from enum import StrEnum
from types import TracebackType
from typing import Protocol, cast
from uuid import UUID

import neatlogs  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


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


class RedactionPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    secrets: tuple[SecretStr, ...] = Field(min_length=1)

    @field_validator("secrets")
    @classmethod
    def nonempty_secrets(cls, values: tuple[SecretStr, ...]) -> tuple[SecretStr, ...]:
        if any(not value.get_secret_value() for value in values):
            raise ValueError("Configured secrets must be nonempty")
        return values


class _RedactedEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    provider_input: str
    provider_output: str
    model_input: str
    model_output: str
    count: int = Field(ge=1)


class SpanContext(Protocol):
    trace_id: int
    span_id: int


class SdkSpan(Protocol):
    def get_span_context(self) -> SpanContext: ...

    def set_attribute(self, key: str, value: str | bool | int) -> None: ...


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

    def span(
        self,
        link: RunLink,
        name: str,
        provider_input: str,
        provider_output: str,
        model_input: str,
        model_output: str,
        policy: RedactionPolicy,
    ) -> AbstractContextManager[SdkSpan]:
        return _RedactedSpan(
            _TrackedSpan(
                self._sdk.trace(
                    name,
                    kind="TOOL",
                    operation_id=str(link.operation_id),
                    attempt_id=str(link.attempt_id),
                    run_id=str(link.run_id),
                    task_id=link.task_id,
                ),
                self,
            ),
            self._redact(
                provider_input, provider_output, model_input, model_output, policy
            ),
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

    def _redact(
        self,
        provider_input: str,
        provider_output: str,
        model_input: str,
        model_output: str,
        policy: RedactionPolicy,
    ) -> _RedactedEvidence:
        values: tuple[str, ...] = (
            provider_input,
            provider_output,
            model_input,
            model_output,
        )
        count = 0
        for secret in policy.secrets:
            raw = secret.get_secret_value()
            count += sum(value.count(raw) for value in values)
            values = tuple(value.replace(raw, "[REDACTED]") for value in values)
        return _RedactedEvidence(
            provider_input=values[0],
            provider_output=values[1],
            model_input=values[2],
            model_output=values[3],
            count=count,
        )


class _RedactedSpan(AbstractContextManager[SdkSpan]):
    def __init__(
        self,
        context: AbstractContextManager[SdkSpan, bool | None],
        evidence: _RedactedEvidence,
    ) -> None:
        self._context = context
        self._evidence = evidence

    def __enter__(self) -> SdkSpan:
        span = self._context.__enter__()
        span.set_attribute("input.value", self._evidence.model_input)
        span.set_attribute("output.value", self._evidence.model_output)
        span.set_attribute("neatlogs.provider.input", self._evidence.provider_input)
        span.set_attribute("neatlogs.provider.output", self._evidence.provider_output)
        span.set_attribute("neatlogs.evidence.redacted", True)
        span.set_attribute("neatlogs.evidence.redaction_count", self._evidence.count)
        return span

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        return self._context.__exit__(exception_type, exception, traceback)
