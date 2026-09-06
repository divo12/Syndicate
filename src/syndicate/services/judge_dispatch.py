"""Concrete bounded NexAU dispatch for one task judge invocation."""

from nexau import Tool
from openai import OpenAI

from syndicate.models.budget import ProductRole
from syndicate.models.evidence import RecordCitation
from syndicate.models.judging import JudgeAttempt, JudgeSpec, TaskReport
from syndicate.models.runtime import RoleDispatchReceipt, RoleDispatchRequest
from syndicate.services.evidence import EvidenceReader
from syndicate.services.judge_tools import build_judge_tools
from syndicate.services.judging import (
    JudgeEvidence,
    execute_judge,
    require_judge_evidence,
)
from syndicate.services.runtime import dispatch_role


def role_tools(spec: JudgeSpec, evidence: JudgeEvidence) -> tuple[Tool, ...]:
    return build_judge_tools(spec.allowed_tools, evidence)


def attempt_from_receipt(
    receipt: RoleDispatchReceipt, usage_ref: str, evidence: JudgeEvidence
) -> JudgeAttempt:
    if receipt.usage_ref != usage_ref:
        raise ValueError("Role receipt usage reference does not match reservation")
    return JudgeAttempt(output_json=receipt.final_text, examined=evidence.examined)


async def dispatch_judge(
    spec: JudgeSpec,
    reader: EvidenceReader,
    verifier_refs: tuple[RecordCitation, ...],
    request: RoleDispatchRequest,
    client: OpenAI,
) -> TaskReport:
    """Dispatch exactly once after evidence preflight; failures stay accounted."""
    if request.role is not ProductRole.TASK_JUDGE:
        raise ValueError("Role dispatch request must be a task judge")
    evidence = JudgeEvidence(reader)
    try:
        require_judge_evidence(spec, reader, verifier_refs)
        receipt = await dispatch_role(request, role_tools(spec, evidence), client)
        attempt = attempt_from_receipt(receipt, request.usage_ref, evidence)
    except (OSError, RuntimeError, TimeoutError, ValueError):
        return execute_judge(
            spec,
            reader,
            verifier_refs,
            request.usage_ref,
            _failed_attempt,
        )
    return execute_judge(
        spec,
        reader,
        verifier_refs,
        request.usage_ref,
        lambda: attempt,
    )


def _failed_attempt() -> JudgeAttempt:
    raise RuntimeError("Judge role dispatch failed")
