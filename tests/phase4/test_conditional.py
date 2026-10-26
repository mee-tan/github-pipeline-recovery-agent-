from app.orchestration import graph as graph_module


# Simulate failed validation
def failed_validation(state):
    print("Simulating failed validation...")

    return {
        "validation_passed": False,
        "status": "validation_failed"
    }


# Save the original function
original_validate_patch = graph_module.validate_patch

# Temporarily replace it in the graph module
graph_module.validate_patch = failed_validation

# Build the graph with failed validation
graph = graph_module.build_graph()

# Restore the original function
graph_module.validate_patch = original_validate_patch


# Initial data
initial_state = {
    "recovery_id": "recovery_102",
    "repository": "demo_repo",
    "run_id": 102,
    "status": "started"
}

# Run the workflow
result = graph.invoke(initial_state)


print("\n--- FAILED VALIDATION RESULT ---")
print("Validation Passed:", result["validation_passed"])
print("Approved:", result.get("approved", False))
print("Draft PR:", result.get("pr_url", "Not created"))
print("Status:", result["status"])

print("\n--- DIAGNOSIS REPORT ---")
print(result["report"])