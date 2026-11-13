# Phase 5.1 Walkthrough — Evidence Models

What a piece of "evidence" is in code, and why it is built this way. Read the concepts first, then add the two files, run the tests, and commit.

---

## 0. The problem this solves

In Lesson 1 you found the error by reading the log yourself, and you could say exactly where you saw it: _"line 201 of the Lint step says F401."_ The agent must be able to do the same.

The proposal requires that **every diagnosis cites its evidence** (requirement FR07, acceptance criterion AC3, the "evidence grounding" metric). A diagnosis like this is not allowed:

> "The code probably has an unused import somewhere."

This is:

> "Unused import of `os` in `ops.py`. Evidence: **E1**."

…where **E1** is a stored piece of evidence the agent actually collected. For that to work, every piece of evidence needs:

1. **An ID**, so it can be cited (`E1`, `E2`, …)
2. **Where it came from**, so a human can go and check it
3. **The text itself**
4. **A fingerprint (hash)**, to prove the text wasn't changed after it was collected
5. **A limit on the total size**, so we never send an entire 24 KB log to the LLM

This lesson builds exactly those five things. It is plain Python with no GitHub or LLM involved yet.

---

## 1. New concept: Pydantic models

A **Pydantic model** is a Python class that describes the _shape_ of some data, and **checks the data automatically** when an object is created.

Without Pydantic:

```python
item = {"excerpt_id": "E1", "start_line": -5}   # nobody stops this
```

With Pydantic:

```python
class EvidenceItem(BaseModel):
    excerpt_id: str
    start_line: int = Field(ge=1)    # ge = "greater than or equal to"

EvidenceItem(excerpt_id="E1", start_line=-5)    # raises ValidationError
```

Bad data is rejected **at the moment it's created**, with an error saying which field is wrong. It can't sneak further into the system and cause a confusing failure later.

You already have Pydantic installed: it's in `pyproject.toml`, and FastAPI uses it internally.

---

## 2. New concept: hashing

A **hash** (here, SHA-256) turns any text into a 64-character "fingerprint":

```python
sha256_text("hello")   # '2cf24dba5fb0a30e26e83b2ac5b9e29e...'
sha256_text("hellO")   # '9a0f5a1b...'  ← completely different
```

Two properties matter:

- The **same text always gives the same hash**.
- Changing **even one character** gives a completely different hash.

So when we collect evidence, we store its hash next to it. Later, anyone can recompute the hash. If it still matches, the evidence is exactly what was collected. This is what the proposal means by "evidence hashes" in the audit store.

---

## 3. The file: `app/models/evidence.py`

It contains four things. Here's what each one is for.

### 3a. `sha256_text(text)`

A tiny helper that returns the fingerprint of some text. It's a separate function so the model and the tests both use exactly the same calculation.

### 3b. `EvidenceSource`, the three allowed origins

```python
class EvidenceSource(StrEnum):
    LOG = "log"
    DIFF = "diff"
    FILE = "file"
```

An **enum** is a fixed list of allowed values. Evidence can only come from a log, the commit diff, or a source file. Anything else (for example `"email"`) is rejected. This matches the proposal's evidence collection list: logs, commit diff, and selected repository files.

`StrEnum` means each value is also a normal string, so it shows up as `"log"` when saved as JSON.

### 3c. `EvidenceItem`, one piece of evidence

| Field                    | Example                                 | Rule                           | Why                                                                   |
| ------------------------ | --------------------------------------- | ------------------------------ | --------------------------------------------------------------------- |
| `excerpt_id`             | `"E1"`                                  | Must match `E` + digits        | The diagnosis cites evidence by this ID                               |
| `source`                 | `log`                                   | Must be log / diff / file      | See 3b                                                                |
| `location`               | `"test/Lint"`                           | Not empty                      | Tells a human where to look: job/step for logs, a file path for files |
| `start_line`, `end_line` | `201`, `201`                            | ≥ 1, and end ≥ start           | Line range inside that location                                       |
| `text`                   | ``"F401 [*] `os` imported but unused"`` | Not empty                      | The evidence itself                                                   |
| `redacted`               | `False`                                 | —                              | `True` if secrets were removed from `text` (Lesson 5.2)               |
| `content_hash`           | `"9e3c…"`                               | Must equal `sha256_text(text)` | Tamper check                                                          |

Three details worth understanding:

**`model_config = ConfigDict(frozen=True)`**: once an item is created, it **cannot be changed**. Writing `item.text = "something else"` raises an error. Evidence is a record of what was observed. If it could be edited afterwards, the audit trail would be meaningless.

**`@model_validator(mode="after")`**: some rules involve _two_ fields at once, so they can't be written on a single field. "End line must not be before start line" compares two fields. "Hash must match the text" also compares two. `mode="after"` means this check runs after each field has passed its own checks.

**`EvidenceItem.create(...)`**: the normal way to make an item. You pass in the text, and it calculates the hash for you, so you can't accidentally store a wrong one. Building an `EvidenceItem(...)` directly with a hash still works (for example, when loading saved evidence back from the database), but then the validator checks the hash.

### 3d. `EvidenceBundle`, all evidence for one run

| Field             | Example                                     | Why                                                                                     |
| ----------------- | ------------------------------------------- | --------------------------------------------------------------------------------------- |
| `repository`      | `"mee-tan/demo-repo-recovery-agent-target"` | Which repo the run belongs to                                                           |
| `run_id`          | `19234567890`                               | The run ID you used with `gh run view`                                                  |
| `head_sha`        | 40 hex characters                           | The **exact commit** that failed. The proposal requires everything to use the exact SHA |
| `max_total_chars` | `20000`                                     | The **budget**: the maximum total text allowed                                          |
| `items`           | list of `EvidenceItem`                      | The evidence itself                                                                     |

And three methods:

- **`total_chars`**: how much of the budget has been used.
- **`add(item)`**: adds an item, but **refuses** it if:
  - its ID is already used (two different "E1"s would make citations ambiguous), or
  - it would push the total over budget. This raises `EvidenceBudgetExceeded`, and the item is **not** added.
- **`get(excerpt_id)`**: finds an item by ID, or returns `None`. In Phase 6, this is how we check that the evidence the model cites actually exists.

**Why a budget?** Your logs are about 24 KB each, but the useful part is about 10 lines. Sending everything to the model costs more, is slower, and makes it _more_ likely the model gets distracted by setup noise. The budget forces the collector to choose. This is requirement FR06 ("limit the size of evidence sent to the model").

---

## 4. The tests: `tests/unit/test_evidence_models.py`

There are 19 tests (15 test functions, one of which runs 5 times with different inputs). Each one checks **one rule**, and its name says which:

| Test                                              | What it proves                                         |
| ------------------------------------------------- | ------------------------------------------------------ |
| `test_same_text_gives_same_hash`                  | Hashing is consistent                                  |
| `test_one_character_changes_the_hash`             | Hashing detects tiny changes                           |
| `test_create_fills_in_the_hash`                   | `create()` calculates the hash correctly               |
| `test_wrong_hash_is_rejected`                     | Tampered evidence can't be loaded                      |
| `test_end_line_before_start_line_is_rejected`     | Line ranges make sense                                 |
| `test_badly_formed_excerpt_id_is_rejected` (×5)   | Only `E1`, `E2`… are valid IDs                         |
| `test_unknown_source_is_rejected`                 | Only log / diff / file are allowed                     |
| `test_items_cannot_be_changed_after_creation`     | `frozen=True` works                                    |
| `test_add_and_get`                                | The basic happy path                                   |
| `test_get_unknown_id_returns_none`                | Citing a missing ID is detectable                      |
| `test_total_chars_counts_all_items`               | The budget counter is right                            |
| `test_duplicate_excerpt_id_is_rejected`           | IDs stay unique                                        |
| `test_item_over_budget_is_rejected_and_not_added` | The budget is enforced, and the bundle stays unchanged |
| `test_item_exactly_at_budget_is_allowed`          | The boundary: exactly at the limit is OK               |
| `test_bad_head_sha_is_rejected`                   | Only real commit SHAs are accepted                     |

Two testing ideas to notice:

- **`pytest.raises(...)`** checks that something _fails_ in the expected way. Many of these tests check that bad input is rejected, not just that good input works. For a safety system, the rejections matter most.
- **`@pytest.mark.parametrize`** runs the same test with several inputs. That's why one function counts as 5 tests.
- **Boundary tests**: "over budget is rejected" and "exactly at budget is allowed" together pin down _exactly_ where the limit is. Bugs often hide at boundaries (`>` vs `>=`).

The test data uses a real line from your `lint_unused_import.log`.

---

## 5. Add the files to your project

1. Copy `evidence.py` into `app/models/` in the agent project.
2. Copy `test_evidence_models.py` into `tests/unit/`.

Check they're in the right place:

```bash
ls app/models tests/unit
```

You should see `evidence.py` in the first list and `test_evidence_models.py` in the second.

---

## 6. Run the tests and the linter

Make sure your virtual environment is active (you'll see `(.venv)` in your prompt), and that you're in the agent project folder.

```bash
pytest tests/unit/test_evidence_models.py -v
```

`-v` ("verbose") prints each test's name and result on its own line. Expect **19 passed**.

Then run all tests, to check nothing else broke:

```bash
pytest
```

Expect **20 passed** (19 new + your health test).

Then the linter:

```bash
ruff check .
```

Expect `All checks passed!`

---

## 7. Try breaking it (recommended)

The best way to understand a rule is to break it and watch the test catch it. Try one at a time, run `pytest tests/unit/test_evidence_models.py`, then undo the change:

1. In `evidence.py`, delete `frozen=True` from `ConfigDict(...)`. Which test fails?
2. In `add()`, change `>` to `>=` in the budget check. Which test fails, and why that one?
3. In `EvidenceSource`, add `EMAIL = "email"`. Which test fails?

Afterwards, run `git diff` to confirm you undid everything. It should print nothing for `evidence.py`.

---

## 8. Commit

```bash
git status
```

Check that only `app/models/evidence.py` and `tests/unit/test_evidence_models.py` are listed, plus this walkthrough if you saved it into `docs/`. Then:

```bash
git add app/models/evidence.py tests/unit/test_evidence_models.py docs/
git commit -m "Phase 5.1: evidence models with hashing and size budget"
git push
```

Check the Actions tab: your agent repo's CI should be **green**.

---

## 9. Checklist

- [ ] `app/models/evidence.py` added
- [ ] `tests/unit/test_evidence_models.py` added
- [ ] `pytest` shows 20 passed
- [ ] `ruff check .` passes
- [ ] Tried at least one "break it" experiment
- [ ] Committed, pushed, and CI is green
- [ ] You can explain: why evidence has an ID, why it has a hash, why the bundle has a budget

**Next lesson: 5.2, secret redaction.** A function that removes tokens and passwords from text _before_ it becomes an `EvidenceItem`. That's what the `redacted` field is for.
