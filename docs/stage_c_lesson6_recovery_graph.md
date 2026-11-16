# Stage C, Lesson 6 Walkthrough — The Recovery Graph

The recovery is now a real LangGraph state machine. A failed log goes in; the recovery moves through named stages; it **always** ends in a named terminal state, and every step is written to an audit log. Stages that don't exist yet (patching, validation, publishing) are placeholders that **refuse safely**, so the graph never pretends to do something it can't.

Proposal items covered: objective 3 (persistent, traceable state machine), FR14 and AC8 (audit by recovery ID), FR15 (diagnosis-only report), AC6 (unsafe cases stop with a clear reason), and the human-approval gate before any pull request.

---

## 1. The graph

```
START → collect_evidence → diagnose_failure → generate_patch → validate_patch
          │                  │                  │                 │
          │ no evidence      │ unsafe /         │ no patch        │ failed
          ▼                  ▼ unsupported      ▼                 ▼
      create_diagnosis_report ◄─────────────────┘        stop_validation_failed
                                                                  │ passed
                                                                  ▼
                   create_draft_pr ◄── approve ── human_review ── anything else ──► stop_rejected
```

Read it as: **every arrow that leaves the happy path leads to a safe stop.** The only way to reach `create_draft_pr` is through evidence, a confident diagnosis, a patch, passing validation, **and** an explicit human "approve".

| Terminal state | When |
|---|---|
| `draft_pr_created` | Everything passed and a human approved |
| `diagnosis_only` | No evidence, low confidence, or no patch: a report is written instead |
| `unsupported_failure` | The failure isn't a kind the agent may fix |
| `validation_failed` | The patch didn't pass the tests (a human is never asked) |
| `rejected_by_reviewer` | The human said anything other than "approve" |
| `internal_error` | A stage crashed: caught, recorded, stopped |

`duplicate_ignored` exists in the list too; the webhook (Stage J) will use it.

---

## 2. The files

| File | What it is |
|---|---|
| `app/policy/config.py` | Every limit in one place: confidence threshold 0.7, evidence budget, supported categories, patch limits, protected paths, allowed commands |
| `app/models/recovery.py` | `RecoveryStatus` (all states), `TERMINAL_STATUSES`, `RecoveryState` (the recovery record), `initial_state()` |
| `app/storage/audit.py` | `AuditLog`: one SQLite row per node that ran |
| `app/agents/rule_diagnosis.py` | A simple keyword-based diagnosis, standing in until the LLM arrives in Phase 6 |
| `app/orchestration/graph.py` | The nodes, the routing functions and `build_graph()` |
| `app/orchestration/runner.py` | `start_recovery()` and `resume_recovery()` |
| `app/cli.py` | Run a recovery by hand on a saved log file |
| `tests/unit/test_graph.py` | 17 tests: one per path, plus records |
| `tests/unit/test_audit.py` | 3 tests for the audit log |

---

## 3. Key ideas, with where to look

### 3a. `RecoveryState` holds plain data (`app/models/recovery.py`)

Every field is a string, number, dict or list. The evidence is stored as `bundle.model_dump(mode="json")`, a plain dict, and turned back into an `EvidenceBundle` when a node needs it. **Why:** the checkpointer has to save the state, and plain data always saves cleanly.

`history` uses the reducer from Lesson 5 (`Annotated[list[str], operator.add]`), so each node adds its name and the full path is kept.

### 3b. `Dependencies`: the outside world is passed in (`graph.py`)

Nodes never call GitHub, an LLM or a test runner directly. They call functions held in a `Dependencies` object:

```python
@dataclass
class Dependencies:
    fetch_log: ...         # (repository, run_id) -> log text
    diagnose: ...          = rule_based_diagnose
    generate_patch: ...    = _no_patch          # returns None
    validate_patch: ...    = _not_validated     # returns passed=False
    publish: ...           = _no_publisher      # raises an error
    audit: AuditLog | None = None
```

This gives two big benefits:

1. **Tests use fakes.** A test can pass a `validate_patch` that always fails, and check the graph stops correctly, without running real tests or touching GitHub.
2. **Later lessons plug in the real thing.** Lesson 7 passes a real GitHub `fetch_log`, Phase 6 a real LLM `diagnose`, and so on. The graph doesn't change.

Notice the **defaults all refuse**: no patch, validation not passed, publishing raises an error. If someone forgets to plug something in, the recovery stops safely instead of pretending. This is called **failing closed**.

### 3c. Nodes signal "stop" with `stop_reason`

A node that finds a problem sets `stop_reason` and returns. The routing function after it sees the reason and goes to a safe exit:

```python
def route_after_diagnosis(state):
    if _finished(state):            # already terminal, e.g. internal_error
        return END
    if state["stop_reason"]:        # this stage found a reason to stop
        return "create_diagnosis_report"
    return "generate_patch"         # only if nothing said stop
```

Every routing function checks the safe exits **first**; continuing is what's left over.

### 3d. `_wrap`: crash safety and the audit log for every node

Each node is wrapped before it's added to the graph. The wrapper:

1. runs the node, and if it **crashes**, turns the crash into `status = internal_error` with the error message as `stop_reason`,
2. adds the node's name to `history`,
3. writes one row to the audit log.

So no node needs its own error handling or logging code, and none can forget it.

**One exception: `human_review` is wrapped without crash-catching.** In Lesson 5 you saw `interrupt()` pause the graph. It does that by raising a special exception that LangGraph has to receive. If the wrapper caught it, the pause would be mistaken for a crash, and every recovery would end in `internal_error` instead of waiting for approval. That's why it's `_wrap("human_review", human_review, deps, False)`.

### 3e. The diagnosis report (`build_report` in `graph.py`)

When the agent stops early, it still produces something useful: a Markdown report with the failed step, the command, the category, the likely cause, **why it stopped**, and the evidence it collected, each piece labelled `E1`, `E2`, … This is requirement FR15.

### 3f. The audit log (`app/storage/audit.py`)

Plain SQLite from Python's standard library. One line worth noticing:

```python
"INSERT INTO audit (...) VALUES (?, ?, ?, ?, ?)", (recovery_id, ...)
```

The `?` placeholders pass values **separately** from the SQL text, so a value containing quotes or SQL can never change the query. `test_quotes_in_values_cannot_break_the_query` proves it with a classic attack string.

### 3g. The runner: `recovery_id` = `thread_id`

`start_recovery()` builds the initial state and runs the graph with `thread_id = recovery_id`. `resume_recovery()` continues the same recovery with the reviewer's answer. Both return a `RunResult` that says whether the recovery is waiting for approval, and what it's asking.

---

## 4. Set up and run

**Step 1.** Put the files in place (paths as in section 2):

| Download | Goes into |
|---|---|
| `config.py` | `app/policy/` |
| `recovery.py` | `app/models/` |
| `audit.py` | `app/storage/` |
| `rule_diagnosis.py` | `app/agents/` |
| `graph.py`, `runner.py` | `app/orchestration/` |
| `cli.py` | `app/` |
| `test_graph.py`, `test_audit.py` | `tests/unit/` |
| `stage_c_lesson6_recovery_graph.md` (this file) | `docs/` |

**Step 2.** Run the tests and linter:

```bash
pytest
ruff check .
```

Expect **88 passed** and `All checks passed!`

**Step 3.** Run a recovery on a real log:

```bash
python -m app.cli tests/fixtures/logs/logic_bug.log
```

Expected (the ID and times will differ):

```
Recovery rec-fa4b9db9 finished: diagnosis_only
Path: collect_evidence -> diagnose_failure -> generate_patch -> create_diagnosis_report
Stop reason: no_patch_generated

# Recovery rec-fa4b9db9: diagnosis_only

- Repository: mee-tan/demo-repo-recovery-agent-target, run 1, commit 0000000
- Failed step: Test (command: pytest)
- Category: unit_test, confidence 0.75
- Likely cause: a test assertion failed
- Why the agent stopped: no_patch_generated

## Evidence

**E1**, test/Test, lines 205-215:
    ...the failing assertion...

Audit log (times in UTC):
  17:25:34  collect_evidence         collecting_evidence
  17:25:34  diagnose_failure         diagnosing
  17:25:34  generate_patch           patching             no_patch_generated
  17:25:34  create_diagnosis_report  diagnosis_only
```

The agent diagnosed the failure, found it has no way to make a patch yet, and **stopped honestly** with a report.

**Step 4.** Watch the full path with labelled fakes:

```bash
python -m app.cli tests/fixtures/logs/lint_unused_import.log --demo-fake-fix
```

It pauses and asks you to type `approve` or `reject`. Try both (run it twice). With `approve` the path ends in `create_draft_pr`; with anything else it ends in `stop_rejected`. The last line always warns that the patch, validation and PR are fakes.

---

## 5. Important: test on your machine

LangGraph isn't installed where I build these lessons, so I tested the graph against a small stand-in that copies LangGraph's documented behaviour (including the pause raising an exception). **Your `pytest` run is the first run on real LangGraph.** If anything fails, paste me the output. The most likely place for a difference is the pause/resume in `runner.py`.

---

## 6. Commit

```bash
git add app/policy/config.py app/models/recovery.py app/storage/audit.py
git add app/agents/rule_diagnosis.py app/orchestration/graph.py app/orchestration/runner.py
git add app/cli.py tests/unit/test_graph.py tests/unit/test_audit.py docs/
git status
git commit -m "Stage C: recovery graph, audit log, policy config, rule-based diagnosis, CLI"
git push
```

Then check the CI run's **Test** step shows **88 passed**.

---

## 7. Checklist

- [ ] All files in place; `pytest` shows 88 passed; `ruff check .` passes
- [ ] Ran the CLI on all three logs
- [ ] Ran `--demo-fake-fix` with approve and with reject
- [ ] Committed, pushed, CI's Test step shows 88 passed
- [ ] You can explain: why the defaults refuse; why `human_review` isn't crash-wrapped; how a node tells the graph to stop

**Next: Lesson 7 (Stage D).** A real GitHub client, so `fetch_log` downloads the log for a run ID instead of reading a saved file, plus the commit diff as extra evidence.
