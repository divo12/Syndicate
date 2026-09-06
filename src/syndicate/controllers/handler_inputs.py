"""Typed controller artifacts needed to invoke the three live handlers."""

from pydantic import BaseModel, ConfigDict

from syndicate.models.evidence import EvidenceGrant, RecordCitation, RunEvidenceGrant
from syndicate.models.improvement import ProposalRequest
from syndicate.models.judging import JudgeSpec
from syndicate.models.runtime import RoleDispatchRequest, RuntimeRequest


class HandlerInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class RuntimeInput(HandlerInput):
    request: RuntimeRequest


class JudgeInput(HandlerInput):
    spec: JudgeSpec
    request: RoleDispatchRequest
    evidence_grants: tuple[EvidenceGrant, ...]
    run_grants: tuple[RunEvidenceGrant, ...]
    verifier_refs: tuple[RecordCitation, ...]


class ProposalInput(HandlerInput):
    request: ProposalRequest
    role_request: RoleDispatchRequest
