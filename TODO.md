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

### Content governance — PostToolUse redaction for sensitive files

The sensitive content warning (PostToolUse governance notice) nudges the agent not to hardcode values from read-only/no-access files. In testing, this works well — Claude refused to embed a database password and offered runtime loading instead. But it's a prompt-level nudge, not enforcement.

A stricter mode could intercept PostToolUse responses for sensitive files and redact known patterns (API keys, passwords, connection strings) before they enter the agent's context. This is hard to get right (false positives, partial redaction) but would close the biggest remaining gap: an agent that reads `.env` via allow-once can currently paste credentials into in-scope source files.

**Decision:** Worth investigating but not urgent. The current warning is surprisingly effective in practice. Redaction adds complexity and risk of breaking legitimate reads.

## Known Limitations

### Command pattern matching and flags-before-subcommand

Command patterns (`command_pattern` in norms and achievement triggers, `safe_commands`) use substring matching. This works when the agent invokes commands in their standard form (`git commit`, `npm test`, `pytest`), but fails when CLI tools accept global flags between the program name and the subcommand:

- `npm --prefix /path test` — does not contain `npm test`
- `cargo +nightly test` — does not contain `cargo test`
- `uv --quiet run pytest` — does not contain `uv run pytest`
- `go -C /path test` — does not contain `go test`

**Git is handled.** A normalization step strips git global flags (`-C`, `--no-pager`, `--git-dir`, etc.) before pattern matching, so `git -C /path commit` correctly matches `git commit`. This applies to permission checks, safe commands, and achievement triggers.

**Other tools are not normalized.** The most impactful case is achievement triggers: if the agent invokes a test runner with extra flags (e.g. `npm --prefix /path test`), the `tests_passing` achievement won't fire, and the commit gate stays locked. The user can work around this by re-running the test command in standard form, or by adding flag-inclusive variants to `command_pattern`.

A generic solution (e.g. ordered-token matching where `npm test` matches any command containing `npm` followed by `test` with arbitrary tokens in between) would fix all tools at once, but risks false positives on commands where token order carries different meaning. The current approach — normalize the most common tool (git), accept substring matching for the rest — is a pragmatic tradeoff.

