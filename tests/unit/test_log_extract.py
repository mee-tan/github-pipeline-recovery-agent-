"""Tests for app/tools/log_extract.py (Phase 5.3).

Most tests use the three real CI logs saved in Lesson 1, so they check the extractor
against what GitHub actually produces, not against what we think it produces.
"""

from pathlib import Path

import pytest

from app.models.evidence import EvidenceBundle, EvidenceItem, EvidenceSource
from app.tools.log_extract import extract_failure, find_failed_step, parse_log, signal_regions

FIXTURES = Path(__file__).parents[1] / "fixtures" / "logs"


def load(name: str) -> str:
    return (FIXTURES / f"{name}.log").read_text(encoding="utf-8")


def new_bundle(max_total_chars: int = 20_000) -> EvidenceBundle:
    return EvidenceBundle(
        repository="mee-tan/demo-repo-recovery-agent-target",
        run_id=1,
        head_sha="a" * 40,
        max_total_chars=max_total_chars,
    )


def all_text(bundle: EvidenceBundle) -> str:
    return "\n".join(item.text for item in bundle.items)


def fake_log(*lines: tuple[str, str]) -> str:
    """Build a small log in the real format from (step, message) pairs."""
    return "\n".join(f"test\t{step}\t2026-11-12T23:05:38.0000000Z {msg}" for step, msg in lines)


# ---------- parsing ----------

def test_parse_splits_job_step_and_message():
    lines = parse_log(load("lint_unused_import"))
    line = next(line for line in lines if "F401" in line.message)
    assert line.job == "test"
    assert line.step == "Lint"
    assert line.message.startswith("F401")      # timestamp removed
    assert line.number == 201                   # same number grep -n showed in Lesson 1


def test_first_line_of_each_step_is_read_correctly():
    # This line starts with an invisible byte-order mark in the real file.
    lines = parse_log(load("lint_unused_import"))
    group_lines = [line for line in lines if "Run ruff check" in line.message]
    assert group_lines[0].message == "##[group]Run ruff check ."
    assert "﻿" not in group_lines[0].message


def test_parse_removes_terminal_colour_codes():
    for line in parse_log(load("logic_bug")):
        assert "\x1b" not in line.message


# ---------- which step failed ----------

@pytest.mark.parametrize(
    ("name", "step", "exit_code"),
    [
        ("lint_unused_import", "Lint", 1),
        ("import_error", "Test", 2),
        ("logic_bug", "Test", 1),
    ],
)
def test_finds_the_first_failed_step(name, step, exit_code):
    assert find_failed_step(parse_log(load(name))) == ("test", step, exit_code)


def test_first_failure_wins_over_later_ones():
    log = fake_log(
        ("Lint", "##[error]Process completed with exit code 1."),
        ("Test", "##[error]Process completed with exit code 2."),
    )
    assert find_failed_step(parse_log(log)) == ("test", "Lint", 1)


# ---------- what each real log produces ----------

@pytest.mark.parametrize(
    ("name", "step", "command", "must_contain"),
    [
        ("lint_unused_import", "Lint", "ruff check .", ["F401", "I001", "src/calculator/ops.py"]),
        ("import_error", "Test", "pytest", ["ImportError", "cannot import name 'subtract'"]),
        ("logic_bug", "Test", "pytest", ["assert -2 == 2", "def test_subtract"]),
    ],
)
def test_real_logs_produce_the_right_evidence(name, step, command, must_contain):
    bundle = new_bundle()
    result = extract_failure(load(name), bundle)
    assert result.failed_step == step
    assert result.command == command
    assert result.truncated is False
    text = all_text(bundle)
    for expected in must_contain:
        assert expected in text


@pytest.mark.parametrize("name", ["lint_unused_import", "import_error", "logic_bug"])
def test_evidence_is_small(name):
    bundle = new_bundle()
    extract_failure(load(name), bundle)
    assert 0 < bundle.total_chars < 2_000      # each full log is about 24,000


@pytest.mark.parametrize("name", ["lint_unused_import", "import_error", "logic_bug"])
def test_setup_noise_is_left_out(name):
    bundle = new_bundle()
    extract_failure(load(name), bundle)
    text = all_text(bundle)
    for noise in ["actions/checkout", "pip install", "pythonLocation", "##[warning]"]:
        assert noise not in text


@pytest.mark.parametrize("name", ["lint_unused_import", "import_error", "logic_bug"])
def test_every_item_is_well_formed(name):
    bundle = new_bundle()
    result = extract_failure(load(name), bundle)
    assert result.excerpt_ids == tuple(item.excerpt_id for item in bundle.items)
    for item in bundle.items:
        assert item.source is EvidenceSource.LOG
        assert item.location == f"test/{result.failed_step}"
        assert item.start_line <= item.end_line
        text_lines = item.text.split("\n")
        assert text_lines[0].strip() and text_lines[-1].strip()   # no blank first/last line


# ---------- regions ----------

def test_nearby_signals_merge_into_one_region():
    log = fake_log(
        ("Test", "E   first"),
        ("Test", "between"),
        ("Test", "E   second"),
    )
    regions = signal_regions(parse_log(log))
    assert len(regions) == 1


def test_distant_signals_stay_separate():
    lines = [("Test", "E   first")] + [("Test", f"filler {i}") for i in range(10)]
    lines += [("Test", "E   second")]
    regions = signal_regions(parse_log(fake_log(*lines)))
    assert len(regions) == 2


# ---------- safety and limits ----------

def test_secrets_in_the_failure_are_redacted():
    secret = "ghp_" + "x1" * 18
    log = fake_log(
        ("Test", "##[group]Run pytest"),
        ("Test", "##[endgroup]"),
        ("Test", f"E   RuntimeError: bad token {secret}"),
        ("Test", "##[error]Process completed with exit code 1."),
    )
    bundle = new_bundle()
    extract_failure(log, bundle)
    assert secret not in all_text(bundle)
    assert bundle.items[0].redacted is True


def test_log_with_no_failed_step_adds_nothing():
    log = fake_log(("Set up job", "Current runner version: 2.338.2"))
    bundle = new_bundle()
    result = extract_failure(log, bundle)
    assert result.failed_step is None
    assert bundle.items == []


def test_budget_stops_extraction_and_reports_truncation():
    bundle = new_bundle(max_total_chars=150)
    result = extract_failure(load("lint_unused_import"), bundle)
    assert result.truncated is True
    assert bundle.total_chars <= 150
    assert len(result.excerpt_ids) == len(bundle.items) < 3


def test_new_ids_continue_after_existing_evidence():
    bundle = new_bundle()
    bundle.add(
        EvidenceItem.create(
            excerpt_id="E1",
            source=EvidenceSource.DIFF,
            location="src/calculator/ops.py",
            start_line=1,
            end_line=1,
            text="+import os",
        )
    )
    result = extract_failure(load("logic_bug"), bundle)
    assert result.excerpt_ids == ("E2",)
