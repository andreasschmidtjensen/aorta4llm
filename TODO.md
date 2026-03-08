# TODO

## Planned

### Per-role access map overrides

The `access` map is currently org-level — it applies to all roles equally. This is correct for the common single-agent case, but multi-role setups (e.g. developer + reviewer) may need different access per role.

**Decision:** Keep org-level access as the simple default. Per-role differences use explicit `norms:`. If demand emerges, consider per-role overrides in the role definition:

```yaml
access:
  src/: read-write
  .env: no-access

roles:
  reviewer:
    access:
      src/: read-only  # override org-level
```

Not urgent — multi-agent isn't the primary use case yet.

### `--strict` mode for bash analysis

Option to always use LLM analysis for bash commands (slower but more thorough).

**File:** `governance/bash_analyzer.py`, org spec YAML field.

```yaml
bash_analysis: strict   # instead of: true
```

Values: `true` (heuristic first, LLM fallback), `strict` (always LLM), `false` (disabled).

Low priority. The heuristic handles ~80% of cases. Strict mode adds ~5s per command. Worth having for high-security contexts but not urgent.

## Bugs (from live Claude Code testing)

### Bash redirect regex captures trailing semicolons

**Severity:** High — causes false positives on common commands like `ls 2>/dev/null; ls ...`.

The `_REDIRECT_RE` pattern in `governance/bash_analyzer.py` uses `\S+` which greedily captures shell metacharacters. `2>/dev/null;` is captured as `/dev/null;`, which doesn't match the `/dev/null` filter.

**Fix:** Change capture group from `\S+` to `[^\s;|&)]+` in `_REDIRECT_RE` (and audit other regexes too).

**File:** `governance/bash_analyzer.py:80`

### Soft block approval in watch shows full untruncated command

**Severity:** Low — cosmetic, but makes watch output hard to read.

When a soft-blocked command is retried and approved, the watch output dumps the full reason including multi-line heredoc commands. The `_format_event` function in `cmd_watch.py` truncates block reasons to 80 chars but doesn't truncate approval reasons at all. Additionally, `hooks.py` logs the full engine reason (including the entire command) for soft block events.

**Fix:** Truncate the logged reason for soft blocks in `hooks.py`, and truncate approval reasons in `cmd_watch.py` the same way blocks are truncated.

**Files:** `integration/hooks.py:366-373`, `cli/cmd_watch.py:66-67`

### `aorta permissions` pollutes event log and watch output

**Severity:** Medium — confusing for users watching live events.

The `_check` function in `cmd_permissions.py` calls `hook.pre_tool_use()` which logs real events. Running `aorta permissions` generates ~14 synthetic check events (one per access map entry, read + write) that appear in `aorta watch` and inflate `aorta status` activity counts.

**Fix:** Add a `suppress_logging` flag to `pre_tool_use()`, or use the engine directly instead of going through the hook layer.

**File:** `cli/cmd_permissions.py:20-28`
