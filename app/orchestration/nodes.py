from app.orchestration.state import RecoveryState


# Node 1: Collect evidence
def collect_evidence(state: RecoveryState):
    print("Collecting pipeline failure evidence...")

    evidence = "GitHub Actions failed because a Python test failed."

    return {
        "evidence": evidence,
        "status": "evidence_collected"
    }


# Node 2: Diagnose failure
def diagnose_failure(state: RecoveryState):
    print("Diagnosing pipeline failure...")

    evidence = state["evidence"]

    if "test failed" in evidence.lower():
        diagnosis = "A Python test failed and needs investigation."
    else:
        diagnosis = "The pipeline failure needs further investigation."

    return {
        "diagnosis": diagnosis,
        "status": "failure_diagnosed"
    }
    
    # Node 3: Plan the fix
def plan_fix(state: RecoveryState):
    print("Planning a fix for the pipeline failure...")

    diagnosis = state["diagnosis"]

    if "test failed" in diagnosis.lower():
        plan = "Review the failed Python test and prepare a correction."
    else:
        plan = "Investigate the pipeline logs and identify a possible fix."

    return {
        "plan": plan,
        "status": "fix_planned"
    }
    
    # Node 4: Generate a patch
def generate_patch(state: RecoveryState):
    print("Generating a patch for the pipeline failure...")

    plan = state["plan"]

    if "Python test" in plan:
        patch = "Dummy patch: Update the Python code to fix the failing test."
    else:
        patch = "Dummy patch: Investigate and update the pipeline configuration."

    return {
        "patch": patch,
        "status": "patch_generated"
    }
    
    # Node 5: Validate the patch
def validate_patch(state: RecoveryState):
    print("Validating the generated patch...")

    patch = state["patch"]

    if patch.startswith("Dummy patch:"):
        validation_passed = True
    else:
        validation_passed = False

    return {
        "validation_passed": validation_passed,
        "status": "patch_validated"
    }
    
    # Node 6: Human review
def human_review(state: RecoveryState):
    print("Reviewing the proposed patch...")

    validation_passed = state["validation_passed"]

    if validation_passed:
        approved = True
        status = "approved"
    else:
        approved = False
        status = "rejected"

    return {
        "approved": approved,
        "status": status
    }
    
    # Node 7: Create a draft pull request
def create_draft_pr(state: RecoveryState):
    print("Creating a draft pull request...")

    #approved = state["approved"]
    approved = state.get("approved", False)

    if approved:
        pr_url = "https://github.com/example/demo-repo/pull/1"
        status = "draft_pr_created"
    else:
        pr_url = ""
        status = "pr_not_created"

    return {
        "pr_url": pr_url,
        "status": status
    }
    
    # Node 8: Create diagnosis report
def create_diagnosis_report(state: RecoveryState):
    print("Creating diagnosis report...")

    diagnosis = state["diagnosis"]
    plan = state["plan"]
    validation = state["validation_passed"]
    approved = state.get("approved", False)
    pr_url = state.get("pr_url", "")

    report = (
        f"Diagnosis: {diagnosis}\n"
        f"Fix Plan: {plan}\n"
        f"Validation Passed: {validation}\n"
        f"Approved: {approved}\n"
        f"Draft PR: {pr_url if pr_url else 'Not created'}"
    )

    return {
        "report": report,
        "status": "report_created"
    }