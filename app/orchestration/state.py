from typing import TypedDict

# Store information about the pipeline recovery
class RecoveryState(TypedDict):
    recovery_id: str
    repository: str
    run_id: int
    status: str
    evidence: str
    diagnosis: str
    plan: str
    patch: str
    validation_passed: bool
    approved: bool
    report: str
    pr_url: str