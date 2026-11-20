"""Tests for the recovery graph (Stage C, Lesson 6).

Every test drives the real graph with fake dependencies, and checks two things:
the recovery ended in the right NAMED state, and it took the expected path.
"""

from pathlib import Path

import pytest

from app.models.recovery import TERMINAL_STATUSES, RecoveryStatus
from app.orchestration.graph import Dependencies, build_graph
from app.orchestration.runner import resume_recovery, start_recovery
from app.storage.audit import AuditLog

FIXTURES = Path(__file__).parents[1] / "fixtures" / "logs"
REPO = "mee-tan/demo-repo-recovery-agent-target"
SHA = "a" * 40


def log_reader(name: str):
    return lambda repo, run_id: (FIXTURES / f"{name}.log").read_text(encoding="utf-8")


def full_deps(name: str = "lint_unused_import", **overrides) -> Dependencies:
    """Dependencies where every stage succeeds, unless a test overrides one."""
    deps = Dependencies(
        fetch_log=log_reader(name),
        generate_patch=lambda state: "--- a/src/calculator/ops.py\n+++ b/src/calculator/ops.py",
        validate_patch=lambda state, patch: {"passed": True, "reason": "fake pass"},
        publish=lambda state: "https://github.com/example/pull/1",
        audit=AuditLog(),
    )
    for key, value in overrides.items():
        setattr(deps, key, value)
    return deps


def run(deps: Dependencies, decision: str | None = None, recovery_id: str = "rec-1"):
    graph = build_graph(deps)
    result = start_recovery(graph, recovery_id=recovery_id, repository=REPO, run_id=7, head_sha=SHA)
    if result.waiting_for_approval and decision is not None:
        result = resume_recovery(graph, recovery_id, decision)
    return result


# ---------- the happy path ----------

def test_approved_fix_ends_in_draft_pr_created():
    result = run(full_deps(), decision="approve")
    assert result.status == RecoveryStatus.DRAFT_PR_CREATED
    assert result.state["pr_url"] == "https://github.com/example/pull/1"
    assert result.state["history"] == [
        "collect_evidence",
        "diagnose_failure",
        "generate_patch",
        "validate_patch",
        "human_review",
        "create_draft_pr",
    ]


def test_graph_pauses_before_publishing():
    result = run(full_deps())  # no decision given
    assert result.waiting_for_approval is True
    assert result.status == RecoveryStatus.AWAITING_APPROVAL
    assert result.question["patch"].startswith("--- a/src/calculator/ops.py")
    assert result.state["pr_url"] is None  # nothing published while waiting


# ---------- every safe exit ----------

def test_reviewer_rejection_stops_safely():
    result = run(full_deps(), decision="reject")
    assert result.status == RecoveryStatus.REJECTED_BY_REVIEWER
    assert result.state["pr_url"] is None


def test_unclear_answer_counts_as_rejection():
    result = run(full_deps(), decision="maybe later")
    assert result.status == RecoveryStatus.REJECTED_BY_REVIEWER


def test_failed_validation_never_reaches_a_human():
    deps = full_deps(validate_patch=lambda state, patch: {"passed": False, "reason": "1 failed"})
    result = run(deps, decision="approve")
    assert result.status == RecoveryStatus.VALIDATION_FAILED
    assert "human_review" not in result.state["history"]
    assert result.state["stop_reason"] == "1 failed"


def test_no_patch_gives_a_diagnosis_report():
    result = run(full_deps(generate_patch=lambda state: None))
    assert result.status == RecoveryStatus.DIAGNOSIS_ONLY
    assert result.state["stop_reason"] == "no_patch_generated"
    assert "F401" in result.state["report"]  # the report shows the evidence


def test_unavailable_log_gives_a_diagnosis_report():
    def broken(repo, run_id):
        raise ConnectionError("GitHub unreachable")

    result = run(full_deps(fetch_log=broken))
    assert result.status == RecoveryStatus.DIAGNOSIS_ONLY
    assert result.state["stop_reason"].startswith("evidence_unavailable")
    assert "No evidence was collected." in result.state["report"]


def test_unknown_failure_is_unsupported():
    unknown = lambda repo, run_id: (  # noqa: E731
        "test\tTest\t2026-11-12T23:05:41.0Z something odd happened\n"
        "test\tTest\t2026-11-12T23:05:41.1Z ##[error]Process completed with exit code 1."
    )
    result = run(full_deps(fetch_log=unknown))
    assert result.status == RecoveryStatus.UNSUPPORTED_FAILURE
    assert result.state["diagnosis"]["failure_category"] == "unsupported"


def test_low_confidence_stops_before_patching():
    cautious = lambda extract, bundle: {  # noqa: E731
        "failure_category": "unit_test",
        "confidence": 0.4,
        "safe_to_attempt_patch": False,
        "stop_reason": "confidence 0.4 below threshold",
    }
    result = run(full_deps(diagnose=cautious))
    assert result.status == RecoveryStatus.DIAGNOSIS_ONLY
    assert "generate_patch" not in result.state["history"]


def test_a_crashing_stage_ends_in_internal_error():
    def crash(state):
        raise RuntimeError("boom")

    result = run(full_deps(generate_patch=crash))
    assert result.status == RecoveryStatus.INTERNAL_ERROR
    assert "generate_patch crashed: RuntimeError: boom" in result.state["stop_reason"]


def test_default_dependencies_never_pretend():
    # Only a log reader: patching, validation and publishing are not built yet.
    result = run(Dependencies(fetch_log=log_reader("logic_bug")))
    assert result.status == RecoveryStatus.DIAGNOSIS_ONLY
    assert result.state["stop_reason"] == "no_patch_generated"


# ---------- the real logs, diagnosed by the rule-based stand-in ----------

@pytest.mark.parametrize(
    ("name", "category"),
    [("lint_unused_import", "lint"), ("import_error", "syntax"), ("logic_bug", "unit_test")],
)
def test_real_logs_are_classified(name, category):
    result = run(full_deps(name), decision="approve")
    assert result.state["diagnosis"]["failure_category"] == category
    cited = result.state["diagnosis"]["evidence"]
    stored = [item["excerpt_id"] for item in result.state["evidence"]["items"]]
    assert cited and set(cited) <= set(stored)  # every citation points at real evidence


# ---------- records ----------

@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_every_finished_run_is_terminal_and_fully_audited(decision):
    deps = full_deps()
    result = run(deps, decision=decision)
    assert result.status in TERMINAL_STATUSES
    rows = deps.audit.rows("rec-1")
    assert [row.node for row in rows] == result.state["history"]
    assert rows[-1].status == result.status


def test_separate_recoveries_do_not_mix():
    deps = full_deps()
    graph = build_graph(deps)
    a = start_recovery(graph, recovery_id="rec-a", repository=REPO, run_id=1, head_sha=SHA)
    b = start_recovery(graph, recovery_id="rec-b", repository=REPO, run_id=2, head_sha=SHA)
    resume_recovery(graph, "rec-a", "approve")
    b = resume_recovery(graph, "rec-b", "reject")
    assert a.waiting_for_approval
    assert b.status == RecoveryStatus.REJECTED_BY_REVIEWER
    assert b.state["run_id"] == 2


# ---------- diff evidence (Stage D) ----------

OPS_PATCH = (
    "@@ -5,7 +5,7 @@\n"
    "-def subtract(a: float, b: float) -> float:\n"
    "+def subtact(a: float, b: float) -> float:"
)


def test_diff_is_added_after_the_log_evidence():
    deps = full_deps(
        "import_error",
        fetch_diff=lambda repo, sha: [
            {"filename": "src/calculator/ops.py", "status": "modified", "patch": OPS_PATCH},
            {"filename": "logo.png", "status": "added", "patch": ""},  # binary: skipped
        ],
    )
    result = run(deps, decision="approve")
    items = result.state["evidence"]["items"]
    assert [item["source"] for item in items] == ["log", "diff"]
    assert items[1]["excerpt_id"] == "E2"
    assert items[1]["location"] == "src/calculator/ops.py"
    assert "def subtact" in items[1]["text"]


def test_a_failing_diff_download_does_not_stop_the_recovery():
    def broken(repo, sha):
        raise ConnectionError("GitHub unreachable")

    result = run(full_deps(fetch_diff=broken), decision="approve")
    assert result.status == RecoveryStatus.DRAFT_PR_CREATED
    assert [item["source"] for item in result.state["evidence"]["items"]] == ["log"] * 3
