# aorta4llm — Issues Found During Live Testing

Tested by setting up a real project at `/tmp/test-project`, initializing
governance with `aorta init`, and running Claude Code interactive sessions
with hooks active. Dashboard was also tested.

## Bugs

### 1. Hook format is outdated (showstopper)
`aorta init` generates the old hook format:
```json
{"matcher": "Write|Edit", "command": "..."}
```
Claude Code now requires:
```json
{"matcher": "Write|Edit", "hooks": [{"type": "command", "command": "..."}]}
```
Every new user hits a settings error on first launch.

### 2. `--scope` doesn't update template YAML
`aorta init --scope lib/` copies the template verbatim with `path: src/`
hardcoded in the forbidden_outside norm. The scope only applies to agent
registration, not the norm. A user who sets `--scope lib/` gets a norm
that blocks writes outside `src/`, not `lib/`.

### 3. Soft block cache is per-process (broken)
Each hook invocation creates a fresh `GovernanceHook` instance.
`_soft_block_cache` is always empty. The retry-within-window mechanism
cannot work because timestamps aren't persisted to the state file.

### 4. Silent approval for unregistered agents (fail-open)
If the agent name doesn't match a registration, `_get_agent_role()` returns
None and the hook approves everything.
**Fixed**: unregistered agents are now denied (fail-closed). `aorta init` always registers as `agent`.

### 5. Self-protection missing
`.aorta/` and `.claude/` weren't in any template's `readonly`.
The `forbidden_outside` norm blocks them incidentally (they're outside
`src/`) but a broader scope or removed norm would expose them.
**Fixed**: added hardcoded protection in hooks.py.

## UX Issues

### 6. Hook command not portable
Generated hooks use `uv run python -m integration.hooks` which only
resolves if aorta4llm is the active project. Should use the installed
`aorta` CLI entry point.

### 7. Multi-agent not practical with Claude Code
Claude Code supports only one `.claude/settings.local.json` per project.
`aorta init` now hardcodes `agent` as the agent name. Multi-agent requires
manual `aorta hook register` and separate project directories.

## What Worked Well

- `aorta init` one-command setup (once format is fixed)
- `aorta validate` catches config mistakes
- `aorta dry-run` excellent for testing specs
- Governance correctly blocked .env, config/, out-of-scope writes
- Bash heuristic caught `cp`, `mv`, `echo >` redirects
- Event log + dashboard showed all decisions in real time
- Claude itself respected governance blocks and explained them to the user
- Claude refused to edit its own policy (by judgment, now also by enforcement)
