import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver
from app.orchestration.graph import build_graph


# Step 1: Open the existing SQLite database
connection = sqlite3.connect(
    "recovery_checkpoints.sqlite",
    check_same_thread=False
)

# Step 2: Create the checkpointer
checkpointer = SqliteSaver(connection)

# Step 3: Build the graph
graph = build_graph(checkpointer=checkpointer)

# Step 4: Use the previous recovery ID
config = {
    "configurable": {
        "thread_id": "recovery_104"
    }
}

# Step 5: Retrieve the previously saved state
saved_state = graph.get_state(config)

# Step 6: Display the saved information
if saved_state.values:

    print("\n--- RESTORED RECOVERY STATE ---")
    print("Recovery ID:", saved_state.values["recovery_id"])
    print("Repository:", saved_state.values["repository"])
    print("Run ID:", saved_state.values["run_id"])
    print("Status:", saved_state.values["status"])
    print("Validation Passed:", saved_state.values["validation_passed"])
    print("Approved:", saved_state.values["approved"])

    print("\n--- RESTORED DIAGNOSIS REPORT ---")
    print(saved_state.values["report"])

else:
    print("No saved recovery state found.")

# Step 7: Close the connection
connection.close()