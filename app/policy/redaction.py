"""Secret redaction (Phase 5.2).

Every piece of text from a log, diff or file passes through redact() BEFORE it is
stored as evidence or sent to the model. That is requirement FR06 and the proposal's
"Secret leakage" threat: a credential that reaches the model can end up in a prompt,
a pull request description or the audit log, and none of those can be un-sent.

GitHub already masks the secrets it knows about as "***". This module catches the
ones it doesn't know about, for example a token that a test prints by accident.
"""

import re
from dataclasses import dataclass, field
from functools import partial


@dataclass(frozen=True)
class SecretPattern:
    """One kind of secret: a name for reports, and a regular expression that finds it.

    If the regex has a group named "secret", only that part is replaced and the rest
    of the match is kept (so "password=abc" becomes "password=[REDACTED:...]").
    Otherwise the whole match is replaced.
    """

    name: str
    regex: re.Pattern[str]


# The configured patterns. Order matters: the multi-line private key goes first,
# so no smaller pattern can cut a key block in half before it is matched whole.
# To add a new kind of secret, add one line here and one test case.
SECRET_PATTERNS: tuple[SecretPattern, ...] = (
    SecretPattern(
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,  # let "." match newlines, because a key spans many lines
        ),
    ),
    SecretPattern("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    SecretPattern("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}")),
    SecretPattern("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    SecretPattern("api_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    SecretPattern(
        "bearer_token",
        re.compile(r"(?i)\bbearer\s+(?P<secret>[A-Za-z0-9._~+/=-]{20,})"),
    ),
    SecretPattern(
        "url_credentials",
        re.compile(r"://(?P<secret>[^/\s:@]+:[^/\s@]+)@"),
    ),
    SecretPattern(
        "assigned_secret",
        re.compile(
            r"(?i)\b(?:password|passwd|pwd|secret|token|api[_-]?key)\s*[=:]\s*"
            # (?!\[REDACTED) skips a value that is already one of our tags, so running
            # redact() twice changes nothing; the first character must not be "*", so
            # GitHub's own "***" mask is left alone.
            r"(?!\[REDACTED)(?P<secret>[^\s'\"*][^\s'\"]{3,})"
        ),
    ),
)


@dataclass(frozen=True)
class RedactionResult:
    """The cleaned text, plus how many secrets of each kind were removed."""

    text: str
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def redacted(self) -> bool:
        """True if anything was removed. This becomes EvidenceItem.redacted."""
        return bool(self.counts)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def _replace(match: re.Match[str], tag: str) -> str:
    """Build the replacement for one match."""
    if "secret" not in match.re.groupindex:
        return tag
    # Keep the text around the secret: rebuild the match with only the secret swapped.
    whole = match.group(0)
    start = match.start("secret") - match.start()
    end = match.end("secret") - match.start()
    return whole[:start] + tag + whole[end:]


def redact(
    text: str, patterns: tuple[SecretPattern, ...] = SECRET_PATTERNS
) -> RedactionResult:
    """Replace every secret in text with [REDACTED:<kind>] and count what was removed."""
    counts: dict[str, int] = {}
    for pattern in patterns:
        tag = f"[REDACTED:{pattern.name}]"
        text, found = pattern.regex.subn(partial(_replace, tag=tag), text)
        if found:
            counts[pattern.name] = found
    return RedactionResult(text=text, counts=counts)
