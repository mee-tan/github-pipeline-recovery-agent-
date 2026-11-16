"""Run a recovery by hand on a saved log file (until the webhook arrives in Stage J).

    python -m app.cli tests/fixtures/logs/logic_bug.log
    python -m app.cli tests/fixtures/logs/logic_bug.log --demo-fake-fix --decision approve

--demo-fake-fix replaces patching, validation and publishing with clearly labelled
fakes, so you can watch the whole graph, including the pause for approval, before
those stages exist. Nothing is changed on GitHub.
"""

import argparse
import uuid
from pathlib import Path

from app.models.recovery import RecoveryState
from app.orchestration.graph import Dependencies, build_graph
from app.orchestration.runner import resume_recovery, start_recovery
from app.storage.audit import AuditLog

DEMO_REPO = "mee-tan/demo-repo-recovery-agent-target"
FAKE_SHA = "0" * 40


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("log", type=Path, help="a log saved with `gh run view --log`")
    parser.add_argument("--demo-fake-fix", action="store_true", help="use labelled fakes")
    parser.add_argument("--decision", help="answer for the approval step (asked if missing)")
    args = parser.parse_args()

    audit = AuditLog()
    deps = Dependencies(fetch_log=lambda repo, run_id: args.log.read_text(encoding="utf-8"))
    if args.demo_fake_fix:
        deps.generate_patch = lambda state: "--- FAKE PATCH (demo only) ---"
        deps.validate_patch = lambda state, patch: {"passed": True, "reason": "FAKE (demo)"}
        deps.publish = lambda state: "dry-run: no pull request was created"
    deps.audit = audit
    graph = build_graph(deps)

    recovery_id = f"rec-{uuid.uuid4().hex[:8]}"
    result = start_recovery(
        graph, recovery_id=recovery_id, repository=DEMO_REPO, run_id=1, head_sha=FAKE_SHA
    )
    if result.waiting_for_approval:
        print(f"Paused for approval. The graph asks:\n  {result.question}")
        decision = args.decision or input("Type approve or reject: ").strip()
        result = resume_recovery(graph, recovery_id, decision)

    state: RecoveryState = result.state  # type: ignore[assignment]
    print(f"\nRecovery {recovery_id} finished: {result.status}")
    print(f"Path: {' -> '.join(state['history'])}")
    if state["stop_reason"]:
        print(f"Stop reason: {state['stop_reason']}")
    if state["pr_url"]:
        print(f"Pull request: {state['pr_url']}")
    if state["report"]:
        print("\n" + state["report"])
    print("\nAudit log (times in UTC):")
    for row in audit.rows(recovery_id):
        print(f"  {row.at[11:19]}  {row.node:<24} {row.status:<20} {row.detail or ''}")
    if args.demo_fake_fix:
        print("\nNOTE: --demo-fake-fix was used. The patch, validation and PR above are FAKES.")


if __name__ == "__main__":
    main()
