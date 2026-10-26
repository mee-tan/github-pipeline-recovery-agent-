from app.orchestration.graph import build_graph

# Create the LangGraph workflow
graph = build_graph()

# Initial dummy data
initial_state = {
    "recovery_id": "recovery_101",
    "repository": "demo_repo",
    "run_id": 101,
    "status": "started"
}

# Run the complete workflow
result = graph.invoke(initial_state)

# Display the final results
print("\n--- FINAL RECOVERY RESULT ---")
print("Recovery ID:", result["recovery_id"])
print("Repository:", result["repository"])
print("Run ID:", result["run_id"])
print("Status:", result["status"])
print("Evidence:", result["evidence"])
print("Diagnosis:", result["diagnosis"])
print("Plan:", result["plan"])
print("Patch:", result["patch"])
print("Validation Passed:", result["validation_passed"])
print("Approved:", result["approved"])
print("Draft PR:", result.get("pr_url", "Not created"))

print("\n--- DIAGNOSIS REPORT ---")
print(result["report"])