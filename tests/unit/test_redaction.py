"""Tests for app/policy/redaction.py (Phase 5.2).

The fake secrets below are built from pieces at runtime ("ghp_" + "x" * 36) instead
of being written out whole. A complete, realistic token in a committed file can make
GitHub's secret scanning block the push, so we never write one, even a fake one.
"""

from pathlib import Path

import pytest

from app.policy.redaction import redact

FIXTURES = Path(__file__).parents[1] / "fixtures" / "logs"

# Fake secrets, assembled at runtime.
GITHUB_TOKEN = "ghp_" + "x1" * 18
GITHUB_PAT = "github_" + "pat_" + "A1b2" * 6
AWS_KEY = "AKIA" + "ABCD1234" * 2
API_KEY = "sk-" + "test" * 6
BEARER = "eyJ" + "a1B2c3" * 5
PRIVATE_KEY = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    + "MIIEow" + "Ab12" * 10 + "\n"
    + "Zz98" * 10 + "\n"
    + "-----END RSA PRIVATE KEY-----"
)


# ---------- each kind of secret is found and removed ----------

@pytest.mark.parametrize(
    ("kind", "line", "secret"),
    [
        ("github_token", f"Using token {GITHUB_TOKEN} for clone", GITHUB_TOKEN),
        ("github_pat", f"export GH={GITHUB_PAT}", GITHUB_PAT),
        ("aws_access_key", f"aws key is {AWS_KEY} ok", AWS_KEY),
        ("api_key", f"OPENAI={API_KEY}", API_KEY),
        ("bearer_token", f"Authorization: Bearer {BEARER}", BEARER),
        ("private_key", f"key:\n{PRIVATE_KEY}\nend", "MIIEow"),
        ("url_credentials", "cloning https://bot:hunter2pass@example.com/repo.git", "hunter2pass"),
        ("assigned_secret", "db password=SuperSecret99 loaded", "SuperSecret99"),
    ],
)
def test_each_kind_of_secret_is_removed(kind, line, secret):
    result = redact(line)
    assert secret not in result.text
    assert f"[REDACTED:{kind}]" in result.text
    assert result.counts.get(kind) == 1
    assert result.redacted is True


# ---------- only the secret is removed, the context stays ----------

def test_bearer_keeps_the_word_bearer():
    result = redact(f"Authorization: Bearer {BEARER} sent")
    assert result.text == "Authorization: Bearer [REDACTED:bearer_token] sent"


def test_assignment_keeps_the_key_name():
    result = redact("password=SuperSecret99")
    assert result.text == "password=[REDACTED:assigned_secret]"


def test_url_keeps_the_host():
    result = redact("https://bot:hunter2pass@example.com/repo.git")
    assert result.text == "https://[REDACTED:url_credentials]@example.com/repo.git"


def test_private_key_is_removed_whole_across_lines():
    result = redact(f"before\n{PRIVATE_KEY}\nafter")
    assert result.text == "before\n[REDACTED:private_key]\nafter"


# ---------- counting ----------

def test_several_secrets_are_all_counted():
    text = f"{GITHUB_TOKEN} and {AWS_KEY} and {GITHUB_TOKEN}"
    result = redact(text)
    assert result.counts == {"github_token": 2, "aws_access_key": 1}
    assert result.total == 3


# ---------- no false alarms ----------

@pytest.mark.parametrize(
    "line",
    [
        "F401 [*] `os` imported but unused",
        "E       assert -2 == 2",
        "  token: ***",                       # GitHub's own mask must be left alone
        "##[group]GITHUB_TOKEN Permissions",  # mentions a token, contains none
        "Process completed with exit code 1.",
        "https://github.com/mee-tan/demo-repo-recovery-agent-target",
    ],
)
def test_normal_log_lines_are_unchanged(line):
    result = redact(line)
    assert result.text == line
    assert result.redacted is False


@pytest.mark.parametrize(
    "name", ["lint_unused_import.log", "import_error.log", "logic_bug.log"]
)
def test_real_ci_logs_have_no_false_alarms(name):
    text = (FIXTURES / name).read_text(encoding="utf-8")
    result = redact(text)
    assert result.counts == {}
    assert result.text == text


def test_running_twice_changes_nothing_more():
    once = redact(f"token={GITHUB_TOKEN}")
    twice = redact(once.text)
    assert twice.text == once.text
    assert twice.redacted is False
