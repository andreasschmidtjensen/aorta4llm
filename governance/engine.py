"""Governance engine wrapping SWI-Prolog via pyswip."""

from dataclasses import dataclass, field
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


@dataclass
class NormChange:
    """A single norm state change detected by notify_action."""

    type: str  # "activated", "fulfilled", "violated"
    deontic: str
    objective: str
    deadline: str


@dataclass
class NotifyResult:
    """Result of a notify_action call."""

    norms_changed: list[NormChange] = field(default_factory=list)


class GovernanceEngine:
    """Wraps pyswip.Prolog to provide the AORTA governance kernel."""

    # Metamodel dynamic predicates: (name, arity)
    _DYNAMIC_PREDICATES = [
        ("role", 2), ("obj", 2), ("dep", 3), ("cap", 2), ("cond", 5),
        ("rea", 2), ("norm", 5), ("viol", 4), ("achieved", 1),
        ("deadline_reached", 1), ("current_scope", 1),
    ]

    def __init__(self):
        self._prolog = Prolog()
        self._reset_dynamic_state()
        self._load_base_rules()

    def _reset_dynamic_state(self):
        """Clean all dynamic predicate facts.

        pyswip shares a single SWI-Prolog engine across Prolog() instances,
        so we must retract all dynamic facts to ensure a clean slate.
        """
        for pred, arity in self._DYNAMIC_PREDICATES:
            args = ", ".join(["_"] * arity)
            try:
                list(self._prolog.query(f"retractall({pred}({args}))"))
            except Exception:
                pass  # Predicate might not exist on first run

    def _load_base_rules(self):
        """Load the metamodel, NC phase, and OG phase rules."""
        for pl_file in ["metamodel.pl", "nc.pl", "og.pl"]:
            self._prolog.consult(str(_PROLOG_DIR / pl_file))

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

    def notify_action(
        self,
        agent: str,
        role: str,
        achieved: list[str] | None = None,
        deadlines_reached: list[str] | None = None,
    ) -> NotifyResult:
        """Notify the engine of state changes and run NC phase.

        Asserts achievements and deadlines, runs NC to process norm
        activations/fulfillments/violations, and returns detected changes.
        """
        # Snapshot norms before
        before_norms = self._get_norm_set(agent, role)
        before_viols = self._get_violation_set(agent, role)

        # Assert achievements
        for obj in achieved or []:
            self._prolog.assertz(f"achieved({obj})")

        # Assert deadlines
        for dl in deadlines_reached or []:
            self._prolog.assertz(f"deadline_reached({dl})")

        # Run NC phase
        self.run_nc(agent, role)

        # Snapshot norms after
        after_norms = self._get_norm_set(agent, role)
        after_viols = self._get_violation_set(agent, role)

        # Compute changes
        changes = []
        for deon, obj, dl in after_norms - before_norms:
            changes.append(NormChange("activated", deon, obj, dl))
        for deon, obj, dl in before_norms - after_norms:
            new_viols = after_viols - before_viols
            if (deon, obj) in new_viols:
                changes.append(NormChange("violated", deon, obj, dl))
            else:
                changes.append(NormChange("fulfilled", deon, obj, dl))

        return NotifyResult(norms_changed=changes)

    def get_obligations(self, agent: str, role: str) -> dict:
        """Return active obligations and generated options for an agent.

        Runs NC and OG phases, then queries for active norms and options.
        """
        self.run_nc(agent, role)

        # Query active norms
        norm_results = list(self._prolog.query(
            f"norm('{agent}', {role}, Deon, Obj, Deadline), "
            f"term_to_atom(Deon, DeonS), "
            f"term_to_atom(Obj, ObjS), "
            f"term_to_atom(Deadline, DlS)"
        ))
        obligations = [
            {
                "deontic": str(r["DeonS"]),
                "objective": str(r["ObjS"]),
                "deadline": str(r["DlS"]),
                "status": "active",
            }
            for r in norm_results
        ]

        # Query OG options
        options = self._query_options(agent)

        return {"obligations": obligations, "options": options}

    def _get_norm_set(self, agent: str, role: str) -> set[tuple[str, str, str]]:
        """Snapshot current norms as a set of (deontic, objective, deadline) tuples."""
        results = list(self._prolog.query(
            f"norm('{agent}', {role}, Deon, Obj, Deadline), "
            f"term_to_atom(Deon, DeonS), "
            f"term_to_atom(Obj, ObjS), "
            f"term_to_atom(Deadline, DlS)"
        ))
        return {
            (str(r["DeonS"]), str(r["ObjS"]), str(r["DlS"]))
            for r in results
        }

    def _get_violation_set(self, agent: str, role: str) -> set[tuple[str, str]]:
        """Snapshot current violations as a set of (deontic, objective) tuples."""
        results = list(self._prolog.query(
            f"viol('{agent}', {role}, Deon, Obj), "
            f"term_to_atom(Deon, DeonS), "
            f"term_to_atom(Obj, ObjS)"
        ))
        return {(str(r["DeonS"]), str(r["ObjS"])) for r in results}

    def _query_options(self, agent: str) -> list[dict]:
        """Query OG options, returning each type with structured fields."""
        options = []
        for r in self._prolog.query(
            f"og_option('{agent}', norm(Deon, Obj)), "
            f"term_to_atom(Deon, DeonS), term_to_atom(Obj, ObjS)"
        ):
            options.append({
                "type": "norm",
                "deontic": str(r["DeonS"]),
                "objective": str(r["ObjS"]),
            })
        for r in self._prolog.query(
            f"og_option('{agent}', violation(Deon, Obj)), "
            f"term_to_atom(Deon, DeonS), term_to_atom(Obj, ObjS)"
        ):
            options.append({
                "type": "violation",
                "deontic": str(r["DeonS"]),
                "objective": str(r["ObjS"]),
            })
        for r in self._prolog.query(
            f"og_option('{agent}', delegate(Role, Obj)), "
            f"term_to_atom(Role, RoleS), term_to_atom(Obj, ObjS)"
        ):
            options.append({
                "type": "delegate",
                "to_role": str(r["RoleS"]),
                "objective": str(r["ObjS"]),
            })
        for r in self._prolog.query(
            f"og_option('{agent}', inform(Role, Obj)), "
            f"term_to_atom(Role, RoleS), term_to_atom(Obj, ObjS)"
        ):
            options.append({
                "type": "inform",
                "to_role": str(r["RoleS"]),
                "objective": str(r["ObjS"]),
            })
        for r in self._prolog.query(
            f"og_option('{agent}', enact(Role)), "
            f"term_to_atom(Role, RoleS)"
        ):
            options.append({"type": "enact", "role": str(r["RoleS"])})
        for r in self._prolog.query(
            f"og_option('{agent}', deact(Role)), "
            f"term_to_atom(Role, RoleS)"
        ):
            options.append({"type": "deact", "role": str(r["RoleS"])})
        return options
