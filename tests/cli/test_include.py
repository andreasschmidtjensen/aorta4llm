"""Tests for aorta include — add/remove policy packs."""

import yaml

from aorta4llm.cli.cmd_include import run, list_packs


class TestListPacks:

    def test_lists_available_packs(self):
        packs = list_packs()
        names = [p["name"] for p in packs]
        assert "tool-hygiene" in names
        assert "git-safety" in names

    def test_packs_have_descriptions(self):
        packs = list_packs()
        for p in packs:
            assert p["description"], f"Pack '{p['name']}' has no description"

    def test_no_args_lists_packs(self, capsys):
        class Args:
            pack = None
            remove = False
            org_spec = None
        run(Args())
        out = capsys.readouterr().out
        assert "Available packs:" in out
        assert "tool-hygiene" in out


class TestIncludePack:

    def _make_args(self, pack=None, remove=False, org_spec=None):
        class Args:
            pass
        a = Args()
        a.pack = pack
        a.remove = remove
        a.org_spec = org_spec
        return a

    def _setup_spec(self, tmp_path, spec_dict):
        spec_file = tmp_path / "spec.yaml"
        spec_file.write_text(yaml.dump(spec_dict, sort_keys=False))
        # Create minimal settings dir so rebuild_hooks doesn't fail
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.local.json").write_text("{}")
        return spec_file

    def test_add_pack(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        spec_file = self._setup_spec(tmp_path, {
            "organization": "test",
            "roles": {"agent": {"objectives": [], "capabilities": []}},
        })
        args = self._make_args(pack="tool-hygiene", org_spec=str(spec_file))
        run(args)
        out = capsys.readouterr().out
        assert "Added pack 'tool-hygiene'" in out

        spec = yaml.safe_load(spec_file.read_text())
        assert "tool-hygiene" in spec["include"]

    def test_add_duplicate_pack(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        spec_file = self._setup_spec(tmp_path, {
            "organization": "test",
            "include": ["tool-hygiene"],
            "roles": {"agent": {"objectives": [], "capabilities": []}},
        })
        args = self._make_args(pack="tool-hygiene", org_spec=str(spec_file))
        run(args)
        out = capsys.readouterr().out
        assert "already included" in out

    def test_add_unknown_pack(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        spec_file = self._setup_spec(tmp_path, {
            "organization": "test",
            "roles": {"agent": {"objectives": [], "capabilities": []}},
        })
        args = self._make_args(pack="nonexistent", org_spec=str(spec_file))
        try:
            run(args)
        except SystemExit:
            pass
        out = capsys.readouterr().out
        assert "Unknown pack" in out

    def test_remove_pack(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        spec_file = self._setup_spec(tmp_path, {
            "organization": "test",
            "include": ["tool-hygiene", "git-safety"],
            "roles": {"agent": {"objectives": [], "capabilities": []}},
        })
        args = self._make_args(pack="tool-hygiene", remove=True, org_spec=str(spec_file))
        run(args)
        out = capsys.readouterr().out
        assert "Removed pack 'tool-hygiene'" in out

        spec = yaml.safe_load(spec_file.read_text())
        assert "tool-hygiene" not in spec["include"]
        assert "git-safety" in spec["include"]

    def test_remove_last_pack_clears_include_key(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        spec_file = self._setup_spec(tmp_path, {
            "organization": "test",
            "include": ["tool-hygiene"],
            "roles": {"agent": {"objectives": [], "capabilities": []}},
        })
        args = self._make_args(pack="tool-hygiene", remove=True, org_spec=str(spec_file))
        run(args)

        spec = yaml.safe_load(spec_file.read_text())
        assert "include" not in spec

    def test_remove_pack_not_included(self, tmp_path, capsys, monkeypatch):
        monkeypatch.chdir(tmp_path)
        spec_file = self._setup_spec(tmp_path, {
            "organization": "test",
            "roles": {"agent": {"objectives": [], "capabilities": []}},
        })
        args = self._make_args(pack="tool-hygiene", remove=True, org_spec=str(spec_file))
        run(args)
        out = capsys.readouterr().out
        assert "not included" in out
