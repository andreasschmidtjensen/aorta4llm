"""Tests for policy pack include system."""

import pytest
import yaml

from aorta4llm.governance.compiler import PACKS_DIR, compile_spec_dict, _resolve_includes
from aorta4llm.governance.validator import validate_spec

from tests.conftest import PACKS_DIR as TEST_PACKS_DIR


class TestResolveIncludes:

    def test_single_pack_loads_norms(self):
        spec = {"include": ["tool-hygiene"]}
        resolved = _resolve_includes(spec)
        assert "include" not in resolved
        norms = resolved["norms"]
        assert len(norms) == 4
        assert all(n["type"] == "forbidden_command" for n in norms)

    def test_multiple_packs_merge(self):
        spec = {"include": ["tool-hygiene", "git-safety"]}
        resolved = _resolve_includes(spec)
        norms = resolved["norms"]
        # tool-hygiene has 4 norms, git-safety has 2
        assert len(norms) == 6

    def test_user_norms_appended_after_pack_norms(self):
        user_norm = {
            "type": "forbidden_command",
            "role": "agent",
            "command_pattern": "rm -rf",
            "severity": "soft",
        }
        spec = {"include": ["git-safety"], "norms": [user_norm]}
        resolved = _resolve_includes(spec)
        norms = resolved["norms"]
        # git-safety has 2 norms, user adds 1
        assert len(norms) == 3
        # User norm is last
        assert norms[-1]["command_pattern"] == "rm -rf"

    def test_user_access_overrides_pack_access(self):
        # Packs currently don't have access entries, so test the logic directly
        spec = {
            "include": ["tool-hygiene"],
            "access": {"src/": "read-write"},
        }
        resolved = _resolve_includes(spec)
        assert resolved["access"] == {"src/": "read-write"}

    def test_unknown_pack_raises_error(self):
        spec = {"include": ["nonexistent-pack"]}
        with pytest.raises(ValueError, match="Unknown policy pack"):
            _resolve_includes(spec)

    def test_no_include_returns_spec_unchanged(self):
        spec = {"organization": "test", "roles": {}}
        resolved = _resolve_includes(spec)
        assert resolved == spec

    def test_empty_include_returns_spec_unchanged(self):
        spec = {"organization": "test", "roles": {}, "include": []}
        resolved = _resolve_includes(spec)
        # include is falsy (empty list), so spec is returned as-is
        assert resolved == spec


class TestCompileWithIncludes:

    def test_tool_hygiene_compiles_to_cond_facts(self):
        spec = compile_spec_dict({
            "include": ["tool-hygiene"],
        })
        cond_facts = [f for f in spec.facts if f.startswith("cond(")]
        assert len(cond_facts) == 4
        assert all("execute_command(Cmd)" in f for f in cond_facts)

    def test_git_safety_compiles_soft_norms(self):
        spec = compile_spec_dict({
            "include": ["git-safety"],
        })
        soft_facts = [f for f in spec.facts if f.startswith("soft_norm(")]
        assert len(soft_facts) == 2

    def test_include_with_user_norms_and_access(self):
        spec = compile_spec_dict({
            "include": ["git-safety"],
            "roles": {
                "agent": {
                    "objectives": ["task_complete"],
                    "capabilities": ["read_file", "write_file", "execute_command"],
                },
            },
            "access": {
                "src/": "read-write",
            },
            "norms": [
                {"role": "agent", "type": "readonly", "paths": ["config/"]},
            ],
        })
        # Should have facts from git-safety + scope + readonly
        cond_facts = [f for f in spec.facts if f.startswith("cond(")]
        assert len(cond_facts) > 2  # at least git-safety + scope + readonly

    def test_backward_compatible_no_include(self):
        spec = compile_spec_dict({
            "roles": {
                "agent": {
                    "objectives": ["x"],
                    "capabilities": ["read_file"],
                },
            },
        })
        assert any("role(agent" in f for f in spec.facts)


class TestPackFilesValid:

    @pytest.mark.parametrize("pack_name", ["tool-hygiene", "git-safety"])
    def test_pack_yaml_is_loadable(self, pack_name):
        pack_path = TEST_PACKS_DIR / f"{pack_name}.yaml"
        assert pack_path.exists()
        with open(pack_path) as f:
            data = yaml.safe_load(f)
        assert isinstance(data, dict)
        assert "norms" in data
        assert isinstance(data["norms"], list)
        assert len(data["norms"]) > 0


class TestValidatorInclude:

    def test_valid_include(self):
        r = validate_spec({
            "organization": "test",
            "roles": {"agent": {"objectives": ["x"], "capabilities": ["read_file"]}},
            "include": ["tool-hygiene"],
        })
        assert r.valid
        assert any("tool-hygiene" in s for s in r.summary)

    def test_unknown_pack_is_validation_error(self):
        r = validate_spec({
            "organization": "test",
            "roles": {"agent": {"objectives": ["x"], "capabilities": ["read_file"]}},
            "include": ["does-not-exist"],
        })
        assert not r.valid
        assert any("does-not-exist" in e for e in r.errors)

    def test_include_not_a_list(self):
        r = validate_spec({
            "organization": "test",
            "roles": {"agent": {"objectives": ["x"], "capabilities": ["read_file"]}},
            "include": "tool-hygiene",
        })
        assert not r.valid
        assert any("list" in e for e in r.errors)

    def test_no_include_still_valid(self):
        r = validate_spec({
            "organization": "test",
            "roles": {"agent": {"objectives": ["x"], "capabilities": ["read_file"]}},
        })
        assert r.valid
