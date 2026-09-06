"""Apply one model proposal inside a controller-created candidate workspace."""

import os
import stat
from collections.abc import Callable

from syndicate.models.candidate import CandidateValidationError, CandidateWorkspace
from syndicate.models.improvement import (
    CandidateCheck,
    CandidateReceipt,
    CheckStatus,
    HarnessChangeManifest,
    ProposalDraft,
    ProposalEdit,
    ProposalRequest,
)
from syndicate.services.candidate_seal import seal_candidate

ProposalTransport = Callable[[ProposalRequest], str]
CheckRunner = Callable[[CandidateWorkspace, str], bool]


def _same_paths(draft: ProposalDraft, request: ProposalRequest) -> bool:
    return {edit.path for edit in draft.edits} == set(
        request.diagnosis.edit_scope.target_paths
    )


def _write_edits(workspace: CandidateWorkspace, draft: ProposalDraft) -> None:
    allowed = {path.as_posix() for path in workspace.allowed_paths}
    for edit in draft.edits:
        if edit.path not in allowed:
            raise ValueError("Proposal edit is outside the candidate workspace")
        _write_edit(workspace, edit)


def _write_edit(workspace: CandidateWorkspace, edit: ProposalEdit) -> None:
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
    directory_fd = os.open(workspace.candidate_root, directory_flags)
    try:
        parts = edit.path.split("/")
        for part in parts[:-1]:
            next_fd = os.open(part, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        file_fd = os.open(parts[-1], file_flags, 0o644, dir_fd=directory_fd)
        try:
            if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                raise CandidateValidationError("candidate must contain regular files")
            with os.fdopen(file_fd, "w", encoding="utf-8") as file:
                file_fd = -1
                file.write(edit.content)
        finally:
            if file_fd >= 0:
                os.close(file_fd)
    except OSError as error:
        raise CandidateValidationError("candidate must not contain symlinks") from error
    finally:
        os.close(directory_fd)


def _checks(
    workspace: CandidateWorkspace, commands: tuple[str, ...], run: CheckRunner
) -> tuple[CandidateCheck, ...]:
    return tuple(
        CandidateCheck(
            command=command,
            status=CheckStatus.PASSED
            if run(workspace, command)
            else CheckStatus.FAILED,
        )
        for command in commands
    )


def apply_proposal(
    request: ProposalRequest,
    workspace: CandidateWorkspace,
    transport: ProposalTransport,
    run_check: CheckRunner,
) -> CandidateReceipt:
    """Use an injected transport; this module has no provider or budget side effects."""
    if (
        request.diagnosis.parent_harness_hash
        != "sha256:" + workspace.candidate_parent_hash
    ):
        raise ValueError("Candidate workspace does not match the diagnosed incumbent")
    draft = ProposalDraft.model_validate_json(transport(request))
    if not _same_paths(draft, request):
        raise ValueError("Proposal edits must exactly match diagnosis target paths")
    _write_edits(workspace, draft)
    checks = _checks(workspace, request.focused_checks, run_check)
    seal = seal_candidate(workspace)
    manifest = HarnessChangeManifest(
        candidate_id=request.candidate_id,
        diagnosis=request.diagnosis,
        diff_hash="sha256:" + seal.diff_hash,
        intended_fix=draft.intended_fix,
        expected_affected_tasks=draft.expected_affected_tasks,
        at_risk_tasks=draft.at_risk_tasks,
        metric_effects=draft.metric_effects,
        focused_checks=checks,
        submitted_at=request.submitted_at,
    )
    return CandidateReceipt(
        manifest=manifest,
        seal=seal,
        usage_reservation_ref=request.usage_reservation_ref,
    )
