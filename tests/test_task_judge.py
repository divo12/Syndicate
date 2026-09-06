from uuid import UUID

import pytest
from pydantic import SecretStr, ValidationError
from test_judging import draft, generator, request

from syndicate.evidence import EvidenceReader
from syndicate.evidence_contracts import EvidenceGrant, SpanCitation
from syndicate.judge_contracts import (
    Finding,
    FindingCategory,
    ReportDraft,
    ReportStatus,
    RunCoverage,
)
from syndicate.judging import JudgeRegistry, validate_report
from syndicate.observability.neatlogs_capture import RunLink
from syndicate.observability.neatlogs_readback import (
    NeatlogsReadbackReader,
    NeatlogsReadbackReceipt,
    NeatlogsTraceRef,
    ReadbackSpan,
)

RUN = UUID(int=1)
TRACE = "a" * 32
SPAN = "b" * 16
LINK = RunLink(
    task_id="task-a-1", operation_id=UUID(int=2), attempt_id=UUID(int=3), run_id=RUN
)
GRANT = EvidenceGrant(link=LINK, trace_ref=TRACE, semantic_digest="sha256:" + "c" * 64)
CITE = SpanCitation(run_id=RUN, trace_ref=TRACE, span_ref=SPAN)


class Remote(NeatlogsReadbackReader):
    def __init__(self, complete: bool = True) -> None:
        super().__init__(SecretStr("fixture"))
        self.complete = complete

    def read(
        self, link: RunLink, trace_ref: NeatlogsTraceRef
    ) -> NeatlogsReadbackReceipt:
        return NeatlogsReadbackReceipt(
            link=LINK,
            trace_ref=TRACE,
            finalized=self.complete,
            complete=self.complete,
            semantic_digest=GRANT.semantic_digest,
            spans=(
                ReadbackSpan(
                    span_id=SPAN,
                    node_name="tool",
                    node_type="tool",
                    input_text="Close incident",
                    output_text="Denied",
                ),
            ),
        )


def report(
    category: FindingCategory = FindingCategory.UNSUPPORTED_CLAIM,
) -> ReportDraft:
    return ReportDraft(
        run_ids=(RUN,),
        status=ReportStatus.COMPLETE,
        findings=(
            Finding(
                finding_id="f1",
                category=category,
                observation="The close request was denied.",
                hypothesis="The agent may have omitted authorization.",
                evidence=(CITE,),
            ),
        ),
        coverage=(RunCoverage(run_id=RUN, examined=(CITE,)),),
    )


def test_report_keeps_cited_observation_hypothesis_and_recovery_distinct() -> None:
    spec = JudgeRegistry().generate(request(), generator(draft()))
    result = validate_report(
        spec, EvidenceReader(Remote(), (GRANT,)), report(), (CITE,)
    )
    assert result.status is ReportStatus.COMPLETE
    assert result.judge_spec_hash == spec.spec_hash
    assert result.findings[0].observation != result.findings[0].hypothesis
    recovered = validate_report(
        spec,
        EvidenceReader(Remote(), (GRANT,)),
        report(FindingCategory.RECOVERED_ERROR),
        (CITE,),
    )
    assert recovered.findings[0].category is FindingCategory.RECOVERED_ERROR


def test_incomplete_remote_capture_cannot_produce_complete_report() -> None:
    spec = JudgeRegistry().generate(request(), generator(draft()))
    with pytest.raises(ValueError, match="citation"):
        validate_report(
            spec, EvidenceReader(Remote(False), (GRANT,)), report(), (CITE,)
        )
    empty = ReportDraft(
        run_ids=(RUN,),
        status=ReportStatus.COMPLETE,
        coverage=(RunCoverage(run_id=RUN),),
    )
    result = validate_report(spec, EvidenceReader(Remote(False), (GRANT,)), empty, ())
    assert result.status is ReportStatus.INCOMPLETE
    assert result.unresolved_questions


def test_fabricated_or_unread_citation_is_rejected() -> None:
    spec = JudgeRegistry().generate(request(), generator(draft()))
    reader = EvidenceReader(Remote(), (GRANT,))
    with pytest.raises(ValueError, match="examined"):
        validate_report(spec, reader, report(), ())
    missing = SpanCitation(run_id=RUN, trace_ref=TRACE, span_ref="d" * 16)
    forged = ReportDraft(
        run_ids=(RUN,),
        status=ReportStatus.COMPLETE,
        findings=(
            Finding(
                finding_id="f",
                category=FindingCategory.WRONG_ENTITY,
                observation="Wrong entity",
                evidence=(missing,),
            ),
        ),
        coverage=(RunCoverage(run_id=RUN, examined=(missing,)),),
    )
    with pytest.raises(ValueError, match="citation"):
        validate_report(spec, reader, forged, (missing,))


def test_every_assigned_run_must_be_accounted_and_reward_cannot_be_overridden() -> None:
    spec = JudgeRegistry().generate(request(), generator(draft()))
    other = EvidenceGrant(
        link=RunLink(
            task_id="task-a-1",
            operation_id=UUID(int=4),
            attempt_id=UUID(int=5),
            run_id=UUID(int=6),
        ),
        trace_ref="d" * 32,
        semantic_digest=GRANT.semantic_digest,
    )
    with pytest.raises(ValueError, match="assigned runs"):
        validate_report(
            spec, EvidenceReader(Remote(), (GRANT, other)), report(), (CITE,)
        )
    with pytest.raises(ValidationError):
        ReportDraft.model_validate_json(
            report().model_dump_json()[:-1] + ',"reward":1}'
        )


def test_unread_relevant_evidence_forces_incomplete() -> None:
    spec = JudgeRegistry().generate(request(), generator(draft()))
    partial = ReportDraft(
        run_ids=(RUN,),
        status=ReportStatus.COMPLETE,
        coverage=(RunCoverage(run_id=RUN, examined=(CITE,), unread_relevant=(CITE,)),),
    )
    result = validate_report(spec, EvidenceReader(Remote(), (GRANT,)), partial, (CITE,))
    assert result.status is ReportStatus.INCOMPLETE


def test_grant_from_another_task_is_rejected() -> None:
    spec = JudgeRegistry().generate(request(), generator(draft()))
    other_task = EvidenceGrant(
        link=RunLink(
            task_id="task-b-1",
            operation_id=UUID(int=2),
            attempt_id=UUID(int=3),
            run_id=RUN,
        ),
        trace_ref=TRACE,
        semantic_digest=GRANT.semantic_digest,
    )
    with pytest.raises(ValueError, match="another task"):
        validate_report(
            spec, EvidenceReader(Remote(), (other_task,)), report(), (CITE,)
        )


def test_coverage_and_findings_require_strict_unique_ids() -> None:
    with pytest.raises(ValidationError):
        RunCoverage(run_id=UUID(int=99), examined=(CITE,))
    with pytest.raises(ValidationError):
        ReportDraft(
            run_ids=(RUN, RUN),
            status=ReportStatus.COMPLETE,
            coverage=(RunCoverage(run_id=RUN),),
        )
    with pytest.raises(ValidationError):
        Finding(
            finding_id="f",
            category=FindingCategory.WRONG_ENTITY,
            observation="Unsupported assertion",
            evidence=(),
        )
