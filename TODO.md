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
