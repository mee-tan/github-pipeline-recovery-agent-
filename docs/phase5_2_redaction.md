# Phase 5.2 Walkthrough — Secret Redaction

How the agent removes passwords, tokens and keys from text before anyone stores it or sends it to the LLM. Read the concepts first, then add the files, run the tests, try breaking it, and commit.

---

## 0. The problem this solves

CI logs sometimes contain secrets. A test prints a config object, a script echoes a URL with a password in it, a library logs the `Authorization` header it sent. If that text reaches the agent:

- it gets sent to the LLM provider (it has left your system),
- it gets written into the audit log and the evidence store,
- and it might be copied into a **public pull request description**.

None of those can be un-sent. So the rule is: **every piece of text is redacted before it becomes evidence**. Nothing downstream ever sees the original.

This is requirement **FR06** ("redact configured secret patterns"), the **Privacy** requirement ("redaction before model calls"), and the threat model's **"Secret leakage"** row.

**What GitHub already does:** GitHub replaces the secrets *it knows about* with `***`. You saw this in your own logs, on line 38: `token: ***`. But GitHub only knows the secrets stored in the repo's settings. A token that a test generates, or one hard-coded by mistake, is printed in full. That's the gap this module fills.

---

## 1. New concept: regular expressions

A **regular expression** (regex) is a pattern that describes what some text looks like. Python's `re` module finds every piece of text that fits the pattern.

The building blocks used in this lesson:

| Piece | Means | Example |
|---|---|---|
| `abc` | exactly these characters | `ghp_` matches "ghp_" |
| `[A-Za-z0-9]` | any **one** character from this set | a letter or digit |
| `[^\s@]` | any one character **not** in this set (`^` inside `[]` = "not") | anything except whitespace and `@` |
| `{36,}` | the thing before, **36 or more** times | `[A-Za-z0-9]{36,}` = 36+ letters/digits |
| `+` | one or more times | `\s+` = one or more spaces |
| `\s` | any whitespace (space, tab, newline) | |
| `\b` | a **word boundary**: the edge between a word character and a non-word character | stops `AKIA` matching inside `XAKIA…` |
| `(?i)` | ignore upper/lower case from here on | `bearer` also matches `Bearer` |
| `(?:a\|b)` | either `a` or `b` (grouped, not saved) | `(?:password\|token)` |
| `(?P<secret>…)` | a **named group**: remember this part as `"secret"` | used to replace only part of a match |
| `(?!…)` | **negative lookahead**: "only match if this does NOT come next" | `(?!\[REDACTED)` |
| `.*?` | any characters, as **few** as possible | used to find the end of a key block |

Read a pattern left to right, like a sentence. For example, the GitHub token pattern:

```
\bgh[pousr]_[A-Za-z0-9]{36,}\b
```

says: *at a word boundary, the letters `gh`, then one of `p o u s r`, then `_`, then 36 or more letters or digits, ending at a word boundary.* That describes GitHub's real token formats: `ghp_` (personal), `gho_` (OAuth), `ghu_`, `ghs_` (server), `ghr_` (refresh).

> **Tip:** paste a pattern and some test text into [regex101.com](https://regex101.com), choose the "Python" flavour, and it explains each piece and highlights what matches.

---

## 2. The file: `app/policy/redaction.py`

### 2a. `SecretPattern`: one kind of secret

```python
@dataclass(frozen=True)
class SecretPattern:
    name: str
    regex: re.Pattern[str]
```

A **dataclass** is a lightweight class for holding data. We use it instead of Pydantic here because this data is written by us, in code, not received from outside, so it doesn't need validating. `frozen=True` again means it can't be changed after it's made.

### 2b. `SECRET_PATTERNS`: the configured list

The proposal says "configured secret patterns", so all patterns live in **one list**. Adding a new kind of secret means adding one line to the list and one test case.

| Name | Catches | Pattern idea |
|---|---|---|
| `private_key` | SSH/RSA/EC private keys | everything from `-----BEGIN … PRIVATE KEY-----` to `-----END … PRIVATE KEY-----` |
| `github_token` | GitHub tokens | `ghp_`/`gho_`/`ghu_`/`ghs_`/`ghr_` + 36 or more characters |
| `github_pat` | GitHub fine-grained tokens | `github_pat_` + 22 or more characters |
| `aws_access_key` | AWS access key IDs | `AKIA` + exactly 16 capitals/digits |
| `api_key` | `sk-` style API keys (several AI providers) | `sk-` + 20 or more characters |
| `bearer_token` | HTTP auth headers | `Bearer` + a long token (only the token is replaced) |
| `url_credentials` | passwords inside URLs | `://user:password@` (only `user:password` is replaced) |
| `assigned_secret` | `password=…`, `token: …`, `api_key=…` | the key word, `=` or `:`, then the value (only the value is replaced) |

**Why the order matters:** the list is applied top to bottom. `private_key` goes first, so the whole key block is replaced in one piece before any smaller pattern can match part of it.

**Why `re.DOTALL` on the private key:** normally `.` matches any character *except a newline*. A key spans several lines, so `DOTALL` lets `.` match newlines too.

### 2c. Replacing only part of a match

Compare these two results:

```
Authorization: Bearer [REDACTED:bearer_token]     ← good: you can still see what it was
Authorization: [REDACTED:bearer_token]            ← worse: the context is lost
```

Keeping the words around a secret helps the agent (and you) understand the log. So patterns that should keep their context put the secret inside a **named group** called `secret`. The `_replace` function checks: if the pattern has a `secret` group, swap only that part; otherwise, swap the whole match.

### 2d. `functools.partial`

```python
pattern.regex.subn(partial(_replace, tag=tag), text)
```

`re.subn` can call a function for each match. That function receives only the match, but `_replace` also needs to know the tag. `partial(_replace, tag=tag)` makes a new function with `tag` already filled in. It's a clean way to pass an extra argument to a callback.

`subn` (with an `n`) returns two things: the new text, **and how many replacements it made**. That number feeds the counts.

### 2e. `RedactionResult`: what you get back

| Field | Example | Used for |
|---|---|---|
| `text` | the cleaned text | becomes `EvidenceItem.text` |
| `counts` | `{"github_token": 2}` | the audit log: what kind of secret was found, never the secret itself |
| `redacted` | `True` | becomes `EvidenceItem.redacted` from Lesson 2 |
| `total` | `2` | quick summary |

This is how Lesson 2 and Lesson 3 connect: the `redacted` field you defined on `EvidenceItem` gets its value from here.

---

## 3. Avoiding false alarms

A redactor that removes too much is also a problem: if it deletes the actual error line, the agent can't diagnose anything. Three design choices prevent that:

1. **Minimum lengths.** `assigned_secret` needs at least 4 characters after `=`, and tokens need 20 to 36+. Short normal words aren't touched.
2. **GitHub's `***` is left alone.** The value can't start with `*`, and three stars are too short anyway.
3. **`(?!\[REDACTED)`**, the bug the tests found (next section).

The tests also run redaction over **all three of your real CI logs** and check that nothing at all changes. Real logs mention `GITHUB_TOKEN` and `token: ***`, so this is a meaningful check.

---

## 4. A bug the tests caught while writing this lesson

The first version of `assigned_secret` had no `(?!\[REDACTED)`. One test runs redaction **twice** on the same text and checks the second run changes nothing. It failed:

```
first run:   token=ghp_x1x1…        →  token=[REDACTED:github_token]
second run:  token=[REDACTED:github_token]  →  token=[REDACTED:assigned_secret]   ← wrong
```

On the second run, the *tag itself* looked like a secret, because it comes after `token=`. The fix is the lookahead `(?!\[REDACTED)`: "don't match if the value is already one of our tags."

**Why "run twice, nothing changes" matters:** in the real system, text may be redacted at more than one point (when collected, and again before a prompt is built). If redaction isn't stable, every pass corrupts the text a little more. This property has a name: the function is **idempotent**.

---

## 5. The tests: `tests/unit/test_redaction.py`

There are 23 tests (7 test functions, several run with multiple inputs).

| Test | What it proves |
|---|---|
| `test_each_kind_of_secret_is_removed` (×8) | Every pattern works: the secret is gone, the right tag appears, the count is 1 |
| `test_bearer_keeps_the_word_bearer` | Only the token is replaced, not the context |
| `test_assignment_keeps_the_key_name` | `password=` stays visible |
| `test_url_keeps_the_host` | The rest of the URL stays readable |
| `test_private_key_is_removed_whole_across_lines` | Multi-line keys are removed in one piece |
| `test_several_secrets_are_all_counted` | Counts are correct with repeats and mixed kinds |
| `test_normal_log_lines_are_unchanged` (×6) | Real-looking lines, including `token: ***`, aren't touched |
| `test_real_ci_logs_have_no_false_alarms` (×3) | Your three real logs come back **exactly** unchanged |
| `test_running_twice_changes_nothing_more` | Idempotent (section 4) |

**Why the fake secrets are built from pieces:** look at the top of the test file:

```python
GITHUB_TOKEN = "ghp_" + "x1" * 18
```

A complete, realistic token written out in a file can trigger **GitHub secret scanning**, which may block your push or raise an alert, even though it's fake. Building it at runtime means the full token never appears in the source. It's the same idea as this lesson: never let a secret-shaped string get committed.

**Why test with real logs:** `test_real_ci_logs_have_no_false_alarms` uses the fixture files you saved in Lesson 1. That's what they were for.

---

## 6. Add the files to your project

| File | Goes into |
|---|---|
| `redaction.py` | `app/policy/` |
| `test_redaction.py` | `tests/unit/` |
| `phase5_2_redaction.md` (this file) | `docs/` |

Check:

```bash
ls app/policy tests/unit docs
```

---

## 7. Run the tests and the linter

```bash
pytest tests/unit/test_redaction.py -v
```

Expect **23 passed**.

```bash
pytest
```

Expect **43 passed** (23 + 19 from Lesson 2 + your health test).

```bash
ruff check .
```

Expect `All checks passed!`

---

## 8. Try breaking it

One at a time: make the change, run `pytest tests/unit/test_redaction.py`, note which tests fail, undo it.

1. **Remove the lookahead.** In `assigned_secret`, delete `(?!\[REDACTED)`. Which test fails? (You already know the story from section 4. Now watch it happen.)
2. **Remove `re.DOTALL`** from the `private_key` pattern (delete the line `re.DOTALL,` and its comment). How many tests fail, and why does the key now go unmatched?
3. **Make the GitHub token pattern stricter.** In `github_token`, change `{36,}` to `{40,}`. Which test fails? What does that tell you about getting a pattern's length rules right?

Then check you undid everything:

```bash
git status
```

`redaction.py` should only appear as untracked (new), and the tests should give **23 passed** again.

---

## 9. Commit

```bash
git status
git add app/policy/redaction.py tests/unit/test_redaction.py docs/
git commit -m "Phase 5.2: secret redaction with idempotent, context-keeping patterns"
git push
```

Check the Actions tab is **green**.

---

## 10. Known limits (write these in your report)

Redaction by pattern can only catch secrets that **look like** something it knows. A random password printed on its own, with no `password=` in front of it, won't be caught. That's why the proposal layers several controls instead of relying on this one:

- **data minimisation**: Lesson 5.3 sends only the few lines around an error, not the whole log, so less text means fewer places for a secret to hide
- **no secrets in the sandbox** (Stage H): the agent's own test runs can't leak what they never had
- **output scanning**: the PR text is redacted again before publishing (Stage I)

This is called **defence in depth**: several layers, each catching what the others miss.

---

## 11. Checklist

- [ ] `app/policy/redaction.py` added
- [ ] `tests/unit/test_redaction.py` added
- [ ] `pytest` shows 43 passed; `ruff check .` passes
- [ ] All three "break it" experiments done and undone
- [ ] Committed, pushed, CI green
- [ ] You can explain: why redaction happens *before* storage; why only the secret part is replaced; what idempotent means and why it matters

**Next lesson: 5.3, log extraction.** It reads your three logs, finds the few lines that matter, redacts them, and packs them into an `EvidenceBundle`. That's the first time Lessons 2 and 3 work together.
