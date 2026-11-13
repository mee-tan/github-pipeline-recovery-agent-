"""Evidence models: what one piece of evidence looks like, and how many fit in one run.

Every claim the agent makes in a diagnosis must point at an EvidenceItem by its
excerpt_id (for example "E3"). That is how the proposal's "evidence grounding"
metric can be checked: if the diagnosis cites "E7" and there is no E7, the
diagnosis is invalid.
"""

import hashlib
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


def sha256_text(text: str) -> str:
    """Return the SHA-256 fingerprint of a piece of text, as 64 hex characters.

    The same text always gives the same fingerprint. Changing even one
    character gives a completely different one.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class EvidenceSource(StrEnum):
    """Where a piece of evidence came from. Only these three values are allowed."""

    LOG = "log"    # lines from a CI job log
    DIFF = "diff"  # part of the commit diff that triggered the run
    FILE = "file"  # part of a source file in the repository


class EvidenceItem(BaseModel):
    """One small, citable piece of evidence.

    frozen=True means an item cannot be changed after it is created.
    Evidence is a record of what was observed; editing it later would break the audit trail.
    """

    model_config = ConfigDict(frozen=True)

    excerpt_id: str = Field(pattern=r"^E[0-9]+$")  # "E1", "E2", ... used for citations
    source: EvidenceSource
    location: str = Field(min_length=1)            # e.g. "test/Lint" or "src/calculator/ops.py"
    start_line: int = Field(ge=1)                  # ge=1: line numbers start at 1
    end_line: int = Field(ge=1)
    text: str = Field(min_length=1)
    redacted: bool = False                         # True if secrets were removed from text
    content_hash: str

    @model_validator(mode="after")
    def check_consistency(self) -> "EvidenceItem":
        """Rules that involve more than one field at once."""
        if self.end_line < self.start_line:
            raise ValueError("end_line must not be before start_line")
        if self.content_hash != sha256_text(self.text):
            raise ValueError("content_hash does not match text")
        return self

    @classmethod
    def create(
        cls,
        excerpt_id: str,
        source: EvidenceSource,
        location: str,
        start_line: int,
        end_line: int,
        text: str,
        redacted: bool = False,
    ) -> "EvidenceItem":
        """The normal way to make an item: the hash is calculated for you."""
        return cls(
            excerpt_id=excerpt_id,
            source=source,
            location=location,
            start_line=start_line,
            end_line=end_line,
            text=text,
            redacted=redacted,
            content_hash=sha256_text(text),
        )


class EvidenceBudgetExceeded(Exception):
    """Raised when adding an item would make the bundle bigger than its budget."""


class EvidenceBundle(BaseModel):
    """All the evidence collected for one failed workflow run."""

    repository: str = Field(pattern=r"^[\w.-]+/[\w.-]+$")  # "owner/name"
    run_id: int = Field(ge=1)
    head_sha: str = Field(pattern=r"^[0-9a-f]{40}$")      # the exact commit that failed
    max_total_chars: int = Field(default=20_000, ge=1)    # budget for text sent to the model
    items: list[EvidenceItem] = Field(default_factory=list)

    @property
    def total_chars(self) -> int:
        """How much of the budget is used so far."""
        return sum(len(item.text) for item in self.items)

    def add(self, item: EvidenceItem) -> None:
        """Add one item, refusing duplicates and anything over budget."""
        if self.get(item.excerpt_id) is not None:
            raise ValueError(f"duplicate excerpt_id: {item.excerpt_id}")
        if self.total_chars + len(item.text) > self.max_total_chars:
            raise EvidenceBudgetExceeded(
                f"adding {item.excerpt_id} ({len(item.text)} chars) would exceed "
                f"the budget of {self.max_total_chars} chars"
            )
        self.items.append(item)

    def get(self, excerpt_id: str) -> EvidenceItem | None:
        """Find an item by its ID, or return None if there is no such item."""
        for item in self.items:
            if item.excerpt_id == excerpt_id:
                return item
        return None
