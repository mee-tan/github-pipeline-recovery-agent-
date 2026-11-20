"""Policy settings, decided before any model code (proposal Appendix D).

Every limit the agent obeys lives here, in one place, so a reviewer can see them all
and change them without hunting through the code.
"""

# Diagnosis: below this confidence the agent writes a report instead of patching.
CONFIDENCE_THRESHOLD = 0.7

# Evidence: the most text (in characters) that may be collected for one recovery.
MAX_EVIDENCE_CHARS = 20_000
MAX_DIFF_CHARS_PER_FILE = 4_000  # one changed file's patch, at most

# Failure categories the agent may try to fix. Anything else gets a report only.
SUPPORTED_CATEGORIES = frozenset({"lint", "syntax", "unit_test"})

# Patches: limits checked before anything is validated (used from Stage G).
MAX_PATCH_FILES = 3
MAX_PATCH_LINES = 40
PROTECTED_PATHS = (".github/workflows/", "pyproject.toml", ".env")

# Validation: the only commands the sandbox may run (used from Stage H).
VALIDATION_COMMANDS = (("ruff", "check", "."), ("pytest",))
