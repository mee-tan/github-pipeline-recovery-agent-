"""Run a recovery by hand (until the webhook arrives in Stage J).

From a saved log file:
    python -m app.cli --log tests/fixtures/logs/logic_bug.log

From a real failed run on GitHub (reads only; needs a token):
    export GITHUB_TOKEN=$(gh auth token)
    python -m app.cli --run-id 19234567890

--demo-fake-fix replaces patching, validation and publishing with clearly labelled
fakes, so you can watch the whole graph, including the pause for approval, before
those stages exist. Nothing is changed on GitHub.
"""

import argparse
import os
import sys
import uuid
from pathlib import Path

from app.github.client import GitHubClient, make_diff_fetcher, make_log_fetcher
from app.models.recovery import RecoveryState
from app.orchestration.graph import Dependencies, build_graph
from app.orchestration.runner import resume_recovery, start_recovery
from app.storage.audit import AuditLog

DEMO_REPO = "mee-tan/demo-repo-recovery-agent-target"
FAKE_SHA = "0" * 40


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--log", type=Path, help="a log saved with `gh run view --log`")
    source.add_argument("--run-id", type=int, help="a failed workflow run on GitHub")
    parser.add_argument("--repo", default=DEMO_REPO, help=f"owner/name (default {DEMO_REPO})")
    parser.add_argument("--demo-fake-fix", action="store_true", help="use labelled fakes")
    parser.add_argument("--decision", help="answer for the approval step (asked if missing)")
    args = parser.parse_args()

    audit = AuditLog()
    if args.log:
        run_id, head_sha = 1, FAKE_SHA
        deps = Dependencies(fetch_log=lambda repo, rid: args.log.read_text(encoding="utf-8"))
    else:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            sys.exit("Set GITHUB_TOKEN first, for example: export GITHUB_TOKEN=$(gh auth token)")
        client = GitHubClient(token)
        run = client.get_run(args.repo, args.run_id)
        if run.get("conclusion") != "failure":
            sys.exit(f"Run {args.run_id} did not fail (conclusion: {run.get('conclusion')}).")
        run_id, head_sha = args.run_id, run["head_sha"]
        print(f"Run {run_id} on {run['head_branch']} at {head_sha[:7]}: {run['html_url']}")
        deps = Dependencies(
            fetch_log=make_log_fetcher(client), fetch_diff=make_diff_fetcher(client)
        )
    if args.demo_fake_fix:
        deps.generate_patch = lambda state: "--- FAKE PATCH (demo only) ---"
        deps.validate_patch = lambda state, patch: {"passed": True, "reason": "FAKE (demo)"}
        deps.publish = lambda state: "dry-run: no pull request was created"
    deps.audit = audit
    graph = build_graph(deps)

    recovery_id = f"rec-{uuid.uuid4().hex[:8]}"
    result = start_recovery(
        graph, recovery_id=recovery_id, repository=args.repo, run_id=run_id, head_sha=head_sha
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
