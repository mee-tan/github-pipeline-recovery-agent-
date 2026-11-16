"""The recovery record: what one recovery attempt knows, and which state it is in.

RecoveryState is the proposal's "canonical recovery record". LangGraph passes it from
node to node; each node returns only the fields it changes.
"""

import operator
from enum import StrEnum
from typing import Annotated, Any, TypedDict


class RecoveryStatus(StrEnum):
    """Where a recovery is. A recovery always finishes in one of the TERMINAL states."""

    # Working states: the stage the recovery most recently worked in.
    RECEIVED = "received"
    COLLECTING_EVIDENCE = "collecting_evidence"
    DIAGNOSING = "diagnosing"
    PATCHING = "patching"
    VALIDATING = "validating"
    AWAITING_APPROVAL = "awaiting_approval"

    # Terminal states, from the proposal (plus one for an explicit reviewer "no").
    DRAFT_PR_CREATED = "draft_pr_created"
    DIAGNOSIS_ONLY = "diagnosis_only"
    UNSUPPORTED_FAILURE = "unsupported_failure"
    VALIDATION_FAILED = "validation_failed"
    REJECTED_BY_REVIEWER = "rejected_by_reviewer"
    DUPLICATE_IGNORED = "duplicate_ignored"
    INTERNAL_ERROR = "internal_error"


TERMINAL_STATUSES = frozenset(
    {
        RecoveryStatus.DRAFT_PR_CREATED,
        RecoveryStatus.DIAGNOSIS_ONLY,
        RecoveryStatus.UNSUPPORTED_FAILURE,
        RecoveryStatus.VALIDATION_FAILED,
        RecoveryStatus.REJECTED_BY_REVIEWER,
        RecoveryStatus.DUPLICATE_IGNORED,
        RecoveryStatus.INTERNAL_ERROR,
    }
)


class RecoveryState(TypedDict):
    """Everything one recovery attempt knows. Values are plain data (str, int, dict, list)
    so the checkpointer can save them."""

    # Identity: which failed run this recovery is about.
    recovery_id: str
    repository: str
    run_id: int
    head_sha: str

    # Progress.
    status: RecoveryStatus
    stop_reason: str | None
    history: Annotated[list[str], operator.add]  # node names, in order (a reducer)

    # What each stage produced. None until that stage has run.
    evidence: dict[str, Any] | None   # EvidenceBundle.model_dump()
    extract: dict[str, Any] | None    # FailureExtract as a dict
    diagnosis: dict[str, Any] | None
    patch: str | None                 # a unified diff
    validation: dict[str, Any] | None
    decision: str | None              # the reviewer's answer
    pr_url: str | None
    report: str | None                # the diagnosis-only report (Markdown)


def initial_state(recovery_id: str, repository: str, run_id: int, head_sha: str) -> RecoveryState:
    """A new recovery: identity filled in, everything else empty."""
    return RecoveryState(
        recovery_id=recovery_id,
        repository=repository,
        run_id=run_id,
        head_sha=head_sha,
        status=RecoveryStatus.RECEIVED,
        stop_reason=None,
        history=[],
        evidence=None,
        extract=None,
        diagnosis=None,
        patch=None,
        validation=None,
        decision=None,
        pr_url=None,
        report=None,
    )
