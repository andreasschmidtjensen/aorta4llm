"""Governance engine wrapping SWI-Prolog via pyswip."""

from dataclasses import dataclass
from pathlib import Path

from pyswip import Prolog

from governance.compiler import CompiledSpec

# Path to the Prolog source files
_PROLOG_DIR = Path(__file__).parent / "prolog"


@dataclass
class PermissionResult:
    """Result of a permission check."""

    permitted: bool
    reason: str
    violation: str | None = None


class GovernanceEngine:
    """Wraps pyswip.Prolog to provide the AORTA governance kernel."""

    def __init__(self):
        self._prolog = Prolog()
        self._load_base_rules()

    def _load_base_rules(self):
        """Load the metamodel and NC phase rules."""
        metamodel_path = str(_PROLOG_DIR / "metamodel.pl")
        nc_path = str(_PROLOG_DIR / "nc.pl")
        self._prolog.consult(metamodel_path)
        self._prolog.consult(nc_path)

    def load_org_spec(self, spec: CompiledSpec):
        """Assert all facts and rules from a compiled org spec."""
        for fact in spec.facts:
            self._prolog.assertz(fact)
        for rule in spec.rules:
            # Rules have trailing '.', strip it for assertz
            rule_body = rule.rstrip(".")
            self._prolog.assertz(rule_body)

    def enact_role(self, agent: str, role: str):
        """Assert that an agent enacts a role."""
        self._prolog.assertz(f"rea('{agent}', {role})")

    def run_nc(self, agent: str, role: str):
        """Run the NC (Norm Check) phase."""
        list(self._prolog.query(f"nc_run('{agent}', {role})"))

    def check_permission(
        self, agent: str, role: str, action: str, params: dict
    ) -> PermissionResult:
        """Check whether an action is permitted for an agent in a role.

        1. Assert current_scope from params
        2. Run NC phase (activates/expires norms)
        3. Build action term and query for blocks
        4. Clean up current_scope
        5. Return result
        """
        scope = params.get("scope", "")

        # Step 1: Assert current scope for condition evaluation
        self._prolog.assertz(f"current_scope('{scope}')")

        try:
            # Step 2: Run NC phase
            self.run_nc(agent, role)

            # Step 3: Build action term
            action_path = params.get("path", "")
            action_term = f"{action}('{action_path}')"

            # Step 4: Query for blocks
            blocked_results = list(
                self._prolog.query(
                    f"check_action_blocked('{agent}', {role}, {action_term}, BlockedObj)"
                )
            )

            if blocked_results:
                blocked_obj = blocked_results[0]["BlockedObj"]
                violation_term = (
                    f"viol('{agent}', {role}, forbidden, {action_term})"
                )
                return PermissionResult(
                    permitted=False,
                    reason=(
                        f"prohibition active: {action_term} blocked — "
                        f"path not in assigned scope {scope}"
                    ),
                    violation=violation_term,
                )

            return PermissionResult(
                permitted=True,
                reason=f"{action_term} permitted for {agent} in role {role}",
            )

        finally:
            # Step 5: Clean up current_scope
            list(self._prolog.query("retractall(current_scope(_))"))
