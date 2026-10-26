import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver
from app.orchestration.graph import build_graph


# Step 1: Connect to SQLite database
connection = sqlite3.connect(
    "recovery_checkpoints.sqlite",
    check_same_thread=False
)

# Step 2: Create SQLite checkpointer
checkpointer = SqliteSaver(connection)

# Step 3: Build LangGraph with checkpointer
graph = build_graph(checkpointer=checkpointer)

# Step 4: Initial recovery information
initial_state = {
    "recovery_id": "recovery_104",
    "repository": "demo_repo",
    "run_id": 104,
    "status": "started"
}

# Step 5: Use recovery_id as thread_id
config = {
    "configurable": {
        "thread_id": initial_state["recovery_id"]
    }
}

# Step 6: Run the workflow
result = graph.invoke(initial_state, config=config)

# Step 7: Display final result
print("\n--- RECOVERY RESULT ---")
print("Recovery ID:", result["recovery_id"])
print("Status:", result["status"])
print("Validation Passed:", result["validation_passed"])
print("Approved:", result["approved"])

# Step 8: Retrieve saved state
saved_state = graph.get_state(config)

print("\n--- SAVED SQLITE STATE ---")
print("Recovery ID:", saved_state.values["recovery_id"])
print("Repository:", saved_state.values["repository"])
print("Status:", saved_state.values["status"])
print("Report:", saved_state.values["report"])

# Step 9: Close database connection
connection.close()