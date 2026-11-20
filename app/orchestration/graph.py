"""The recovery state machine (Stage C).

    START → collect_evidence → diagnose_failure → generate_patch → validate_patch
              │                  │                  │                 │
              │ no evidence      │ unsafe /         │ no patch        │ failed
              ▼                  ▼ unsupported      ▼                 ▼
          create_diagnosis_report ◄─────────────────┘        stop_validation_failed
                                                                      │ passed
                                                                      ▼
                       create_draft_pr ◄── approve ── human_review ── reject ──► stop_rejected

Every path ends in a TERMINAL status. Anything a stage cannot do yet is a dependency
(see Dependencies) whose default refuses safely, so the graph never pretends.
"""

from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agents.rule_diagnosis import rule_based_diagnose
from app.models.evidence import (
    EvidenceBudgetExceeded,
    EvidenceBundle,
    EvidenceItem,
    EvidenceSource,
)
from app.models.recovery import TERMINAL_STATUSES, RecoveryState, RecoveryStatus
from app.policy.config import MAX_DIFF_CHARS_PER_FILE, MAX_EVIDENCE_CHARS
from app.policy.redaction import redact
from app.storage.audit import AuditLog
from app.tools.log_extract import extract_failure

# ---------- dependencies: what the graph needs from the outside world ----------


def _no_patch(state: RecoveryState) -> str | None:
    return None  # patch generation arrives in Stage G


def _not_validated(state: RecoveryState, patch: str) -> dict[str, Any]:
    return {"passed": False, "reason": "validation not implemented yet (Stage H)"}


def _no_publisher(state: RecoveryState) -> str:
    raise NotImplementedError("publishing a draft PR arrives in Stage I")


@dataclass
class Dependencies:
    """Functions the nodes call. Tests pass fakes; later stages pass the real ones."""

    fetch_log: Callable[[str, int], str]  # (repository, run_id) -> log text
    # (repository, head_sha) -> [{"filename", "status", "patch"}]; None = no diff evidence
    fetch_diff: Callable[[str, str], list[dict[str, Any]]] | None = None
    diagnose: Callable[[dict[str, Any], EvidenceBundle], dict[str, Any]] = rule_based_diagnose
    generate_patch: Callable[[RecoveryState], str | None] = _no_patch
    validate_patch: Callable[[RecoveryState, str], dict[str, Any]] = _not_validated
    publish: Callable[[RecoveryState], str] = _no_publisher
    audit: AuditLog | None = None


# ---------- nodes: each takes the state, returns only what it changes ----------


def collect_evidence(state: RecoveryState, deps: Dependencies) -> dict[str, Any]:
    bundle = EvidenceBundle(
        repository=state["repository"],
        run_id=state["run_id"],
        head_sha=state["head_sha"],
        max_total_chars=MAX_EVIDENCE_CHARS,
    )
    update: dict[str, Any] = {"status": RecoveryStatus.COLLECTING_EVIDENCE}
    try:
        log_text = deps.fetch_log(state["repository"], state["run_id"])
    except Exception as exc:  # GitHub down, run deleted, no permission, ...
        update["stop_reason"] = f"evidence_unavailable: {type(exc).__name__}: {exc}"
        return update

    extract = extract_failure(log_text, bundle)
    if extract.failed_step is None:
        update["stop_reason"] = "no_failed_step_in_log"
    elif deps.fetch_diff is not None:
        try:
            files = deps.fetch_diff(state["repository"], state["head_sha"])
        except Exception:
            files = []  # the diff is helpful, not essential: carry on with the log evidence
        add_diff_evidence(bundle, files)
    update["evidence"] = bundle.model_dump(mode="json")
    update["extract"] = asdict(extract)
    return update


def add_diff_evidence(bundle: EvidenceBundle, files: list[dict[str, Any]]) -> None:
    """Add each changed file's patch as DIFF evidence, after the log evidence.

    The log says where the error was REPORTED; the diff shows what CHANGED. For the
    import failure they are different files, and the agent needs both.
    """
    for changed in files:
        patch = (changed.get("patch") or "")[:MAX_DIFF_CHARS_PER_FILE]
        if not patch.strip():
            continue  # binary files and pure renames have no patch text
        result = redact(patch)
        item = EvidenceItem.create(
            excerpt_id=f"E{len(bundle.items) + 1}",
            source=EvidenceSource.DIFF,
            location=changed["filename"],
            start_line=1,
            end_line=max(1, len(result.text.splitlines())),
            text=result.text,
            redacted=result.redacted,
        )
        try:
            bundle.add(item)
        except EvidenceBudgetExceeded:
            return  # the log evidence matters most; stop adding when the budget is full


def diagnose_failure(state: RecoveryState, deps: Dependencies) -> dict[str, Any]:
    bundle = EvidenceBundle.model_validate(state["evidence"])
    diagnosis = deps.diagnose(state["extract"] or {}, bundle)
    update: dict[str, Any] = {"status": RecoveryStatus.DIAGNOSING, "diagnosis": diagnosis}
    if not diagnosis.get("safe_to_attempt_patch"):
        update["stop_reason"] = diagnosis.get("stop_reason") or "not_safe_to_patch"
    return update


def generate_patch(state: RecoveryState, deps: Dependencies) -> dict[str, Any]:
    patch = deps.generate_patch(state)
    update: dict[str, Any] = {"status": RecoveryStatus.PATCHING, "patch": patch}
    if not patch:
        update["stop_reason"] = "no_patch_generated"
    return update


def validate_patch(state: RecoveryState, deps: Dependencies) -> dict[str, Any]:
    result = deps.validate_patch(state, state["patch"] or "")
    return {"status": RecoveryStatus.VALIDATING, "validation": result}


def human_review(state: RecoveryState, deps: Dependencies) -> dict[str, Any]:
    # Pauses the graph. Resumes with the reviewer's answer. Runs again from the top on
    # resume, so nothing that must happen only once may come before interrupt().
    answer = interrupt(
        {
            "recovery_id": state["recovery_id"],
            "diagnosis": (state["diagnosis"] or {}).get("root_cause"),
            "patch": state["patch"],
            "validation": state["validation"],
        }
    )
    return {"status": RecoveryStatus.AWAITING_APPROVAL, "decision": str(answer)}


def create_draft_pr(state: RecoveryState, deps: Dependencies) -> dict[str, Any]:
    return {"status": RecoveryStatus.DRAFT_PR_CREATED, "pr_url": deps.publish(state)}


def create_diagnosis_report(state: RecoveryState, deps: Dependencies) -> dict[str, Any]:
    diagnosis = state["diagnosis"] or {}
    unsupported = diagnosis.get("failure_category") == "unsupported"
    status = RecoveryStatus.UNSUPPORTED_FAILURE if unsupported else RecoveryStatus.DIAGNOSIS_ONLY
    return {"status": status, "report": build_report(state, status)}


def stop_validation_failed(state: RecoveryState, deps: Dependencies) -> dict[str, Any]:
    reason = (state["validation"] or {}).get("reason", "validation failed")
    return {"status": RecoveryStatus.VALIDATION_FAILED, "stop_reason": reason}


def stop_rejected(state: RecoveryState, deps: Dependencies) -> dict[str, Any]:
    return {
        "status": RecoveryStatus.REJECTED_BY_REVIEWER,
        "stop_reason": f"reviewer answered {state['decision']!r}",
    }


def build_report(state: RecoveryState, status: RecoveryStatus) -> str:
    """The diagnosis-only report (FR15): what failed, why we think so, why we stopped."""
    extract = state["extract"] or {}
    diagnosis = state["diagnosis"] or {}
    evidence = (state["evidence"] or {}).get("items", [])
    lines = [
        f"# Recovery {state['recovery_id']}: {status.value}",
        "",
        f"- Repository: {state['repository']}, run {state['run_id']}, "
        f"commit {state['head_sha'][:7]}",
        f"- Failed step: {extract.get('failed_step') or 'unknown'} "
        f"(command: {extract.get('command') or 'unknown'})",
        f"- Category: {diagnosis.get('failure_category', 'not diagnosed')}, "
        f"confidence {diagnosis.get('confidence', 'n/a')}",
        f"- Likely cause: {diagnosis.get('root_cause', 'not diagnosed')}",
        f"- Why the agent stopped: {state['stop_reason'] or 'unknown'}",
        "",
        "## Evidence",
    ]
    for item in evidence:
        lines += [
            "",
            f"**{item['excerpt_id']}**, {item['location']}, "
            f"lines {item['start_line']}-{item['end_line']}:",
            "```",
            item["text"],
            "```",
        ]
    if not evidence:
        lines += ["", "No evidence was collected."]
    return "\n".join(lines)


# ---------- routing functions: read the state, return the next node's name ----------


def _finished(state: RecoveryState) -> bool:
    return state["status"] in TERMINAL_STATUSES  # e.g. internal_error


def route_after_evidence(state: RecoveryState) -> str:
    if _finished(state):
        return END
    if state["stop_reason"]:
        return "create_diagnosis_report"
    return "diagnose_failure"


def route_after_diagnosis(state: RecoveryState) -> str:
    if _finished(state):
        return END
    if state["stop_reason"]:
        return "create_diagnosis_report"
    return "generate_patch"


def route_after_patch(state: RecoveryState) -> str:
    if _finished(state):
        return END
    if state["stop_reason"]:
        return "create_diagnosis_report"
    return "validate_patch"


def route_after_validation(state: RecoveryState) -> str:
    if _finished(state):
        return END
    if (state["validation"] or {}).get("passed") is True:
        return "human_review"
    return "stop_validation_failed"


def route_after_review(state: RecoveryState) -> str:
    # Fail closed: anything other than an explicit "approve" means no.
    return "create_draft_pr" if state["decision"] == "approve" else "stop_rejected"


# ---------- building the graph ----------

NodeFn = Callable[[RecoveryState, Dependencies], dict[str, Any]]


def _wrap(name: str, fn: NodeFn, deps: Dependencies, catch_errors: bool = True):
    """Give a node the two things every node needs: error safety and an audit record."""

    def node(state: RecoveryState) -> dict[str, Any]:
        if catch_errors:
            try:
                update = fn(state, deps)
            except Exception as exc:
                update = {
                    "status": RecoveryStatus.INTERNAL_ERROR,
                    "stop_reason": f"{name} crashed: {type(exc).__name__}: {exc}",
                }
        else:
            # human_review: interrupt() pauses the graph by raising a special exception
            # that LangGraph must receive, so this node must not catch exceptions.
            update = fn(state, deps)
        update["history"] = [name]
        if deps.audit is not None:
            deps.audit.record(
                state["recovery_id"],
                name,
                update.get("status", state["status"]),
                update.get("stop_reason"),
            )
        return update

    return node


def build_graph(deps: Dependencies, checkpointer=None):
    """Assemble and compile the recovery graph. A checkpointer is required for pausing."""
    builder = StateGraph(RecoveryState)
    nodes: dict[str, NodeFn] = {
        "collect_evidence": collect_evidence,
        "diagnose_failure": diagnose_failure,
        "generate_patch": generate_patch,
        "validate_patch": validate_patch,
        "create_draft_pr": create_draft_pr,
        "create_diagnosis_report": create_diagnosis_report,
        "stop_validation_failed": stop_validation_failed,
        "stop_rejected": stop_rejected,
    }
    for name, fn in nodes.items():
        builder.add_node(name, _wrap(name, fn, deps))
    builder.add_node("human_review", _wrap("human_review", human_review, deps, False))

    builder.add_edge(START, "collect_evidence")
    builder.add_conditional_edges(
        "collect_evidence",
        route_after_evidence,
        ["diagnose_failure", "create_diagnosis_report", END],
    )
    builder.add_conditional_edges(
        "diagnose_failure",
        route_after_diagnosis,
        ["generate_patch", "create_diagnosis_report", END],
    )
    builder.add_conditional_edges(
        "generate_patch",
        route_after_patch,
        ["validate_patch", "create_diagnosis_report", END],
    )
    builder.add_conditional_edges(
        "validate_patch",
        route_after_validation,
        ["human_review", "stop_validation_failed", END],
    )
    builder.add_conditional_edges(
        "human_review", route_after_review, ["create_draft_pr", "stop_rejected"]
    )
    for terminal in (
        "create_draft_pr",
        "create_diagnosis_report",
        "stop_validation_failed",
        "stop_rejected",
    ):
        builder.add_edge(terminal, END)

    return builder.compile(checkpointer=checkpointer or MemorySaver())
