from app.orchestration import graph as graph_module


# Simulate rejected approval
def rejected_review(state):
    print("Simulating human rejection...")

    return {
        "approved": False,
        "status": "rejected"
    }


# Save the original function
original_human_review = graph_module.human_review

# Replace human review temporarily
graph_module.human_review = rejected_review

# Build the graph
graph = graph_module.build_graph()

# Restore the original function
graph_module.human_review = original_human_review


# Initial data
initial_state = {
    "recovery_id": "recovery_103",
    "repository": "demo_repo",
    "run_id": 103,
    "status": "started"
}

# Run the workflow
result = graph.invoke(initial_state)


print("\n--- REJECTED APPROVAL RESULT ---")
print("Validation Passed:", result["validation_passed"])
print("Approved:", result["approved"])
print("Draft PR:", result.get("pr_url", "Not created"))
print("Status:", result["status"])

print("\n--- DIAGNOSIS REPORT ---")
print(result["report"])