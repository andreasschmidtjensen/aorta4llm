"""System prompt generation for governed agents.

Builds role-appropriate system prompts from the organizational spec,
including active obligations and norm descriptions.
"""

from governance.service import GovernanceService


def build_system_prompt(
    role: str,
    spec: dict,
    service: GovernanceService | None = None,
    agent_id: str | None = None,
) -> str:
    """Build a system prompt with organizational context for a role.

    Args:
        role: The role name (must exist in spec).
        spec: Parsed org spec dict.
        service: GovernanceService for obligation queries.
        agent_id: Agent ID for obligation queries.
    """
    role_spec = spec["roles"][role]
    org_name = spec.get("organization", "organization")

    lines = [
        f"You are acting as **{role}** in the {org_name} organization.",
        "",
        f"Objectives: {', '.join(role_spec.get('objectives', []))}",
        f"Capabilities: {', '.join(role_spec.get('capabilities', []))}",
    ]

    # Add norms relevant to this role
    role_norms = [n for n in spec.get("norms", []) if n["role"] == role]
    if role_norms:
        lines.append("")
        lines.append("Organizational constraints (enforced deterministically — violations are blocked):")
        for n in role_norms:
            cond = f" when {n['condition']}" if n.get("condition") else ""
            deadline = f" by {n['deadline']}" if n.get("deadline") and n["deadline"] is not False else ""
            lines.append(f"  - {n['type']}: {n['objective']}{cond}{deadline}")

    # Add active obligations from the governance engine
    if service and agent_id:
        try:
            result = service.get_obligations(agent_id, role)
            obligations = result.get("obligations", [])
            if obligations:
                lines.append("")
                lines.append("[ACTIVE OBLIGATIONS]")
                for obl in obligations:
                    lines.append(
                        f"  - You are {obl['deontic']} to achieve: {obl['objective']}"
                        f" (deadline: {obl['deadline']})"
                    )
        except Exception:
            pass

    return "\n".join(lines)
