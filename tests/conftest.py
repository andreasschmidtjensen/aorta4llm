"""Shared test fixtures."""

from pathlib import Path

# Root of the source package
SRC_ROOT = Path(__file__).parent.parent / "src" / "aorta4llm"

# Org spec fixtures
ORG_SPECS_DIR = SRC_ROOT / "org-specs"
TEMPLATES_DIR = ORG_SPECS_DIR / "templates"
CODE_REVIEW_SPEC = ORG_SPECS_DIR / "code_review.yaml"
