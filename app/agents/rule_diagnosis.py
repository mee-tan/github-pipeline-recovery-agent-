"""A simple rule-based diagnosis: a stand-in until Phase 6 adds the LLM.

It looks for well-known error markers in the extracted evidence. It is deliberately
cautious, and it is useful later too: the benchmark can compare the LLM against it.
Its answer has the same shape as the Phase 6 diagnosis schema.
"""

import re
from typing import Any

from app.models.evidence import EvidenceBundle
from app.policy.config import CONFIDENCE_THRESHOLD, SUPPORTED_CATEGORIES

RUFF_CODE = re.compile(r"^[A-Z]{1,4}\d{3,4} ", re.MULTILINE)


def rule_based_diagnose(extract: dict[str, Any], bundle: EvidenceBundle) -> dict[str, Any]:
    text = "\n".join(item.text for item in bundle.items)
    cited = [item.excerpt_id for item in bundle.items]

    if extract.get("failed_step") == "Lint" and RUFF_CODE.search(text):
        category, confidence, cause = "lint", 0.9, "ruff reported rule violations"
    elif re.search(r"\b(ImportError|ModuleNotFoundError|SyntaxError)\b", text):
        category, confidence, cause = "syntax", 0.8, "a module or name could not be imported"
    elif re.search(r"\bAssertionError\b|^E\s+assert ", text, re.MULTILINE):
        category, confidence, cause = "unit_test", 0.75, "a test assertion failed"
    else:
        category, confidence, cause = "unsupported", 0.2, "no known failure pattern found"

    safe = category in SUPPORTED_CATEGORIES and confidence >= CONFIDENCE_THRESHOLD
    return {
        "failure_category": category,
        "root_cause": cause,
        "evidence": cited,
        "confidence": confidence,
        "safe_to_attempt_patch": safe,
        "stop_reason": None if safe else f"{category} failure, confidence {confidence}",
        "diagnosed_by": "rules",
    }
