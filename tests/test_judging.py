from collections.abc import Callable

import pytest
from pydantic import ValidationError

from syndicate.budget_policy import BudgetCap
from syndicate.judge_contracts import (
    Criterion,
    CriterionStatus,
    EvidenceKind,
    JudgeBuildRequest,
    JudgeDraft,
    PublicRequirement,
    RequirementKind,
    SupportQuote,
)
from syndicate.judging import JudgeRegistry


def request() -> JudgeBuildRequest:
    return JudgeBuildRequest(
        campaign_id="campaign-1",
        task_id="task-a-1",
        requirements=(
            PublicRequirement(
                reference="instruction:1",
                kind=RequirementKind.GOAL,
                text="Close the incident after confirming recovery.",
            ),
        ),
        budget=BudgetCap(max_tokens=1000, max_seconds=30, max_spend_microusd=10000),
    )


def draft(quote: str = "confirming recovery") -> JudgeDraft:
    return JudgeDraft(
        criteria=(
            Criterion(
                criterion_id="recovery",
                description="Confirm recovery before closing.",
                status=CriterionStatus.SUPPORTED,
                support=(SupportQuote(reference="instruction:1", quote=quote),),
                evidence_requirements=(EvidenceKind.TRAJECTORY, EvidenceKind.VERIFIER),
            ),
        )
    )


def generator(value: JudgeDraft) -> Callable[[JudgeBuildRequest], str]:
    return lambda _: value.model_dump_json()


def test_generation_pins_first_spec_and_never_learns_from_later_results() -> None:
    registry = JudgeRegistry()
    spec = registry.generate(request(), generator(draft()))

    def forbidden(_: JudgeBuildRequest) -> str:
        raise AssertionError("Already generated judge must not call model again")

    assert registry.generate(request(), forbidden) is spec
    assert spec.task_id == "task-a-1"
    assert spec.model == "gpt-5.4-mini"
    assert len(spec.spec_hash) == 64
    assert "No source" in spec.prompt
    assert "Neatlogs is the only durable trace source" in spec.prompt
    assert (
        "missing or incomplete Neatlogs evidence requires an incomplete report"
        in spec.prompt
    )


def test_invalid_support_is_rejected_without_sealing() -> None:
    registry = JudgeRegistry()
    with pytest.raises(ValueError, match="public requirement"):
        registry.generate(request(), generator(draft("secret expected answer")))
    assert registry.generate(request(), generator(draft())).criteria == draft().criteria


def test_changed_public_input_cannot_replace_pinned_spec() -> None:
    registry = JudgeRegistry()
    registry.generate(request(), generator(draft()))
    changed = request().model_copy(
        update={
            "budget": BudgetCap(
                max_tokens=2000,
                max_seconds=30,
                max_spend_microusd=10000,
            )
        }
    )
    with pytest.raises(ValueError, match="pinned"):
        registry.generate(changed, generator(draft()))


def test_unresolved_criterion_requires_explicit_missing_context() -> None:
    with pytest.raises(ValidationError):
        Criterion(
            criterion_id="policy",
            description="Unknown policy",
            status=CriterionStatus.UNRESOLVED,
            evidence_requirements=(EvidenceKind.VERIFIER,),
        )
    unresolved = Criterion(
        criterion_id="policy",
        description="Unknown policy",
        status=CriterionStatus.UNRESOLVED,
        unresolved_reason="Public policy absent",
        evidence_requirements=(EvidenceKind.VERIFIER,),
    )
    spec = JudgeRegistry().generate(
        request(), generator(JudgeDraft(criteria=(unresolved,)))
    )
    assert spec.criteria[0].unresolved_reason == "Public policy absent"


def test_schema_rejects_execution_tools_and_coercion() -> None:
    with pytest.raises(ValidationError):
        JudgeDraft.model_validate_json('{"criteria": [], "allowed_tools": ["shell"]}')
    with pytest.raises(ValidationError):
        JudgeBuildRequest.model_validate_json('{"campaign_id": 1, "task_id": "x"}')


def test_duplicate_criteria_and_missing_goal_are_rejected() -> None:
    with pytest.raises(ValidationError):
        JudgeDraft(criteria=(draft().criteria[0], draft().criteria[0]))
    with pytest.raises(ValidationError):
        JudgeBuildRequest(
            campaign_id="c", task_id="t", requirements=(), budget=request().budget
        )
