"""Log extraction (Phase 5.3): find the few lines that explain a CI failure.

A failed job's log is about 230 lines, and roughly 10 of them matter. This module:

1. parses each line of a `gh run view --log` file into job, step and message,
2. finds the FIRST step that failed (later steps are skipped symptoms, not causes),
3. finds error signals inside that step's output,
4. keeps a few lines of context around each signal, merging regions that touch,
5. redacts each region (Lesson 5.2) and adds it to the EvidenceBundle (Lesson 5.1),
   stopping when the budget is full.
"""

import re
from dataclasses import dataclass

from app.models.evidence import (
    EvidenceBudgetExceeded,
    EvidenceBundle,
    EvidenceItem,
    EvidenceSource,
)
from app.policy.redaction import redact

ANSI_COLOUR = re.compile(r"\x1b\[[0-9;]*m")     # terminal colour codes such as ^[[36;1m
STEP_FAILED = re.compile(r"^##\[error\]Process completed with exit code (\d+)")
RUN_GROUP = re.compile(r"^##\[group\]Run (.+)")

# Lines that signal an error. Each pattern describes one kind of tool output.
ERROR_SIGNALS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^##\[error\]"),                           # GitHub's own error marker
    re.compile(r"^[A-Z]{1,4}\d{3,4} "),                     # ruff rule codes: F401, I001, E501
    re.compile(r"^Found \d+ errors?"),                      # ruff summary
    re.compile(r"^E\s"),                                    # pytest explanation lines
    re.compile(r"^>\s"),                                    # pytest: the line that failed
    re.compile(r"\b(FAILED|ERROR)\b"),                      # pytest: FAILED x, ERROR collecting x
    re.compile(r"^Traceback\b"),                            # Python and pytest traceback start
    re.compile(r"\b\w*(Error|Exception)\b"),                # ImportError, AssertionError, ...
    re.compile(r"\berror during collection\b"),             # pytest could not load a test file
)


@dataclass(frozen=True)
class LogLine:
    """One parsed log line. `number` is its line number in the file (from 1)."""

    number: int
    job: str
    step: str
    message: str


@dataclass(frozen=True)
class FailureExtract:
    """What extraction found: where the job failed, and which evidence it added."""

    failed_job: str | None
    failed_step: str | None
    command: str | None          # e.g. "ruff check ."
    exit_code: int | None
    excerpt_ids: tuple[str, ...]  # IDs of the EvidenceItems added to the bundle
    truncated: bool               # True if the budget ran out before all regions fit


def parse_log(text: str) -> list[LogLine]:
    """Split a `gh run view --log` file into LogLines. Lines that don't fit are skipped."""
    lines: list[LogLine] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        parts = raw.split("\t", 2)               # job \t step \t timestamp message
        if len(parts) != 3:
            continue
        job, step, rest = parts
        # The first line of each step has an invisible byte-order mark before the
        # timestamp. We split at the first space and throw the timestamp away, so the
        # mark goes with it and never reaches the message.
        _timestamp, _, message = rest.partition(" ")
        message = ANSI_COLOUR.sub("", message)
        lines.append(LogLine(number=number, job=job, step=step, message=message))
    return lines


def find_failed_step(lines: list[LogLine]) -> tuple[str, str, int] | None:
    """Return (job, step, exit_code) of the FIRST step that failed, or None."""
    for line in lines:
        match = STEP_FAILED.match(line.message)
        if match:
            return line.job, line.step, int(match.group(1))
    return None


def step_output(lines: list[LogLine], job: str, step: str) -> tuple[str | None, list[LogLine]]:
    """Return the step's command and its output, without the command's header group.

    Every step begins with a header like:
        ##[group]Run ruff check .
        ...env settings...
        ##[endgroup]
    The header tells us the command but is not output, so it is skipped.
    """
    step_lines = [line for line in lines if line.job == job and line.step == step]
    command = None
    output_start = 0
    if step_lines:
        header = RUN_GROUP.match(step_lines[0].message)
        if header:
            command = header.group(1).strip()
            for index, line in enumerate(step_lines):
                if line.message.startswith("##[endgroup]"):
                    output_start = index + 1
                    break
    return command, step_lines[output_start:]


def is_signal(message: str) -> bool:
    return any(pattern.search(message) for pattern in ERROR_SIGNALS)


def signal_regions(
    output: list[LogLine], before: int = 2, after: int = 3
) -> list[list[LogLine]]:
    """Group the output into regions: each signal plus context, merged when they touch."""
    regions: list[tuple[int, int]] = []          # (first index, last index) into output
    for index, line in enumerate(output):
        if not is_signal(line.message):
            continue
        start = max(0, index - before)
        end = min(len(output) - 1, index + after)
        if regions and start <= regions[-1][1] + 1:   # touches the previous region
            regions[-1] = (regions[-1][0], max(regions[-1][1], end))
        else:
            regions.append((start, end))
    return [_trim_blank_edges(output[start : end + 1]) for start, end in regions]


def _trim_blank_edges(region: list[LogLine]) -> list[LogLine]:
    """Drop empty lines at the start and end of a region. A signal line is never empty."""
    while not region[0].message.strip():
        region = region[1:]
    while not region[-1].message.strip():
        region = region[:-1]
    return region


def extract_failure(
    log_text: str, bundle: EvidenceBundle, before: int = 2, after: int = 3
) -> FailureExtract:
    """Find the failure in a log and add its key regions to the bundle as evidence."""
    lines = parse_log(log_text)
    failed = find_failed_step(lines)
    if failed is None:
        # No step reported a failure (for example, the workflow file itself was invalid).
        return FailureExtract(None, None, None, None, (), False)

    job, step, exit_code = failed
    command, output = step_output(lines, job, step)
    added: list[str] = []
    truncated = False

    for region in signal_regions(output, before, after):
        result = redact("\n".join(line.message for line in region))
        item = EvidenceItem.create(
            excerpt_id=f"E{len(bundle.items) + 1}",
            source=EvidenceSource.LOG,
            location=f"{job}/{step}",
            start_line=region[0].number,
            end_line=region[-1].number,
            text=result.text,
            redacted=result.redacted,
        )
        try:
            bundle.add(item)
        except EvidenceBudgetExceeded:
            truncated = True
            break
        added.append(item.excerpt_id)

    return FailureExtract(job, step, command, exit_code, tuple(added), truncated)
