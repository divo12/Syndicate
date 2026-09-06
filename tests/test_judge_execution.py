import pytest
from test_judging_service import draft, generator, request
from test_task_judge import CITE, GRANT, RUN, Remote, grant, report

from syndicate.models.evidence import (
    Citation,
    CitationValidation,
    EvidenceStatus,
    RecordCitation,
    SpanQuery,
)
from syndicate.models.judging import JudgeAttempt, ReportStatus
from syndicate.observability.neatlogs_capture import RunLink
from syndicate.services.evidence import EvidenceReader
from syndicate.services.judging import JudgeEvidence, JudgeRegistry, execute_judge

ANCHOR = RecordCitation(run_id=RUN, record_ref="verifier:trusted-1")


class VerifierReader(EvidenceReader):
    def validate_citation(self, citation: Citation) -> CitationValidation:
        if citation == ANCHOR:
            return CitationValidation(status=EvidenceStatus.RESOLVED, complete=True)
        return super().validate_citation(citation)


def test_verifier_refs_and_usage_are_controller_owned() -> None:
    spec = JudgeRegistry().generate(request(), generator(draft()))
    reader = VerifierReader(Remote(), (GRANT,))
    attempt = JudgeAttempt(output_json=report().model_dump_json(), examined=(CITE,))
    result = execute_judge(spec, reader, (ANCHOR,), "usage:1", lambda: attempt)
    assert result.status is ReportStatus.COMPLETE
    assert result.verifier_refs == (ANCHOR,)
    assert result.usage_ref == "usage:1"


def test_missing_verifier_blocks_dispatch_and_retains_explicit_incomplete() -> None:
    spec = JudgeRegistry().generate(request(), generator(draft()))

    def forbidden() -> JudgeAttempt:
        raise AssertionError("Missing verifier must prevent model dispatch")

    result = execute_judge(
        spec, EvidenceReader(Remote(), (GRANT,)), (ANCHOR,), "usage:reserved", forbidden
    )
    assert result.status is ReportStatus.INCOMPLETE
    assert result.verifier_refs == (ANCHOR,)
    assert result.usage_ref == "usage:reserved"
    assert not result.findings


@pytest.mark.parametrize("output", ["invalid JSON", '{"reward":1}', "{}"])
def test_invalid_output_is_incomplete_without_repair_loop(output: str) -> None:
    spec = JudgeRegistry().generate(request(), generator(draft()))
    calls: list[int] = []

    def invoke() -> JudgeAttempt:
        calls.append(1)
        return JudgeAttempt(output_json=output, examined=())

    result = execute_judge(
        spec, VerifierReader(Remote(), (GRANT,)), (ANCHOR,), "usage:failed", invoke
    )
    assert result.status is ReportStatus.INCOMPLETE
    assert result.usage_ref == "usage:failed"
    assert calls == [1]
    assert result.unresolved_questions


def test_timeout_remains_incomplete_and_accounted() -> None:
    spec = JudgeRegistry().generate(request(), generator(draft()))

    def timeout() -> JudgeAttempt:
        raise TimeoutError("provider detail must not leak")

    result = execute_judge(
        spec, VerifierReader(Remote(), (GRANT,)), (ANCHOR,), "usage:timeout", timeout
    )
    assert result.status is ReportStatus.INCOMPLETE
    assert result.usage_ref == "usage:timeout"
    assert "provider detail" not in result.model_dump_json()


def test_remote_capture_gap_blocks_dispatch() -> None:
    spec = JudgeRegistry().generate(request(), generator(draft()))

    def forbidden() -> JudgeAttempt:
        raise AssertionError("Missing capture must prevent model dispatch")

    result = execute_judge(
        spec,
        VerifierReader(Remote(False), (GRANT,)),
        (ANCHOR,),
        "usage:reserved",
        forbidden,
    )
    assert result.status is ReportStatus.INCOMPLETE


def test_read_ledger_records_only_complete_span_reads() -> None:
    session = JudgeEvidence(EvidenceReader(Remote(), (GRANT,)))
    first = SpanQuery(
        run_id=RUN,
        trace_ref=CITE.trace_ref,
        span_ref=CITE.span_ref,
        before=0,
        after=0,
        max_chars=5,
    )
    session.read_span_context(first)
    assert session.examined == ()
    session.read_span_context(
        SpanQuery(
            run_id=RUN,
            trace_ref=CITE.trace_ref,
            span_ref=CITE.span_ref,
            before=0,
            after=0,
            max_chars=20,
            offset=5,
        )
    )
    assert session.examined == (CITE,)


def test_later_full_read_completes_earlier_partial_read_without_payload_cache() -> None:
    session = JudgeEvidence(EvidenceReader(Remote(), (GRANT,)))
    for limit in (5, 100):
        session.read_span_context(
            SpanQuery(
                run_id=RUN,
                trace_ref=CITE.trace_ref,
                span_ref=CITE.span_ref,
                before=0,
                after=0,
                max_chars=limit,
            )
        )
    assert session.examined == (CITE,)


@pytest.mark.parametrize("usage_ref", ["", " \t"])
def test_blank_usage_reference_prevents_dispatch(usage_ref: str) -> None:
    spec = JudgeRegistry().generate(request(), generator(draft()))

    def forbidden() -> JudgeAttempt:
        raise AssertionError("Usage reference must be validated before dispatch")

    with pytest.raises(ValueError, match="Usage"):
        execute_judge(
            spec, VerifierReader(Remote(), (GRANT,)), (ANCHOR,), usage_ref, forbidden
        )


def test_wrong_task_grant_never_dispatches() -> None:
    spec = JudgeRegistry().generate(request(), generator(draft()))
    wrong = grant(
        RunLink(
            operation_id=GRANT.link.operation_id,
            attempt_id=GRANT.link.attempt_id,
            run_id=RUN,
            task_id="task-b-1",
        )
    )

    def forbidden() -> JudgeAttempt:
        raise AssertionError("Wrong task must prevent model dispatch")

    result = execute_judge(
        spec, VerifierReader(Remote(), (wrong,)), (ANCHOR,), "usage:reserved", forbidden
    )
    assert result.status is ReportStatus.INCOMPLETE


def test_skipping_a_span_page_does_not_count_as_examined() -> None:
    session = JudgeEvidence(EvidenceReader(Remote(), (GRANT,)))
    for offset in (0, 10):
        session.read_span_context(
            SpanQuery(
                run_id=RUN,
                trace_ref=CITE.trace_ref,
                span_ref=CITE.span_ref,
                before=0,
                after=0,
                max_chars=5,
                offset=offset,
            )
        )
    assert session.examined == ()
