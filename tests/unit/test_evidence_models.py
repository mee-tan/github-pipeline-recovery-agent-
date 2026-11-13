"""Tests for app/models/evidence.py (Phase 5.1)."""

import pytest
from pydantic import ValidationError

from app.models.evidence import (
    EvidenceBudgetExceeded,
    EvidenceBundle,
    EvidenceItem,
    EvidenceSource,
    sha256_text,
)

# A real line from tests/fixtures/logs/lint_unused_import.log
LINT_LINE = "F401 [*] `os` imported but unused"
SHA = "a" * 40  # a fake but correctly shaped commit SHA


def make_item(excerpt_id: str = "E1", text: str = LINT_LINE) -> EvidenceItem:
    return EvidenceItem.create(
        excerpt_id=excerpt_id,
        source=EvidenceSource.LOG,
        location="test/Lint",
        start_line=201,
        end_line=201,
        text=text,
    )


def make_bundle(max_total_chars: int = 20_000) -> EvidenceBundle:
    return EvidenceBundle(
        repository="mee-tan/demo-repo-recovery-agent-target",
        run_id=12345,
        head_sha=SHA,
        max_total_chars=max_total_chars,
    )


# ---------- sha256_text ----------

def test_same_text_gives_same_hash():
    assert sha256_text("hello") == sha256_text("hello")


def test_one_character_changes_the_hash():
    assert sha256_text("hello") != sha256_text("hellO")


# ---------- EvidenceItem ----------

def test_create_fills_in_the_hash():
    item = make_item()
    assert item.content_hash == sha256_text(LINT_LINE)


def test_wrong_hash_is_rejected():
    with pytest.raises(ValidationError, match="content_hash does not match"):
        EvidenceItem(
            excerpt_id="E1",
            source=EvidenceSource.LOG,
            location="test/Lint",
            start_line=1,
            end_line=1,
            text=LINT_LINE,
            content_hash=sha256_text("something else"),
        )


def test_end_line_before_start_line_is_rejected():
    with pytest.raises(ValidationError, match="end_line"):
        EvidenceItem.create(
            excerpt_id="E1",
            source=EvidenceSource.LOG,
            location="test/Lint",
            start_line=10,
            end_line=5,
            text=LINT_LINE,
        )


@pytest.mark.parametrize("bad_id", ["1", "e1", "E", "E1a", "evidence1"])
def test_badly_formed_excerpt_id_is_rejected(bad_id):
    with pytest.raises(ValidationError):
        make_item(excerpt_id=bad_id)


def test_unknown_source_is_rejected():
    with pytest.raises(ValidationError):
        EvidenceItem.create(
            excerpt_id="E1",
            source="email",  # not one of log / diff / file
            location="x",
            start_line=1,
            end_line=1,
            text="x",
        )


def test_items_cannot_be_changed_after_creation():
    item = make_item()
    with pytest.raises(ValidationError):
        item.text = "tampered"


# ---------- EvidenceBundle ----------

def test_add_and_get():
    bundle = make_bundle()
    bundle.add(make_item("E1"))
    assert bundle.get("E1") is not None
    assert bundle.get("E1").text == LINT_LINE


def test_get_unknown_id_returns_none():
    assert make_bundle().get("E99") is None


def test_total_chars_counts_all_items():
    bundle = make_bundle()
    bundle.add(make_item("E1", text="abc"))
    bundle.add(make_item("E2", text="defgh"))
    assert bundle.total_chars == 8


def test_duplicate_excerpt_id_is_rejected():
    bundle = make_bundle()
    bundle.add(make_item("E1"))
    with pytest.raises(ValueError, match="duplicate"):
        bundle.add(make_item("E1"))


def test_item_over_budget_is_rejected_and_not_added():
    bundle = make_bundle(max_total_chars=10)
    bundle.add(make_item("E1", text="12345"))
    with pytest.raises(EvidenceBudgetExceeded):
        bundle.add(make_item("E2", text="123456"))  # 5 + 6 = 11 > 10
    assert bundle.get("E2") is None
    assert bundle.total_chars == 5


def test_item_exactly_at_budget_is_allowed():
    bundle = make_bundle(max_total_chars=10)
    bundle.add(make_item("E1", text="1234567890"))
    assert bundle.total_chars == 10


def test_bad_head_sha_is_rejected():
    with pytest.raises(ValidationError):
        EvidenceBundle(repository="a/b", run_id=1, head_sha="not-a-sha")
