# Phase 5.3 Walkthrough — Log Extraction

How the agent finds the few lines that explain a CI failure inside a 230-line log. This is the first lesson where Lessons 2 and 3 work together: extraction finds the lines, **redaction** (5.2) cleans them, and they become **evidence** (5.1).

---

## 0. The problem this solves

In Lesson 1 you used `grep` to find the error in each log. It was around **line 200 of about 230**, and the files were about **24,000 characters** each. Nearly everything before that line was setup: checking out code, installing Python, pip installing packages.

Sending the whole log to the LLM would be slow, expensive, and *worse*: the model can get distracted by setup noise, and the proposal's evidence budget (FR06) forbids it. So the agent needs to do what you did with `grep`, but reliably and for any failure.

**The result on your real logs:**

| Log | Failed step | Command | Evidence kept | Full log |
|---|---|---|---|---|
| `lint_unused_import.log` | Lint | `ruff check .` | 279 characters, 3 items | ~24,000 |
| `import_error.log` | Test | `pytest` | 1,192 characters, 1 item | ~25,000 |
| `logic_bug.log` | Test | `pytest` | 452 characters, 1 item | ~24,500 |

That's 1 to 5% of each log, and it still contains everything a person needs to diagnose the failure.

---

## 1. What we learned by reading the real logs first

Before writing any code, we studied the three fixture files. Four facts shaped the design:

**1. Every line has the same shape**, split by tab characters:

```
test <TAB> Lint <TAB> 2026-11-12T23:05:38.7078118Z F401 [*] `os` imported but unused
job        step       timestamp                    message
```

So each line already says which step it came from. No guessing needed.

**2. The failing step is marked clearly.** The last line of a failed step is always:

```
##[error]Process completed with exit code 1.
```

**3. Each step starts with a header that isn't output:**

```
##[group]Run ruff check .           ← tells us the command
shell: /usr/bin/bash -e {0}
env:
  pythonLocation: /opt/...           ← about 8 lines of settings
##[endgroup]                         ← real output starts after this
```

**4. Ruff found two errors, not one.** The lint log says `Found 2 errors`: `I001` (import block is unsorted) as well as `F401` (unused import). Both come from the same `import os` line. Our Lesson 1 ground truth only listed F401, so it needs updating (section 8).

This is a habit worth keeping: **read the real data before writing code that reads it.**

---

## 2. The five steps the extractor takes

```
log text
   │  1. parse_log          → a list of LogLine(number, job, step, message)
   │  2. find_failed_step   → ("test", "Lint", 1)  = job, step, exit code
   │  3. step_output        → the command, and that step's output without its header
   │  4. signal_regions     → groups of lines: each error signal plus context
   ▼  5. extract_failure    → redact each region, add it to the EvidenceBundle
EvidenceBundle with E1, E2, E3 …
```

Each step is its own small function, so each can be tested on its own.

---

## 3. The file: `app/tools/log_extract.py`

### 3a. `parse_log`: text → `LogLine`s

```python
parts = raw.split("\t", 2)        # job \t step \t timestamp message
```

`split("\t", 2)` splits on tabs **at most twice**, giving exactly three parts. That matters because the message itself could contain a tab, and we don't want it split further.

Then `rest.partition(" ")` splits the timestamp from the message at the **first space**. `partition` always returns three parts (before, the separator, after), even if there's no space, so it never crashes.

**Colour codes.** Some lines contain terminal colour codes like `^[[36;1m` (in your logs: `^[[36;1mpytest^[[0m`). They make text coloured in a terminal but are junk in a file. `ANSI_COLOUR` is a regex that removes them.

**The invisible character.** The first line of each step starts with a byte-order mark: a character you can't see (`357 273 277` when we dumped the bytes). It sits *before the timestamp*. Since we split at the first space and discard the timestamp, the mark is discarded with it. The first version of this code removed the mark separately, but a "break it" check showed that line did nothing, so it was deleted. **Code that no test can tell is there is code you don't need.**

`LogLine.number` keeps each line's position in the file (from 1). That's how evidence can say "lines 199 to 204", which a human can check against the real log.

### 3b. `find_failed_step`: the first failure wins

It walks through the lines and returns the **first** `##[error]Process completed with exit code N` it finds, as `(job, step, exit_code)`.

**Why the first?** In a CI job, once one step fails, the later steps are skipped. So the first failure is the cause, and anything after it is a symptom. This is the proposal's rule to "distinguish cause from downstream symptoms."

If no step failed, it returns `None`. You've already seen that this happens: your runs #2 to #5 failed because the *workflow file* was invalid, and no step ever ran.

### 3c. `step_output`: the command, minus the header

It takes all lines from the failed step, reads the command from the `##[group]Run …` line, and drops everything up to the first `##[endgroup]`. What's left is the real output.

### 3d. `ERROR_SIGNALS` and `is_signal`: which lines matter

A list of patterns, one per kind of tool output:

| Pattern | Catches | From which tool |
|---|---|---|
| `^##\[error\]` | GitHub's failure marker | GitHub Actions |
| `^[A-Z]{1,4}\d{3,4} ` | Rule codes: `F401`, `I001`, `E501` | ruff |
| `^Found \d+ errors?` | `Found 2 errors.` | ruff |
| `^E\s` | `E   assert -2 == 2` | pytest |
| `^>\s` | `>       assert subtract(5, 3) == 2` (the failing line) | pytest |
| `\b(FAILED\|ERROR)\b` | `FAILED tests/…`, `ERROR collecting tests/…` | pytest |
| `^Traceback\b` | Start of a traceback | Python, pytest |
| `\b\w*(Error\|Exception)\b` | `ImportError`, `AssertionError`, … | Python |
| `\berror during collection\b` | pytest couldn't load a test file | pytest |

Like redaction patterns, they live in **one list**: supporting a new tool means adding a line.

**A bug found while writing this:** the first version required `ERROR ` at the *start* of a line, and `Error:` *with a colon*. On the import log, that missed two of the most useful lines:

```
______________________ ERROR collecting tests/test_ops.py ______________________
ImportError while importing test module '…/tests/test_ops.py'.
```

The first starts with underscores, and the second has no colon. Printing what the extractor actually returned for each real log, and reading it, caught this. Widening the two patterns fixed it, and the evidence for that log grew from 582 to 1,192 characters, now including the whole story.

### 3e. `signal_regions`: context, and merging

A signal line on its own often isn't enough. Compare:

```
F401 [*] `os` imported but unused                 ← the signal alone: which file?
```

```
F401 [*] `os` imported but unused                 ← with 3 lines after it:
 --> src/calculator/ops.py:1:8                       the file and position
  |
1 | import os                                        the actual code
```

So each signal keeps **2 lines before** and **3 lines after** (the `before` and `after` settings). For pytest, the lines *before* matter too: they show `def test_subtract():`, which names the test.

When two regions **touch or overlap**, they merge into one. Otherwise, a traceback with five signal lines in a row would become five overlapping pieces of evidence repeating the same lines.

Finally, `_trim_blank_edges` removes empty lines from the start and end of each region. A signal line is never empty, so a region can never be trimmed to nothing.

### 3f. `extract_failure`: putting it together

For each region:

1. join its messages into one text,
2. **redact** it (Lesson 5.2), which gives the clean text and whether anything was removed,
3. create an `EvidenceItem` (Lesson 5.1) with the next free ID, `source = log`, `location = "test/Lint"`, the region's first and last line numbers, the clean text, and the `redacted` flag,
4. **add it to the bundle**. If the budget is full, `bundle.add` raises `EvidenceBudgetExceeded`; extraction stops and reports `truncated = True`.

**Why stop instead of skipping?** Regions are in log order, and earlier regions usually explain later ones. Keeping the first ones that fit, and saying clearly that some were left out, is more honest than silently picking and choosing.

**IDs continue from what's already there.** The next ID is `E{number of items + 1}`. If the commit diff was added first as `E1`, the log evidence starts at `E2`. Later stages add diff and file evidence to the same bundle.

It returns a `FailureExtract`: the failed job and step, the command, the exit code, the IDs it added, and whether it was truncated. The diagnosis stage (Phase 6) will use all of these.

---

## 4. The tests: `tests/unit/test_log_extract.py`

There are 25 tests. Most use **your three real logs** from Lesson 1.

| Test | What it proves |
|---|---|
| `test_parse_splits_job_step_and_message` | F401 is on line **201**, the same line `grep -n` showed you in Lesson 1 |
| `test_first_line_of_each_step_is_read_correctly` | The invisible character never reaches a message |
| `test_parse_removes_terminal_colour_codes` | No colour codes survive |
| `test_finds_the_first_failed_step` (×3) | Lint/1, Test/2, Test/1, matching what you saw in the Actions tab |
| `test_first_failure_wins_over_later_ones` | Cause beats symptom |
| `test_real_logs_produce_the_right_evidence` (×3) | F401 **and** I001 and the file path; the ImportError; `assert -2 == 2` and the test's name |
| `test_evidence_is_small` (×3) | Under 2,000 characters each |
| `test_setup_noise_is_left_out` (×3) | No checkout, pip install, env settings or warnings |
| `test_every_item_is_well_formed` (×3) | IDs match, source is `log`, location is the failed step, no blank edges |
| `test_nearby_signals_merge_into_one_region` | Touching regions merge |
| `test_distant_signals_stay_separate` | Far-apart regions don't |
| `test_secrets_in_the_failure_are_redacted` | Lesson 5.2 is wired in: a token in an error line is removed and `redacted` is `True` |
| `test_log_with_no_failed_step_adds_nothing` | The "invalid workflow file" case is handled safely |
| `test_budget_stops_extraction_and_reports_truncation` | The budget is enforced and reported |
| `test_new_ids_continue_after_existing_evidence` | `E2` follows an existing `E1` |

The `fake_log(...)` helper builds tiny logs in the real format, for situations your three fixtures don't cover (a secret, no failure, two failed steps).

---

## 5. A bug in your repo that this lesson uncovered

When the extractor was first run, it crashed before doing anything:

```
File "app/tools/__init__.py", line 3, in <module>
    echo("Approved read/execute/publish tools with argument validation.")
_curses.error: must call initscr() first
```

Two of your package files contained this:

```python
from curses import echo

echo("Approved read/execute/publish tools with argument validation.")
```

It looks like a shell command meant to write a description into the file (`echo "..." > app/tools/__init__.py`) was turned into Python code by an editor's auto-import. `curses` is a library for drawing terminal screens, and its `echo()` crashes unless a screen has been set up.

**Why it stayed hidden:** Python only runs a package's `__init__.py` when something imports from that package. Until this lesson, nothing imported from `app/tools/` or `app/orchestration/`, so the crash never happened. CI was green because no test touched those folders.

**The fix** is to make each file a plain docstring: a description Python stores but doesn't run. `app/github/__init__.py` had a smaller problem, a stray `")` at the end of a comment, and it gets the same treatment.

---

## 6. Add everything to your project

**Step 1: fix the three `__init__.py` files.** From the agent project folder, run these three lines. Each one replaces a file's whole content with one docstring line:

```bash
printf '"""Approved read/execute/publish tools with argument validation."""\n' > app/tools/__init__.py
printf '"""LangGraph StateGraph, nodes, routing, checkpointer."""\n' > app/orchestration/__init__.py
printf '"""GitHub App authentication and REST API adapters."""\n' > app/github/__init__.py
```

Check:

```bash
cat app/tools/__init__.py app/orchestration/__init__.py app/github/__init__.py
```

You should see exactly three lines, each starting and ending with `"""`, and no `curses`.

**Step 2: place the lesson files.**

| File | Goes into |
|---|---|
| `log_extract.py` | `app/tools/` |
| `test_log_extract.py` | `tests/unit/` |
| `phase5_3_log_extraction.md` (this file) | `docs/` |

---

## 7. Run the tests and the linter

```bash
pytest tests/unit/test_log_extract.py -v
```

Expect **25 passed**.

```bash
pytest
```

Expect **68 passed** (25 + 23 + 19 + your health test).

```bash
ruff check .
```

Expect `All checks passed!`

**See it work.** This short script runs the extractor on your logic-bug log and prints what the agent would see:

```bash
python -c "
from pathlib import Path
from app.models.evidence import EvidenceBundle
from app.tools.log_extract import extract_failure
bundle = EvidenceBundle(repository='mee-tan/demo-repo-recovery-agent-target', run_id=1, head_sha='a'*40)
result = extract_failure(Path('tests/fixtures/logs/logic_bug.log').read_text(), bundle)
print(result)
for item in bundle.items:
    print('---', item.excerpt_id, item.location, 'lines', item.start_line, '-', item.end_line)
    print(item.text)
"
```

Try it with the other two logs too. Compare what it prints with what you found by hand in Lesson 1.

---

## 8. Update the ground truth

In `benchmark/cases/README.md`, update the C01 row to include both ruff errors:

| Case ID | Branch | Category | Failed step | Root cause | Correct fix | Forbidden changes |
|---|---|---|---|---|---|---|
| C01 | `seed/lint-unused-import` | `lint` | Lint | `ops.py` imports `os` but never uses it, which ruff reports twice: F401 (unused import) and I001 (unsorted import block) | Delete the `import os` line, which fixes both | Disabling F401 or I001; editing `ci.yml` |

**Why this matters:** in Phase 6, the agent's diagnosis is scored against this table. If the table says "only F401" and the agent correctly says "F401 and I001", a careless score would mark the agent wrong. Ground truth has to be checked against the real data too.

---

## 9. Try breaking it

One at a time: make the change in `app/tools/log_extract.py`, run `pytest tests/unit/test_log_extract.py`, note which tests fail, then undo it.

1. **Delete the ruff pattern.** Remove the whole line containing `# ruff rule codes: F401, I001, E501`. Which tests fail? Why does the lint log still produce *some* evidence?
2. **Remove the context.** In **both** places that say `before: int = 2, after: int = 3`, change them to `before: int = 0, after: int = 0`. Five tests fail. Look at which ones: what information goes missing without context?
3. **Stop trimming blank lines.** In `signal_regions`, change `return [_trim_blank_edges(output[start : end + 1]) for` to `return [output[start : end + 1] for`. Which logs now produce evidence with a blank first or last line?

Then check you undid everything:

```bash
git diff app/tools/log_extract.py
```

It should print nothing, because the file is new. Run the tests again: **25 passed**.

---

## 10. Commit

```bash
git status
```

You should see: the three `__init__.py` files **modified**, `benchmark/cases/README.md` **modified**, and three new files. Then:

```bash
git add app/tools/__init__.py app/orchestration/__init__.py app/github/__init__.py
git add app/tools/log_extract.py tests/unit/test_log_extract.py docs/ benchmark/cases/README.md
git status
```

Check everything is listed under "Changes to be committed", then:

```bash
git commit -m "Phase 5.3: log extraction; fix curses imports in package inits"
git push
```

On GitHub, open the new CI run, click the **Test** step, and check it says **68 passed**. That proves CI ran the new tests, not just that it's green.

---

## 11. Known limits (for your report)

- The patterns are tuned for **Python, pytest and ruff**, the MVP's single language. A JavaScript project would need its own patterns added to `ERROR_SIGNALS`.
- If a tool prints its error with no recognisable pattern, extraction finds only the `##[error]` line. The diagnosis stage then has little to go on, and should stop with "diagnosis only" rather than guess.
- When the budget runs out, later regions are dropped, and `truncated = True` says so. The diagnosis stage should treat truncated evidence with lower confidence.

---

## 12. Checklist

- [ ] Three `__init__.py` files fixed (no `curses`)
- [ ] `app/tools/log_extract.py` and `tests/unit/test_log_extract.py` added
- [ ] Ground truth C01 updated
- [ ] `pytest` shows 68 passed; `ruff check .` passes
- [ ] Ran the "see it work" script on all three logs
- [ ] All three "break it" experiments done and undone
- [ ] Committed, pushed, CI's Test step shows 68 passed
- [ ] You can explain: why the first failed step wins; why each signal keeps context; why regions merge; what happens when the budget runs out

**Next:** the rest of Phase 5 (5.4 to 5.6) needs the LangGraph state machine first, so the next lesson is **Stage C: learning LangGraph** and building the recovery graph's skeleton.
