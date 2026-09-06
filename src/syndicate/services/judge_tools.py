"""Read-only NexAU bindings for controller-authorized evidence."""

from collections.abc import Callable
from uuid import UUID

from nexau import Tool
from pydantic import BaseModel, ConfigDict, Field

from syndicate.models.evidence import (
    EvidenceStatus,
    RecordCitation,
    SpanQuery,
    TraceCursor,
    TraceQuery,
)
from syndicate.models.judging import (
    JudgeTool,
    ObservationStatus,
    ToolObservation,
)
from syndicate.services.judging import JudgeEvidence

_BOUND_TOOLS = (
    JudgeTool.MANIFEST,
    JudgeTool.SEARCH,
    JudgeTool.SPAN,
    JudgeTool.RECORD,
    JudgeTool.VERIFIER,
)
_SUCCESS_NEXT = {
    JudgeTool.MANIFEST: ("search_trajectory",),
    JudgeTool.SEARCH: ("read_span_context",),
    JudgeTool.SPAN: ("read_run_record",),
    JudgeTool.RECORD: ("read_verifier_result",),
    JudgeTool.VERIFIER: (),
}
_RECOVERY: dict[EvidenceStatus, tuple[ObservationStatus, tuple[str, ...]]] = {
    EvidenceStatus.RESOLVED: (ObservationStatus.SUCCESS, ()),
    EvidenceStatus.INCOMPLETE: (
        ObservationStatus.WARNING,
        ("retry after remote evidence finalizes", "stop if still incomplete"),
    ),
    EvidenceStatus.MISSING: (
        ObservationStatus.WARNING,
        ("verify the citation against the grant", "stop if the citation is wrong"),
    ),
    EvidenceStatus.FORBIDDEN: (
        ObservationStatus.ERROR,
        ("stop: citation is not granted for this judge",),
    ),
    EvidenceStatus.MISALIGNED: (
        ObservationStatus.ERROR,
        ("stop: grant identity does not match the receipt",),
    ),
}


class _ManifestInput(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    run_id: str = Field(min_length=1)
    trace_ref: str = Field(pattern=r"^[0-9a-f]{32}$")


class _RecordInput(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    run_id: str = Field(min_length=1)
    record_ref: str = Field(min_length=1, max_length=200)


def build_judge_tools(
    allowed: tuple[JudgeTool, ...], evidence: JudgeEvidence
) -> tuple[Tool, ...]:
    """Return only requested controller-owned read tools; no local trace store."""
    unsupported = set(allowed) - set(_BOUND_TOOLS)
    if unsupported:
        raise ValueError("Requested judge tool is not available from remote evidence")
    bindings = (
        _tool(
            JudgeTool.MANIFEST,
            "Read controller-authorized trace manifest metadata.",
            _ManifestInput.model_json_schema(),
            _manifest(evidence),
        ),
        _tool(
            JudgeTool.SEARCH,
            "Search controller-authorized remote trajectory spans.",
            TraceQuery.model_json_schema(),
            _search(evidence),
        ),
        _tool(
            JudgeTool.SPAN,
            "Read bounded controller-authorized remote span context.",
            SpanQuery.model_json_schema(),
            _span(evidence),
        ),
        _tool(
            JudgeTool.RECORD,
            "Read a controller-authorized trusted run record.",
            _RecordInput.model_json_schema(),
            _record(evidence),
        ),
        _tool(
            JudgeTool.VERIFIER,
            "Read a controller-authorized trusted verifier receipt.",
            _RecordInput.model_json_schema(),
            _verifier(evidence),
        ),
    )
    return tuple(tool for tool in bindings if JudgeTool(tool.name) in allowed)


def _tool(
    name: JudgeTool,
    description: str,
    schema: dict[str, object],
    bind: Callable[..., str],
) -> Tool:
    return Tool(
        name=name, description=description, input_schema=schema, implementation=bind
    )


def _observe(
    tool: JudgeTool, status: EvidenceStatus, summary: str, artifacts_json: str
) -> str:
    observation_status, recovery = _RECOVERY[status]
    next_actions = (
        _SUCCESS_NEXT[tool]
        if observation_status is ObservationStatus.SUCCESS
        else recovery
    )
    return ToolObservation(
        status=observation_status,
        summary=summary,
        next_actions=next_actions,
        artifacts_json=artifacts_json,
    ).model_dump_json()


def _manifest(evidence: JudgeEvidence) -> Callable[[str, str], str]:
    def read(run_id: str, trace_ref: str) -> str:
        overview = evidence.reader.get_trace_manifest(UUID(run_id), trace_ref)
        return _observe(
            JudgeTool.MANIFEST,
            overview.status,
            f"Trace manifest {overview.status} with {overview.span_count} spans",
            overview.model_dump_json(),
        )

    return read


def _search(evidence: JudgeEvidence) -> Callable[..., str]:
    def search(
        run_id: str,
        trace_ref: str,
        text: str = "",
        node_name: str | None = None,
        node_type: str | None = None,
        limit: int = 20,
        cursor: TraceCursor | None = None,
    ) -> str:
        page = evidence.reader.search_trajectory(
            TraceQuery(
                run_id=UUID(run_id),
                trace_ref=trace_ref,
                text=text,
                node_name=node_name,
                node_type=node_type,
                limit=limit,
                cursor=cursor,
            )
        )
        return _observe(
            JudgeTool.SEARCH,
            page.status,
            f"Trajectory search {page.status} with {len(page.span_refs)} spans",
            page.model_dump_json(),
        )

    return search


def _span(evidence: JudgeEvidence) -> Callable[..., str]:
    def read(
        run_id: str,
        trace_ref: str,
        span_ref: str,
        before: int = 1,
        after: int = 1,
        offset: int = 0,
        max_chars: int = 1000,
    ) -> str:
        context = evidence.read_span_context(
            SpanQuery(
                run_id=UUID(run_id),
                trace_ref=trace_ref,
                span_ref=span_ref,
                before=before,
                after=after,
                offset=offset,
                max_chars=max_chars,
            )
        )
        return _observe(
            JudgeTool.SPAN,
            context.status,
            f"Span context {context.status} with {len(context.spans)} spans",
            context.model_dump_json(),
        )

    return read


def _record(evidence: JudgeEvidence) -> Callable[[str, str], str]:
    def read(run_id: str, record_ref: str) -> str:
        view = evidence.reader.read_run_record(
            RecordCitation(run_id=UUID(run_id), record_ref=record_ref)
        )
        return _observe(
            JudgeTool.RECORD,
            view.status,
            f"Run record {view.status}",
            view.model_dump_json(),
        )

    return read


def _verifier(evidence: JudgeEvidence) -> Callable[[str, str], str]:
    def read(run_id: str, record_ref: str) -> str:
        view = evidence.reader.read_verifier_result(
            RecordCitation(run_id=UUID(run_id), record_ref=record_ref)
        )
        return _observe(
            JudgeTool.VERIFIER,
            view.status,
            f"Verifier result {view.status}",
            view.model_dump_json(),
        )

    return read
