"""Tests for the per-directory access map feature."""

import yaml
import pytest

from aorta4llm.governance.compiler import compile_spec_dict, _expand_access_map
from aorta4llm.governance.validator import validate_spec
from aorta4llm.governance.service import GovernanceService
from aorta4llm.integration.hooks import GovernanceHook


class TestExpandAccessMap:
    """Tests for _expand_access_map converting access entries to norms."""

    def test_read_write_becomes_scope(self):
        norms = _expand_access_map({"src/": "read-write"})
        assert len(norms) == 1
        assert norms[0]["type"] == "scope"
        assert norms[0]["paths"] == ["src/"]

    def test_read_only_becomes_readonly(self):
        norms = _expand_access_map({"config/": "read-only"})
        assert len(norms) == 1
        assert norms[0]["type"] == "readonly"
        assert norms[0]["paths"] == ["config/"]

    def test_no_access_becomes_protected(self):
        norms = _expand_access_map({".env": "no-access"})
        assert len(norms) == 1
        assert norms[0]["type"] == "protected"
        assert norms[0]["paths"] == [".env"]

    def test_mixed_access_levels(self):
        norms = _expand_access_map({
            "src/": "read-write",
            "tests/": "read-write",
            "config/": "read-only",
            ".env": "no-access",
            "secrets/": "no-access",
        })
        types = {n["type"] for n in norms}
        assert types == {"scope", "readonly", "protected"}
        scope = next(n for n in norms if n["type"] == "scope")
        assert set(scope["paths"]) == {"src/", "tests/"}
        protected = next(n for n in norms if n["type"] == "protected")
        assert set(protected["paths"]) == {".env", "secrets/"}

    def test_empty_access_map(self):
        norms = _expand_access_map({})
        assert norms == []

    def test_default_role_is_agent(self):
        norms = _expand_access_map({"src/": "read-write"})
        assert norms[0]["role"] == "agent"


class TestAccessMapCompilation:
    """Tests for access map being compiled into governance facts."""

    def test_access_map_produces_cond_facts(self):
        spec = compile_spec_dict({
            "access": {
                "src/": "read-write",
                ".env": "no-access",
            },
        })
        cond_facts = [f for f in spec.facts if f.startswith("cond(")]
        # scope: 1 cond, protected: 2 conds (read + write)
        assert len(cond_facts) == 3

    def test_access_map_merged_with_explicit_norms(self):
        spec = compile_spec_dict({
            "access": {"src/": "read-write"},
            "norms": [{"role": "agent", "type": "readonly", "paths": ["config/"]}],
        })
        cond_facts = [f for f in spec.facts if f.startswith("cond(")]
        # scope: 1 cond + readonly: 1 cond
        assert len(cond_facts) == 2


class TestAccessMapValidation:
    """Tests for access map validation."""

    def test_valid_access_map(self):
        r = validate_spec({
            "organization": "test",
            "roles": {"agent": {"objectives": ["x"], "capabilities": ["read_file"]}},
            "access": {"src/": "read-write", ".env": "no-access"},
        })
        assert r.valid
        assert any("access" in s for s in r.summary)

    def test_invalid_access_level(self):
        r = validate_spec({
            "organization": "test",
            "roles": {"agent": {"objectives": ["x"], "capabilities": ["read_file"]}},
            "access": {"src/": "banana"},
        })
        assert not r.valid
        assert any("banana" in e for e in r.errors)


class TestAccessMapEndToEnd:
    """End-to-end tests using access map through the hook layer."""

    def _make_hook(self, tmp_path, access, norms=None):
        spec_dict = {
            "organization": "access_test",
            "roles": {
                "agent": {
                    "objectives": ["task_complete"],
                    "capabilities": ["read_file", "write_file", "execute_command"],
                }
            },
            "access": access,
        }
        if norms:
            spec_dict["norms"] = norms
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(yaml.dump(spec_dict))
        hook = GovernanceHook(spec_file, state_path=tmp_path / "state.json")
        hook.register_agent("dev", "agent")
        return hook

    def test_write_in_scope_approved(self, tmp_path):
        hook = self._make_hook(tmp_path, {"src/": "read-write"})
        result = hook.pre_tool_use(
            {"tool_name": "Write", "tool_input": {"file_path": "src/app.py"}},
            agent="dev",
        )
        assert result["decision"] == "approve"

    def test_write_out_of_scope_blocked(self, tmp_path):
        hook = self._make_hook(tmp_path, {"src/": "read-write"})
        result = hook.pre_tool_use(
            {"tool_name": "Write", "tool_input": {"file_path": "README.md"}},
            agent="dev",
        )
        assert result["decision"] == "block"

    def test_no_access_blocks_read(self, tmp_path):
        hook = self._make_hook(tmp_path, {".env": "no-access"})
        result = hook.pre_tool_use(
            {"tool_name": "Read", "tool_input": {"file_path": ".env"}},
            agent="dev",
        )
        assert result["decision"] == "block"

    def test_read_only_allows_read(self, tmp_path):
        hook = self._make_hook(tmp_path, {"config/": "read-only"})
        result = hook.pre_tool_use(
            {"tool_name": "Read", "tool_input": {"file_path": "config/db.yml"}},
            agent="dev",
        )
        assert result["decision"] == "approve"

    def test_read_only_blocks_write(self, tmp_path):
        hook = self._make_hook(tmp_path, {"config/": "read-only"})
        result = hook.pre_tool_use(
            {"tool_name": "Write", "tool_input": {"file_path": "config/db.yml"}},
            agent="dev",
        )
        assert result["decision"] == "block"

    def test_read_only_block_reason_is_specific(self, tmp_path):
        """When both scope and read-only match, reason should mention prefix, not scope."""
        hook = self._make_hook(tmp_path, {
            "src/": "read-write",
            "config/": "read-only",
        })
        result = hook.pre_tool_use(
            {"tool_name": "Write", "tool_input": {"file_path": "config/db.yml"}},
            agent="dev",
        )
        assert result["decision"] == "block"
        assert "forbidden prefix" in result["reason"]
        assert "outside allowed scope" not in result["reason"]
