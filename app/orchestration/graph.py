from langgraph.graph import StateGraph, START, END

from app.orchestration.state import RecoveryState

from app.orchestration.nodes import (
    collect_evidence,
    diagnose_failure,
    plan_fix,
    generate_patch,
    validate_patch,
    human_review,
    create_draft_pr,
    create_diagnosis_report
)


# Check whether the patch should proceed to human review
def check_validation(state: RecoveryState):
    if state["validation_passed"]:
        return "passed"
    return "failed"


# Check whether the proposed fix was approved
def check_approval(state: RecoveryState):
    if state["approved"]:
        return "approved"
    return "rejected"


# Build the LangGraph workflow
#def build_graph():
def build_graph(checkpointer=None):

    workflow = StateGraph(RecoveryState)

    # Add all eight nodes
    workflow.add_node("collect_evidence", collect_evidence)
    workflow.add_node("diagnose_failure", diagnose_failure)
    workflow.add_node("plan_fix", plan_fix)
    workflow.add_node("generate_patch", generate_patch)
    workflow.add_node("validate_patch", validate_patch)
    workflow.add_node("human_review", human_review)
    workflow.add_node("create_draft_pr", create_draft_pr)
    workflow.add_node("create_diagnosis_report", create_diagnosis_report)

    # Connect the first five nodes
    workflow.add_edge(START, "collect_evidence")
    workflow.add_edge("collect_evidence", "diagnose_failure")
    workflow.add_edge("diagnose_failure", "plan_fix")
    workflow.add_edge("plan_fix", "generate_patch")
    workflow.add_edge("generate_patch", "validate_patch")

    # Check validation result
    workflow.add_conditional_edges(
        "validate_patch",
        check_validation,
        {
            "passed": "human_review",
            "failed": "create_diagnosis_report"
        }
    )

    # Check approval result
    workflow.add_conditional_edges(
        "human_review",
        check_approval,
        {
            "approved": "create_draft_pr",
            "rejected": "create_diagnosis_report"
        }
    )

    # Continue to the report after creating a draft PR
    workflow.add_edge("create_draft_pr", "create_diagnosis_report")

    # Finish the workflow
    workflow.add_edge("create_diagnosis_report", END)

    #return workflow.compile()
    return workflow.compile(checkpointer=checkpointer)