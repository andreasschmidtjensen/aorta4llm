"""Tests for governance.validator — org spec validation."""

import pytest
from pathlib import Path

from aorta4llm.governance.validator import validate_spec, validate_spec_file

from tests.conftest import TEMPLATES_DIR as _TEMPLATES_DIR


class TestValidateSpec:

    def test_valid_minimal_spec(self):
        r = validate_spec({
            "organization": "test",
            "roles": {"agent": {"objectives": ["x"], "capabilities": ["read_file"]}},
        })
        assert r.valid
        assert len(r.errors) == 0

    def test_missing_organization(self):
        r = validate_spec({"roles": {"agent": {}}})
        assert not r.valid
        assert any("organization" in e for e in r.errors)

    def test_missing_roles(self):
        r = validate_spec({"organization": "test"})
        assert not r.valid
        assert any("roles" in e for e in r.errors)

    def test_norm_references_undefined_role(self):
        r = validate_spec({
            "organization": "test",
            "roles": {"agent": {"objectives": [], "capabilities": []}},
            "norms": [{"role": "ghost", "type": "scope", "paths": ["src/"]}],
        })
        assert not r.valid
        assert any("ghost" in e for e in r.errors)

    def test_invalid_norm_type(self):
        r = validate_spec({
            "organization": "test",
            "roles": {"agent": {"objectives": [], "capabilities": []}},
            "norms": [{"role": "agent", "type": "banana"}],
        })
        assert not r.valid
        assert any("banana" in e for e in r.errors)

    def test_scope_missing_paths(self):
        r = validate_spec({
            "organization": "test",
            "roles": {"agent": {"objectives": [], "capabilities": []}},
            "norms": [{"role": "agent", "type": "scope"}],
        })
        assert not r.valid
        assert any("paths" in e for e in r.errors)

    def test_protected_missing_paths(self):
        r = validate_spec({
            "organization": "test",
            "roles": {"agent": {"objectives": [], "capabilities": []}},
            "norms": [{"role": "agent", "type": "protected"}],
        })
        assert not r.valid
        assert any("paths" in e for e in r.errors)

    def test_readonly_missing_paths(self):
        r = validate_spec({
            "organization": "test",
            "roles": {"agent": {"objectives": [], "capabilities": []}},
            "norms": [{"role": "agent", "type": "readonly"}],
        })
        assert not r.valid
        assert any("paths" in e for e in r.errors)

    def test_required_before_missing_requires(self):
        r = validate_spec({
            "organization": "test",
            "roles": {"agent": {"objectives": [], "capabilities": []}},
            "norms": [{"role": "agent", "type": "required_before"}],
        })
        assert not r.valid
        assert any("requires" in e for e in r.errors)

    def test_unrecognized_capability_is_warning(self):
        r = validate_spec({
            "organization": "test",
            "roles": {"agent": {"objectives": [], "capabilities": ["fly"]}},
        })
        assert r.valid  # warnings don't make it invalid
        assert any("fly" in w for w in r.warnings)

    def test_dependency_references_undefined_role(self):
        r = validate_spec({
            "organization": "test",
            "roles": {"agent": {"objectives": [], "capabilities": []}},
            "dependencies": [{"role": "ghost", "depends_on": "agent"}],
        })
        assert not r.valid
        assert any("ghost" in e for e in r.errors)

    def test_summary_lists_roles_and_norms(self):
        r = validate_spec({
            "organization": "test",
            "roles": {"agent": {"objectives": ["x"], "capabilities": ["read_file"]}},
            "norms": [{"role": "agent", "type": "scope", "paths": ["src/"]}],
        })
        assert any("agent" in s for s in r.summary)
        assert any("scope" in s for s in r.summary)


class TestValidateSpecFile:

    def test_file_not_found(self, tmp_path):
        r = validate_spec_file(tmp_path / "nope.yaml")
        assert not r.valid
        assert any("not found" in e for e in r.errors)

    def test_invalid_yaml(self, tmp_path):
        f = tmp_path / "bad.yaml"
        f.write_text("just a string")
        r = validate_spec_file(f)
        assert not r.valid

    @pytest.mark.parametrize("template", ["safe-agent", "test-gate"])
    def test_existing_templates_are_valid(self, template):
        path = _TEMPLATES_DIR / f"{template}.yaml"
        r = validate_spec_file(path)
        assert r.valid, f"{template}: {r.errors}"
