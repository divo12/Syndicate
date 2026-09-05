"""Neatlogs-only transient capture; durable evidence is verified by readback."""

from contextlib import AbstractContextManager
from enum import StrEnum
from typing import cast
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


class NeatlogsCapture:
    """Creates SDK spans without storing trace payloads on disk."""

    def __init__(self, workflow_name: str) -> None:
        self.workflow_name = workflow_name

    def start(self) -> None:
        neatlogs.init(
            workflow_name=self.workflow_name, register_shutdown_handlers=False
        )

    def span(self, link: RunLink, name: str) -> AbstractContextManager[object]:
        return cast(
            AbstractContextManager[object],
            neatlogs.trace(
                name,
                kind="TOOL",
                operation_id=str(link.operation_id),
                attempt_id=str(link.attempt_id),
                run_id=str(link.run_id),
                task_id=link.task_id,
            ),
        )

    def flush(self, link: RunLink) -> CaptureReceipt:
        neatlogs.flush()
        return CaptureReceipt(
            link=link,
            state=CaptureState.FLUSHED_UNVERIFIED,
            reason="Neatlogs flush completed; persisted readback required",
        )

    def shutdown(self) -> None:
        neatlogs.shutdown()
