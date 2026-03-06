"""Tests for the YAML -> Prolog compiler."""

import pytest

from governance.compiler import CompiledSpec, compile_org_spec, compile_spec_dict


@pytest.fixture
def minimal_spec():
    return {
        "organization": "test_org",
        "roles": {
            "worker": {
                "objectives": ["task_done(T)"],
                "capabilities": ["read_file", "write_file"],
            }
        },
    }


@pytest.fixture
def full_spec():
    return {
        "organization": "full_org",
        "roles": {
            "implementer": {
                "objectives": ["feature_implemented(F)", "tests_passing(F)"],
                "capabilities": ["read_file", "write_file", "execute_command"],
            },
            "reviewer": {
                "objectives": ["code_reviewed(F)"],
                "capabilities": ["read_file"],
            },
        },
        "dependencies": [
            {
                "role": "reviewer",
                "depends_on": "implementer",
                "for": "feature_implemented(F)",
            }
        ],
        "norms": [
            {
                "role": "implementer",
                "type": "forbidden",
                "objective": "write_file(Path)",
                "deadline": "false",
                "condition": "not(in_scope(Path, AssignedScope))",
            }
        ],
        "rules": [
            "in_scope(Path, Scope) :- current_scope(Scope), atom_concat(Scope, _, Path)."
        ],
    }


class TestCompileRoles:
    def test_single_role_produces_role_fact(self, minimal_spec):
        spec = compile_spec_dict(minimal_spec)
        assert "role(worker, [task_done(T)])" in spec.facts

    def test_capabilities_produce_cap_facts(self, minimal_spec):
        spec = compile_spec_dict(minimal_spec)
        assert "cap(worker, read_file)" in spec.facts
        assert "cap(worker, write_file)" in spec.facts

    def test_objectives_produce_obj_facts(self, minimal_spec):
        spec = compile_spec_dict(minimal_spec)
        assert "obj(task_done(T), [])" in spec.facts

    def test_multiple_roles(self, full_spec):
        spec = compile_spec_dict(full_spec)
        role_facts = [f for f in spec.facts if f.startswith("role(")]
        assert len(role_facts) == 2


class TestCompileDependencies:
    def test_dependency_produces_dep_fact(self, full_spec):
        spec = compile_spec_dict(full_spec)
        assert "dep(reviewer, implementer, feature_implemented(F))" in spec.facts

    def test_no_dependencies_is_fine(self, minimal_spec):
        spec = compile_spec_dict(minimal_spec)
        dep_facts = [f for f in spec.facts if f.startswith("dep(")]
        assert len(dep_facts) == 0


class TestCompileNorms:
    def test_prohibition_produces_cond_fact(self, full_spec):
        spec = compile_spec_dict(full_spec)
        cond_facts = [f for f in spec.facts if f.startswith("cond(")]
        assert len(cond_facts) == 1
        assert "cond(implementer, forbidden, write_file(Path), false, not(in_scope(Path, AssignedScope)))" in spec.facts


class TestCompileRules:
    def test_rules_passed_through(self, full_spec):
        spec = compile_spec_dict(full_spec)
        assert len(spec.rules) == 1
        assert spec.rules[0].endswith(".")

    def test_rule_gets_trailing_dot_if_missing(self):
        spec = compile_spec_dict({
            "rules": ["foo(X) :- bar(X)"]
        })
        assert spec.rules[0] == "foo(X) :- bar(X)."


class TestCompileEmpty:
    def test_empty_spec(self):
        spec = compile_spec_dict({})
        assert spec.facts == []
        assert spec.rules == []

    def test_empty_roles(self):
        spec = compile_spec_dict({"roles": {}})
        assert spec.facts == []


class TestHighLevelNorms:
    def test_scope_emits_cond_fact(self):
        spec = compile_spec_dict({
            "norms": [{"role": "agent", "type": "scope", "paths": ["src/"]}]
        })
        assert any(
            "cond(agent, forbidden, write_file(Path), false, not(in_scope(Path, 'src/')))" in f
            for f in spec.facts
        )

    def test_scope_adds_in_scope_rule(self):
        spec = compile_spec_dict({
            "norms": [{"role": "agent", "type": "scope", "paths": ["src/"]}]
        })
        assert "in_scope(Path, Scope) :- atom_concat(Scope, _, Path)." in spec.rules

    def test_scope_deduplicates_in_scope_rule(self):
        spec = compile_spec_dict({
            "norms": [
                {"role": "agent", "type": "scope", "paths": ["src/"]},
                {"role": "agent", "type": "scope", "paths": ["lib/"]},
            ]
        })
        in_scope_rules = [r for r in spec.rules if r.startswith("in_scope(")]
        assert len(in_scope_rules) == 1

    def test_scope_normalises_trailing_slash(self):
        spec_with = compile_spec_dict({
            "norms": [{"role": "agent", "type": "scope", "paths": ["src/"]}]
        })
        spec_without = compile_spec_dict({
            "norms": [{"role": "agent", "type": "scope", "paths": ["src"]}]
        })
        assert spec_with.facts == spec_without.facts

    def test_protected_emits_read_and_write_cond(self):
        spec = compile_spec_dict({
            "norms": [{"role": "agent", "type": "protected", "paths": [".env"]}]
        })
        cond_facts = [f for f in spec.facts if f.startswith("cond(")]
        assert len(cond_facts) == 2
        assert any("read_file(Path)" in f for f in cond_facts)
        assert any("write_file(Path)" in f for f in cond_facts)

    def test_protected_multiple_paths(self):
        spec = compile_spec_dict({
            "norms": [{"role": "agent", "type": "protected", "paths": [".env", "secrets/"]}]
        })
        cond_facts = [f for f in spec.facts if f.startswith("cond(")]
        # 2 facts per path (read + write)
        assert len(cond_facts) == 4

    def test_readonly_emits_one_cond_per_path(self):
        spec = compile_spec_dict({
            "norms": [
                {"role": "agent", "type": "readonly",
                 "paths": ["config/", ".env", "secrets/"]}
            ]
        })
        cond_facts = [f for f in spec.facts if f.startswith("cond(")]
        assert len(cond_facts) == 3

    def test_readonly_uses_atom_concat(self):
        spec = compile_spec_dict({
            "norms": [
                {"role": "agent", "type": "readonly", "paths": [".env"]}
            ]
        })
        assert any("atom_concat('.env', _, Path)" in f for f in spec.facts)

    def test_required_before_emits_cond_fact(self):
        spec = compile_spec_dict({
            "norms": [{
                "role": "agent",
                "type": "required_before",
                "blocks": "execute_command",
                "command_pattern": "git commit",
                "requires": "tests_passing",
            }]
        })
        cond_facts = [f for f in spec.facts if "execute_command(Cmd)" in f]
        assert len(cond_facts) == 1
        assert "not(achieved(tests_passing))" in cond_facts[0]

    def test_required_before_emits_helper_rules(self):
        spec = compile_spec_dict({
            "norms": [{
                "role": "agent",
                "type": "required_before",
                "command_pattern": "git commit",
                "requires": "tests_passing",
            }]
        })
        helper_rules = [r for r in spec.rules if "git commit" in r]
        assert len(helper_rules) == 2  # prefix + suffix match

    def test_required_before_stable_helper_name(self):
        # Same pattern → same helper name across two compilations
        spec1 = compile_spec_dict({
            "norms": [{
                "role": "a", "type": "required_before",
                "command_pattern": "git commit", "requires": "x",
            }]
        })
        spec2 = compile_spec_dict({
            "norms": [{
                "role": "b", "type": "required_before",
                "command_pattern": "git commit", "requires": "y",
            }]
        })
        helper1 = [f for f in spec1.facts if "execute_command" in f][0]
        helper2 = [f for f in spec2.facts if "execute_command" in f][0]
        # Both should reference the same helper name (derived from sha1 of pattern)
        import re as _re
        name1 = _re.search(r"cmd_matches_\w+", helper1).group()
        name2 = _re.search(r"cmd_matches_\w+", helper2).group()
        assert name1 == name2

    def test_raw_norm_syntax_unchanged(self):
        spec = compile_spec_dict({
            "norms": [{
                "role": "agent",
                "type": "forbidden",
                "objective": "write_file(Path)",
                "deadline": "false",
                "condition": "not(in_scope(Path, Scope))",
            }]
        })
        assert "cond(agent, forbidden, write_file(Path), false, not(in_scope(Path, Scope)))" in spec.facts


class TestCompileFromFile:
    def test_compile_code_review_yaml(self):
        from pathlib import Path

        yaml_path = Path(__file__).parent.parent.parent / "org-specs" / "code_review.yaml"
        spec = compile_org_spec(yaml_path)
        assert len(spec.facts) > 0
        assert len(spec.rules) > 0
        # Check key facts exist
        assert any("role(implementer" in f for f in spec.facts)
        assert any("cap(implementer, write_file)" in f for f in spec.facts)
        assert any("cond(implementer, forbidden" in f for f in spec.facts)
