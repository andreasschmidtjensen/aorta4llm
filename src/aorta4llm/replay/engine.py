"""Replay engine — run parsed tool events through the governance hook."""

from dataclasses import dataclass, field
from pathlib import Path
from tempfile import mkdtemp

from aorta4llm.integration.hooks import GovernanceHook
from aorta4llm.replay.trace_parser import ToolEvent


@dataclass
class ReplayResult:
    """Result of replaying a single tool event."""
    tool_name: str
    tool_input: dict
    pre_decision: str  # "approve" | "block"
    pre_reason: str = ""
    post_result: dict | None = None
    is_sidechain: bool = False
    block_type: str = ""  # "policy" | "sanction" | "held" | ""


def _classify_block(reason: str) -> str:
    """Classify a block reason into a type."""
    if reason.startswith("HOLD:"):
        return "held"
    if reason.startswith("SANCTION:"):
        return "sanction"
    if reason:
        return "policy"
    return ""


class ReplayEngine:
    """Replay conversation traces against a governance spec.

    Creates a GovernanceHook with a temporary state directory so replays
    don't affect the real state.
    """

    def __init__(self, org_spec_path: str | Path, agent: str = "agent"):
        self._org_spec_path = Path(org_spec_path)
        self._agent = agent
        self._tmp_dir = Path(mkdtemp(prefix="aorta-replay-"))
        state_path = self._tmp_dir / "state.json"
        events_path = self._tmp_dir / "events.jsonl"
        self._hook = GovernanceHook(
            self._org_spec_path,
            state_path=state_path,
            events_path=str(events_path),
        )
        # Register the agent with the role from the spec
        import yaml
        with open(self._org_spec_path) as f:
            spec = yaml.safe_load(f)
        roles = list(spec.get("roles", {}).keys())
        role = roles[0] if roles else "agent"
        self._hook.register_agent(self._agent, role, "")

    def replay(self, events: list[ToolEvent]) -> list[ReplayResult]:
        """Replay a list of tool events and return results."""
        results: list[ReplayResult] = []
        for event in events:
            result = self._replay_one(event)
            results.append(result)
        return results

    def _replay_one(self, event: ToolEvent) -> ReplayResult:
        """Replay a single tool event through pre + post hooks."""
        context = {
            "tool_name": event.tool_name,
            "tool_input": event.tool_input,
        }

        # Run PreToolUse
        pre = self._hook.pre_tool_use(context, agent=self._agent, quiet=True)
        decision = pre.get("decision", "approve")
        reason = pre.get("reason", "")

        result = ReplayResult(
            tool_name=event.tool_name,
            tool_input=event.tool_input,
            pre_decision=decision,
            pre_reason=reason,
            is_sidechain=event.is_sidechain,
            block_type=_classify_block(reason) if decision == "block" else "",
        )

        # Always run PostToolUse — in the real session the tool executed
        # regardless of what our policy would have said.
        post_context = dict(context)
        post_context["tool_response"] = _approximate_tool_response(event)
        post = self._hook.post_tool_use(post_context, agent=self._agent)
        result.post_result = post

        return result


def _approximate_tool_response(event: ToolEvent) -> dict:
    """Build an approximate tool_response for PostToolUse.

    For Bash tools, approximate exitCode from is_error. For others,
    include stdout from the tool_result.
    """
    response: dict = {}
    if event.tool_name == "Bash":
        response["exitCode"] = 1 if event.is_error else 0
        response["stdout"] = event.tool_result or ""
        response["stderr"] = ""
    else:
        response["stdout"] = event.tool_result or ""
    return response
