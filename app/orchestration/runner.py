"""Start and resume recoveries. The recovery_id is LangGraph's thread_id."""

from dataclasses import dataclass
from typing import Any

from langgraph.types import Command

from app.models.recovery import RecoveryStatus, initial_state


@dataclass(frozen=True)
class RunResult:
    recovery_id: str
    status: str              # "awaiting_approval" while paused, else the state's status
    waiting_for_approval: bool
    question: Any            # what the graph asked the reviewer, if paused
    state: dict[str, Any]


def _config(recovery_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": recovery_id}}


def _result(graph, recovery_id: str) -> RunResult:
    snapshot = graph.get_state(_config(recovery_id))
    paused = "human_review" in snapshot.next
    question = None
    if paused and snapshot.tasks and snapshot.tasks[0].interrupts:
        question = snapshot.tasks[0].interrupts[0].value
    status = RecoveryStatus.AWAITING_APPROVAL if paused else snapshot.values["status"]
    return RunResult(recovery_id, str(status), paused, question, dict(snapshot.values))


def start_recovery(
    graph, *, recovery_id: str, repository: str, run_id: int, head_sha: str
) -> RunResult:
    state = initial_state(recovery_id, repository, run_id, head_sha)
    graph.invoke(state, _config(recovery_id))
    return _result(graph, recovery_id)


def resume_recovery(graph, recovery_id: str, decision: str) -> RunResult:
    graph.invoke(Command(resume=decision), _config(recovery_id))
    return _result(graph, recovery_id)
