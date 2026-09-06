"""Build and execute fresh paired trials without dispatching live work itself."""

from collections.abc import Callable
from uuid import UUID, uuid5

from syndicate.models.comparison import (
    Arm,
    ArmExecutionReceipt,
    PairedTrial,
    PairSchedule,
    PairScheduleRequest,
    TrialRequest,
)

_SCHEDULE_NAMESPACE = UUID("65676f90-a810-4fbd-a2ff-13681ca8c257")


def _identifier(request: PairScheduleRequest, label: str) -> UUID:
    return uuid5(_SCHEDULE_NAMESPACE, f"{request.campaign_id}:{label}")


def _trial(
    request: PairScheduleRequest,
    task_id: str,
    judge_spec_hash: str,
    repeat_index: int,
    arm: Arm,
    pair_id: UUID,
) -> TrialRequest:
    label = f"{task_id}:{repeat_index}:{arm.value}"
    candidate = arm is Arm.CANDIDATE
    return TrialRequest(
        trial_id=_identifier(request, f"trial:{label}"),
        pair_id=pair_id,
        campaign_id=request.campaign_id,
        task_id=task_id,
        repeat_index=repeat_index,
        arm=arm,
        world_id=_identifier(request, f"world:{label}"),
        harness_hash=(
            "sha256:" + request.candidate_seal.candidate_hash
            if candidate
            else request.incumbent_harness_hash
        ),
        memory_hash=(
            request.candidate_memory_hash
            if candidate
            else request.incumbent_memory_hash
        ),
        agent_model_hash=request.agent_model_hash,
        judge_spec_hash=judge_spec_hash,
        runtime_image_hash=request.runtime_image_hash,
        verifier_version=request.verifier_version,
        seed=request.seed,
        budget=request.arm_budget,
    )


def schedule_pairs(request: PairScheduleRequest) -> PairSchedule:
    """Create a deterministic interleaved schedule; no trial may reuse a world."""
    if (
        request.candidate_seal.parent_hash
        != request.incumbent_harness_hash.removeprefix("sha256:")
    ):
        raise ValueError("Candidate seal parent does not match incumbent")
    if len({control.task_id for control in request.task_controls}) != len(
        request.task_controls
    ):
        raise ValueError("Task controls must have unique task IDs")
    pairs: list[PairedTrial] = []
    for control in request.task_controls:
        for repeat_index in range(request.repeats):
            pair_id = _identifier(request, f"pair:{control.task_id}:{repeat_index}")
            incumbent = _trial(
                request,
                control.task_id,
                control.judge_spec_hash,
                repeat_index,
                Arm.INCUMBENT,
                pair_id,
            )
            candidate = _trial(
                request,
                control.task_id,
                control.judge_spec_hash,
                repeat_index,
                Arm.CANDIDATE,
                pair_id,
            )
            first_arm = Arm.INCUMBENT if len(pairs) % 2 == 0 else Arm.CANDIDATE
            pairs.append(
                PairedTrial(
                    incumbent=incumbent, candidate=candidate, first_arm=first_arm
                )
            )
    return PairSchedule(
        campaign_id=request.campaign_id,
        incumbent_harness_hash=request.incumbent_harness_hash,
        candidate_harness_hash="sha256:" + request.candidate_seal.candidate_hash,
        candidate_diff_hash="sha256:" + request.candidate_seal.diff_hash,
        pairs=tuple(pairs),
    )


def execute_schedule(
    schedule: PairSchedule, execute: Callable[[TrialRequest], ArmExecutionReceipt]
) -> tuple[ArmExecutionReceipt, ...]:
    """Call an injected isolated runner and reject substitutions in its receipt."""
    receipts: list[ArmExecutionReceipt] = []
    for trial in schedule.trials:
        receipt = execute(trial)
        if (
            receipt.trial_id != trial.trial_id
            or receipt.world_id != trial.world_id
            or receipt.arm is not trial.arm
            or receipt.harness_hash != trial.harness_hash
        ):
            raise ValueError(
                "Execution receipt changed trial ID, world, arm or harness"
            )
        receipts.append(receipt)
    return tuple(receipts)
