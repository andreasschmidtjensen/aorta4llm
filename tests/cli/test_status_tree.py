"""Tests for aorta status --tree output."""

import json
from pathlib import Path

import yaml

from aorta4llm.cli.cmd_status import run, run_tree, run_graph


class TestStatusTree:

    def _make_args(self, org_spec, events_path=None, tree=True):
        class Args:
            pass
        a = Args()
        a.org_spec = str(org_spec)
        a.events_path = str(events_path) if events_path else None
        a.json_output = False
        a.tree = tree
        a.graph = False
        return a

    def _setup_spec(self, tmp_path, spec_dict):
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(yaml.dump(spec_dict, sort_keys=False))
        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps({"events": [], "exceptions": []}))
        return spec_file

    def test_basic_tree_output(self, capsys):
        """Tree shows org name, role, access, and norms."""
        spec = {
            "organization": "test_org",
            "roles": {"agent": {
                "objectives": ["task_complete"],
                "capabilities": ["read_file", "write_file"],
            }},
            "access": {"src/": "read-write", ".env": "no-access"},
            "norms": [{
                "type": "forbidden_command",
                "role": "agent",
                "command_pattern": "git push",
                "severity": "soft",
            }],
        }
        run_tree(spec, achievements=[], packs=[])
        out = capsys.readouterr().out

        assert "test_org" in out
        assert "Role: agent" in out
        assert "Objectives: task_complete" in out
        assert "Capabilities: read_file, write_file" in out
        assert "src/" in out
        assert "read-write" in out
        assert ".env" in out
        assert "no-access" in out
        assert "[soft] git push" in out

    def test_achievements_empty_markers(self, capsys):
        """Unachieved objectives show empty circle."""
        spec = {
            "organization": "test",
            "roles": {"agent": {"objectives": ["task_complete"], "capabilities": []}},
            "achievement_triggers": [{
                "tool": "Bash",
                "command_pattern": "pytest",
                "exit_code": 0,
                "marks": "tests_passing",
            }],
        }
        run_tree(spec, achievements=[], packs=[])
        out = capsys.readouterr().out

        assert "○ tests_passing" in out
        assert "○ task_complete" in out

    def test_achievements_filled_markers(self, capsys):
        """Achieved objectives show filled circle."""
        spec = {
            "organization": "test",
            "roles": {"agent": {"objectives": ["task_complete"], "capabilities": []}},
            "achievement_triggers": [{
                "tool": "Bash",
                "command_pattern": "pytest",
                "exit_code": 0,
                "marks": "tests_passing",
            }],
        }
        run_tree(spec, achievements=["tests_passing"], packs=[])
        out = capsys.readouterr().out

        assert "● tests_passing" in out
        assert "○ task_complete" in out

    def test_achievement_trigger_details(self, capsys):
        """Trigger details shown in parentheses."""
        spec = {
            "organization": "test",
            "roles": {"agent": {"objectives": [], "capabilities": []}},
            "achievement_triggers": [{
                "tool": "Bash",
                "command_pattern": "pytest",
                "exit_code": 0,
                "marks": "tests_passing",
                "reset_on_file_change": True,
            }],
        }
        run_tree(spec, achievements=[], packs=[])
        out = capsys.readouterr().out

        assert "pytest" in out
        assert "exit 0" in out
        assert "resets on change" in out

    def test_required_before_norm(self, capsys):
        """required_before norms display correctly."""
        spec = {
            "organization": "test",
            "roles": {"agent": {"objectives": [], "capabilities": []}},
            "norms": [{
                "type": "required_before",
                "role": "agent",
                "command_pattern": "git commit",
                "requires": "tests_passing",
            }],
        }
        run_tree(spec, achievements=[], packs=[])
        out = capsys.readouterr().out

        assert "requires tests_passing before git commit" in out

    def test_pack_provenance_shown(self, tmp_path, capsys):
        """Norms from packs show their provenance."""
        # Create a fake pack
        packs_dir = tmp_path / "packs"
        packs_dir.mkdir()
        pack = {
            "norms": [{
                "type": "forbidden_command",
                "role": "agent",
                "command_pattern": "grep",
                "severity": "soft",
            }],
        }
        (packs_dir / "my-pack.yaml").write_text(yaml.dump(pack))

        spec = {
            "organization": "test",
            "roles": {"agent": {"objectives": [], "capabilities": []}},
            "norms": [{
                "type": "forbidden_command",
                "role": "agent",
                "command_pattern": "grep",
                "severity": "soft",
            }],
        }

        # Patch the pack loading to use our tmp dir
        import aorta4llm.cli.cmd_status as mod
        from aorta4llm.cli.cmd_init import TEMPLATES_DIR
        original_fn = mod._load_pack_norms

        def _mock_load(pack_name):
            pack_path = packs_dir / f"{pack_name}.yaml"
            if pack_path.exists():
                pack_data = yaml.safe_load(pack_path.read_text()) or {}
                return pack_data.get("norms", [])
            return []

        mod._load_pack_norms = _mock_load
        try:
            run_tree(spec, achievements=[], packs=["my-pack"])
            out = capsys.readouterr().out
            assert "(from my-pack)" in out
        finally:
            mod._load_pack_norms = original_fn

    def test_packs_line_shown(self, capsys):
        """Packs section shown when includes exist."""
        spec = {
            "organization": "test",
            "roles": {"agent": {"objectives": [], "capabilities": []}},
        }
        run_tree(spec, achievements=[], packs=["tool-hygiene", "git-safety"])
        out = capsys.readouterr().out

        assert "Packs: tool-hygiene, git-safety" in out

    def test_no_packs_line_when_empty(self, capsys):
        """No packs section when no includes."""
        spec = {
            "organization": "test",
            "roles": {"agent": {"objectives": [], "capabilities": []}},
        }
        run_tree(spec, achievements=[], packs=[])
        out = capsys.readouterr().out

        assert "Packs" not in out

    def test_box_drawing_chars(self, capsys):
        """Output uses proper box-drawing characters."""
        spec = {
            "organization": "test",
            "roles": {"agent": {"objectives": ["x"], "capabilities": ["y"]}},
            "access": {"src/": "read-write"},
        }
        run_tree(spec, achievements=[], packs=[])
        out = capsys.readouterr().out

        assert "├── " in out
        assert "└── " in out
        assert "│   " in out

    def test_tree_via_run(self, tmp_path, capsys):
        """Integration test: run() with --tree flag produces tree output."""
        spec_file = self._setup_spec(tmp_path, {
            "organization": "integration_test",
            "roles": {"agent": {
                "objectives": ["task_complete"],
                "capabilities": ["read_file"],
            }},
            "access": {"src/": "read-write"},
        })
        args = self._make_args(spec_file)
        run(args)
        out = capsys.readouterr().out

        assert "integration_test" in out
        assert "Role: agent" in out

    def test_multiple_roles(self, capsys):
        """Multiple roles render with sub-branches."""
        spec = {
            "organization": "multi",
            "roles": {
                "developer": {
                    "objectives": ["task_complete"],
                    "capabilities": ["read_file", "write_file"],
                },
                "reviewer": {
                    "objectives": ["code_reviewed"],
                    "capabilities": ["read_file"],
                },
            },
        }
        run_tree(spec, achievements=[], packs=[])
        out = capsys.readouterr().out

        assert "Roles" in out
        assert "Role: developer" in out
        assert "Role: reviewer" in out

    def test_obligations_shown_in_tree(self, capsys):
        """Active obligations shown with ! marker."""
        spec = {
            "organization": "test",
            "roles": {"agent": {"objectives": ["task_complete"], "capabilities": []}},
        }
        obligations = [
            {"agent": "agent", "role": "agent", "objective": "fix_leak", "deadline": "false"},
        ]
        run_tree(spec, achievements=[], packs=[], obligations=obligations)
        out = capsys.readouterr().out

        assert "Obligations" in out
        assert "! fix_leak" in out

    def test_obligations_with_deadline(self, capsys):
        """Obligations with deadlines show the deadline."""
        spec = {
            "organization": "test",
            "roles": {"agent": {"objectives": [], "capabilities": []}},
        }
        obligations = [
            {"agent": "agent", "role": "agent", "objective": "create_migration",
             "deadline": "git_commit"},
        ]
        run_tree(spec, achievements=[], packs=[], obligations=obligations)
        out = capsys.readouterr().out

        assert "! create_migration" in out
        assert "(deadline: git_commit)" in out

    def test_no_obligations_section_when_empty(self, capsys):
        """No obligations section when none active."""
        spec = {
            "organization": "test",
            "roles": {"agent": {"objectives": [], "capabilities": []}},
        }
        run_tree(spec, achievements=[], packs=[], obligations=[])
        out = capsys.readouterr().out

        assert "Obligations" not in out

    def test_fulfilled_obligations_excluded(self, tmp_path, capsys):
        """Obligations whose objective was achieved are not shown."""
        spec_dict = {
            "organization": "test",
            "roles": {"agent": {"objectives": [], "capabilities": []}},
        }
        spec_file = self._setup_spec(tmp_path, spec_dict)
        # State has obligation_created AND achieved for same objective
        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps({
            "events": [
                {"type": "register", "agent": "agent", "role": "agent", "scope": ""},
                {"type": "obligation_created", "agent": "agent", "role": "agent",
                 "objective": "fix_leak", "deadline": "false"},
                {"type": "achieved", "agent": "agent", "role": "agent",
                 "objectives": ["fix_leak"]},
            ],
            "exceptions": [],
        }))
        args = self._make_args(spec_file)
        run(args)
        out = capsys.readouterr().out

        assert "Obligations" not in out

    def test_active_obligation_shown_via_run(self, tmp_path, capsys):
        """Integration: run() with --tree shows active obligations from state."""
        spec_dict = {
            "organization": "test",
            "roles": {"agent": {"objectives": [], "capabilities": []}},
        }
        spec_file = self._setup_spec(tmp_path, spec_dict)
        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps({
            "events": [
                {"type": "register", "agent": "agent", "role": "agent", "scope": ""},
                {"type": "obligation_created", "agent": "agent", "role": "agent",
                 "objective": "remove_leaked_secret", "deadline": "false"},
            ],
            "exceptions": [],
        }))
        args = self._make_args(spec_file)
        run(args)
        out = capsys.readouterr().out

        assert "Obligations" in out
        assert "! remove_leaked_secret" in out


class TestStatusGraph:

    def test_linear_chain(self, capsys):
        """counts_as A -> B renders as a chain."""
        spec = {
            "achievement_triggers": [
                {"marks": "A", "command_pattern": "step1"},
                {"marks": "B", "command_pattern": "step2"},
            ],
            "counts_as": [{"when": ["A"], "marks": "B"}],
        }
        run_graph(spec, achievements=[])
        out = capsys.readouterr().out
        assert "[ ] A" in out
        assert "[ ] B" in out

    def test_achieved_markers(self, capsys):
        """Achieved nodes show [*]."""
        spec = {
            "achievement_triggers": [{"marks": "A", "command_pattern": "x"}],
        }
        run_graph(spec, achievements=["A"])
        out = capsys.readouterr().out
        assert "[*] A" in out

    def test_required_before_edge(self, capsys):
        """required_before norms create unlock edges."""
        spec = {
            "achievement_triggers": [{"marks": "tests_pass", "command_pattern": "pytest"}],
            "norms": [{
                "type": "required_before",
                "requires": "tests_pass",
                "command_pattern": "git push",
            }],
        }
        run_graph(spec, achievements=[])
        out = capsys.readouterr().out
        assert "tests_pass" in out
        assert "unlocks: git push" in out

    def test_fan_in(self, capsys):
        """Multiple prerequisites fan into one node."""
        spec = {
            "achievement_triggers": [
                {"marks": "A", "command_pattern": "x"},
                {"marks": "B", "command_pattern": "y"},
            ],
            "counts_as": [{"when": ["A", "B"], "marks": "C"}],
        }
        run_graph(spec, achievements=["A"])
        out = capsys.readouterr().out
        assert "[*] A" in out
        assert "[ ] B" in out
        assert "[ ] C" in out

    def test_empty_graph(self, capsys):
        """No achievements or edges prints a message."""
        spec = {}
        run_graph(spec, achievements=[])
        out = capsys.readouterr().out
        assert "No achievements" in out
