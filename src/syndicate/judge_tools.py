"""Read-only NexAU bindings for controller-authorized Neatlogs evidence."""

from collections.abc import Callable
from uuid import UUID

from nexau import Tool  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field

from syndicate.evidence_contracts import SpanQuery, TraceQuery
from syndicate.judge_contracts import JudgeTool
from syndicate.judging import JudgeEvidence


class _ManifestInput(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    run_id: str = Field(min_length=1)
    trace_ref: str = Field(pattern=r"^[0-9a-f]{32}$")


_BOUND_TOOLS = (JudgeTool.MANIFEST, JudgeTool.SEARCH, JudgeTool.SPAN)


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


def _manifest(evidence: JudgeEvidence) -> Callable[[str, str], str]:
    def read(run_id: str, trace_ref: str) -> str:
        return evidence.reader.get_trace_manifest(
            UUID(run_id), trace_ref
        ).model_dump_json()

    return read


def _search(evidence: JudgeEvidence) -> Callable[..., str]:
    def search(
        run_id: str,
        trace_ref: str,
        text: str = "",
        node_name: str | None = None,
        node_type: str | None = None,
        limit: int = 20,
    ) -> str:
        return evidence.reader.search_trajectory(
            TraceQuery(
                run_id=UUID(run_id),
                trace_ref=trace_ref,
                text=text,
                node_name=node_name,
                node_type=node_type,
                limit=limit,
            )
        ).model_dump_json()

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
        return evidence.read_span_context(
            SpanQuery(
                run_id=UUID(run_id),
                trace_ref=trace_ref,
                span_ref=span_ref,
                before=before,
                after=after,
                offset=offset,
                max_chars=max_chars,
            )
        ).model_dump_json()

    return read
