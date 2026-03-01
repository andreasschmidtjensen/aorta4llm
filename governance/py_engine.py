"""Pure-Python governance engine — no SWI-Prolog required.

Drop-in replacement for GovernanceEngine with the same public API.
Reimplements the NC (Norm Check) and OG (Option Generation) phases
using the Python term/evaluator modules instead of pyswip.
"""

from __future__ import annotations

from governance.compiler import CompiledSpec
from governance.engine_types import NormChange, NotifyResult, PermissionResult
from governance.evaluator import ConditionEvaluator, FactDatabase, Rule
from governance.terms import (
    Atom, Term, Var, WILDCARD, TermType, Substitution,
    apply_subst, is_ground, parse_term, term_to_str, unify,
)


class PythonGovernanceEngine:
    """Pure-Python governance engine implementing the AORTA reasoning cycle."""

    _DYNAMIC_PREDICATES = [
        ("role", 2), ("obj", 2), ("dep", 3), ("cap", 2), ("cond", 5),
        ("rea", 2), ("norm", 5), ("viol", 4), ("achieved", 1),
        ("deadline_reached", 1), ("current_scope", 1),
    ]

    def __init__(self):
        self._facts = FactDatabase()
        self._evaluator = ConditionEvaluator(self._facts)

    def load_org_spec(self, spec: CompiledSpec) -> None:
        """Load compiled org spec facts and rules."""
        for fact_str in spec.facts:
            term = parse_term(fact_str)
            if isinstance(term, Term):
                self._facts.assert_fact(term.functor, term.args)
            elif isinstance(term, Atom):
                self._facts.assert_fact(term.value, ())

        for rule_str in spec.rules:
            rule_str = rule_str.rstrip(".")
            rule = self._parse_rule(rule_str)
            if rule:
                self._evaluator.add_rule(rule)

    def _parse_rule(self, rule_str: str) -> Rule | None:
        """Parse 'head :- body' rule string into a Rule object."""
        # Split on :- (but not inside parentheses)
        depth = 0
        split_pos = -1
        for i in range(len(rule_str) - 1):
            if rule_str[i] in "('":
                depth += 1
            elif rule_str[i] in ")'":
                depth -= 1
            elif depth == 0 and rule_str[i:i + 2] == ":-":
                split_pos = i
                break

        if split_pos == -1:
            return None  # Not a rule, just a fact

        head_str = rule_str[:split_pos].strip()
        body_str = rule_str[split_pos + 2:].strip()

        head = parse_term(head_str)
        if not isinstance(head, Term):
            return None

        body_goals = self._parse_body(body_str)
        return Rule(head, body_goals)

    def _parse_body(self, body_str: str) -> list[TermType]:
        """Parse a rule body, splitting on commas at the top level."""
        goals = []
        depth = 0
        current = []
        for ch in body_str:
            if ch in "([":
                depth += 1
                current.append(ch)
            elif ch in ")]":
                depth -= 1
                current.append(ch)
            elif ch == "'" :
                current.append(ch)
                # Track quoted atoms — find matching quote
            elif ch == "," and depth == 0:
                goal_str = "".join(current).strip()
                if goal_str:
                    goals.append(parse_term(goal_str))
                current = []
            else:
                current.append(ch)
        remainder = "".join(current).strip()
        if remainder:
            goals.append(parse_term(remainder))
        return goals

    def enact_role(self, agent: str, role: str) -> None:
        self._facts.assert_fact("rea", (Atom(agent), Atom(role)))

    def run_nc(self, agent: str, role: str) -> None:
        """Run the NC (Norm Check) phase — Python translation of nc.pl."""
        self._nc_activate_obligation()
        self._nc_fulfill_obligation()
        self._nc_violate_obligation()
        self._nc_activate_prohibition()
        self._nc_expire_prohibition()

    def check_permission(
        self, agent: str, role: str, action: str, params: dict
    ) -> PermissionResult:
        scope = params.get("scope", "")
        self._facts.assert_fact("current_scope", (Atom(scope),))

        try:
            self.run_nc(agent, role)

            action_path = params.get("path", "")
            action_term = Term(action, (Atom(action_path),))

            blocked_obj = self._check_action_blocked(agent, role, action_term)
            if blocked_obj is not None:
                action_str = term_to_str(action_term)
                return PermissionResult(
                    permitted=False,
                    reason=f"prohibition active: {action_str} blocked for {agent} in role {role}",
                    violation=f"viol('{agent}', {role}, forbidden, {action_str})",
                )

            action_str = term_to_str(action_term)
            return PermissionResult(
                permitted=True,
                reason=f"{action_str} permitted for {agent} in role {role}",
            )
        finally:
            self._facts.retract_all("current_scope", 1)

    def notify_action(
        self,
        agent: str,
        role: str,
        achieved: list[str] | None = None,
        deadlines_reached: list[str] | None = None,
    ) -> NotifyResult:
        before_norms = self._get_norm_set(agent, role)
        before_viols = self._get_violation_set(agent, role)

        for obj in achieved or []:
            term = parse_term(obj)
            if isinstance(term, Term):
                self._facts.assert_fact("achieved", (term,))
            elif isinstance(term, Atom):
                self._facts.assert_fact("achieved", (term,))

        for dl in deadlines_reached or []:
            term = parse_term(dl)
            if isinstance(term, Term):
                self._facts.assert_fact("deadline_reached", (term,))
            elif isinstance(term, Atom):
                self._facts.assert_fact("deadline_reached", (term,))

        self.run_nc(agent, role)

        after_norms = self._get_norm_set(agent, role)
        after_viols = self._get_violation_set(agent, role)

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
        self.run_nc(agent, role)

        # Query active norms
        obligations = []
        for args in self._facts.get_all("norm", 5):
            n_agent, n_role, n_deon, n_obj, n_deadline = args
            if (isinstance(n_agent, Atom) and n_agent.value == agent and
                    isinstance(n_role, Atom) and n_role.value == role):
                obligations.append({
                    "deontic": term_to_str(n_deon),
                    "objective": term_to_str(n_obj),
                    "deadline": term_to_str(n_deadline),
                    "status": "active",
                })

        options = self._query_options(agent)
        return {"obligations": obligations, "options": options}

    def get_agent_role(self, agent: str) -> str | None:
        results = self._facts.query("rea", (Atom(agent), Var("Role")))
        if results:
            role = results[0].get("Role")
            if isinstance(role, Atom):
                return role.value
            if isinstance(role, Term):
                return term_to_str(role)
        return None

    # --- NC phase rules (translation of nc.pl) ---

    def _nc_activate_obligation(self) -> None:
        """Activate obligations when condition holds and objective not achieved."""
        for rea_args in self._facts.get_all("rea", 2):
            agent_atom, role_atom = rea_args
            if not isinstance(agent_atom, Atom) or not isinstance(role_atom, Atom):
                continue
            agent, role = agent_atom.value, role_atom.value

            for cond_args in self._facts.get_all("cond", 5):
                c_role, c_deon, c_obj, c_deadline, c_cond = cond_args
                if not (isinstance(c_role, Atom) and c_role.value == role):
                    continue
                if not (isinstance(c_deon, Atom) and c_deon.value == "obliged"):
                    continue

                # Evaluate condition (with catch for instantiation errors)
                try:
                    if not self._evaluator.evaluate_bool(c_cond):
                        continue
                except Exception:
                    continue

                # Check objective not already achieved
                if self._facts.has_fact("achieved", (c_obj,)):
                    continue

                # Check norm not already active
                norm_args = (Atom(agent), Atom(role), c_deon, c_obj, c_deadline)
                if self._facts.has_fact("norm", norm_args):
                    continue

                self._facts.assert_fact("norm", norm_args)

    def _nc_fulfill_obligation(self) -> None:
        """Fulfill obligations when objective is achieved."""
        to_retract = []
        for norm_args in self._facts.get_all("norm", 5):
            n_agent, n_role, n_deon, n_obj, n_deadline = norm_args
            if not (isinstance(n_deon, Atom) and n_deon.value == "obliged"):
                continue
            if self._facts.has_fact("achieved", (n_obj,)):
                to_retract.append(norm_args)

        for args in to_retract:
            self._facts.retract_fact("norm", args)

    def _nc_violate_obligation(self) -> None:
        """Violate obligations when deadline reached without fulfillment."""
        to_retract = []
        to_assert_viol = []
        for norm_args in self._facts.get_all("norm", 5):
            n_agent, n_role, n_deon, n_obj, n_deadline = norm_args
            if not (isinstance(n_deon, Atom) and n_deon.value == "obliged"):
                continue
            # Objective not achieved
            if self._facts.has_fact("achieved", (n_obj,)):
                continue
            # Deadline reached
            if not self._facts.has_fact("deadline_reached", (n_deadline,)):
                continue
            # Violation not already recorded
            viol_args = (n_agent, n_role, n_deon, n_obj)
            if self._facts.has_fact("viol", viol_args):
                continue

            to_retract.append(norm_args)
            to_assert_viol.append(viol_args)

        for args in to_retract:
            self._facts.retract_fact("norm", args)
        for args in to_assert_viol:
            self._facts.assert_fact("viol", args)

    def _nc_activate_prohibition(self) -> None:
        """Activate ground prohibitions when condition holds."""
        for rea_args in self._facts.get_all("rea", 2):
            agent_atom, role_atom = rea_args
            if not isinstance(agent_atom, Atom) or not isinstance(role_atom, Atom):
                continue
            agent, role = agent_atom.value, role_atom.value

            for cond_args in self._facts.get_all("cond", 5):
                c_role, c_deon, c_obj, c_deadline, c_cond = cond_args
                if not (isinstance(c_role, Atom) and c_role.value == role):
                    continue
                if not (isinstance(c_deon, Atom) and c_deon.value == "forbidden"):
                    continue

                # Only activate ground prohibitions
                if not is_ground(c_obj) or not is_ground(c_cond):
                    continue

                if not self._evaluator.evaluate_bool(c_cond):
                    continue

                norm_args = (Atom(agent), Atom(role), c_deon, c_obj, c_deadline)
                if not self._facts.has_fact("norm", norm_args):
                    self._facts.assert_fact("norm", norm_args)

    def _nc_expire_prohibition(self) -> None:
        """Expire prohibitions when deadline reached."""
        to_retract = []
        for norm_args in self._facts.get_all("norm", 5):
            n_agent, n_role, n_deon, n_obj, n_deadline = norm_args
            if not (isinstance(n_deon, Atom) and n_deon.value == "forbidden"):
                continue
            if isinstance(n_deadline, Atom) and n_deadline.value == "false":
                continue
            if self._facts.has_fact("deadline_reached", (n_deadline,)):
                to_retract.append(norm_args)

        for args in to_retract:
            self._facts.retract_fact("norm", args)

    # --- Permission checking ---

    def _check_action_blocked(self, agent: str, role: str, action: Term) -> TermType | None:
        """Check if an action is blocked by any prohibition.

        Two paths (matching nc.pl's two check_action_blocked clauses):
        1. Check activated norm/5 facts with forbidden deontic
        2. Check cond/5 facts directly — unify action with objective to bind variables
        """
        # Path 1: Check activated norms
        for norm_args in self._facts.get_all("norm", 5):
            n_agent, n_role, n_deon, n_obj, n_deadline = norm_args
            if not (isinstance(n_agent, Atom) and n_agent.value == agent):
                continue
            if not (isinstance(n_role, Atom) and n_role.value == role):
                continue
            if not (isinstance(n_deon, Atom) and n_deon.value == "forbidden"):
                continue
            if unify(action, n_obj) is not None:
                return n_obj

        # Path 2: Check conditional prohibitions directly
        for cond_args in self._facts.get_all("cond", 5):
            c_role, c_deon, c_obj, c_deadline, c_cond = cond_args
            if not (isinstance(c_role, Atom) and c_role.value == role):
                continue
            if not (isinstance(c_deon, Atom) and c_deon.value == "forbidden"):
                continue

            # Check agent enacts role
            if not self._facts.has_fact("rea", (Atom(agent), Atom(role))):
                continue

            # Unify action with objective to bind shared variables
            subst = unify(action, c_obj)
            if subst is None:
                continue

            # Evaluate condition with bound variables
            bound_cond = apply_subst(c_cond, subst)
            if self._evaluator.evaluate_bool(bound_cond, subst):
                return c_obj

        return None

    # --- Norm/violation snapshots ---

    def _get_norm_set(self, agent: str, role: str) -> set[tuple[str, str, str]]:
        result = set()
        for args in self._facts.get_all("norm", 5):
            n_agent, n_role, n_deon, n_obj, n_deadline = args
            if (isinstance(n_agent, Atom) and n_agent.value == agent and
                    isinstance(n_role, Atom) and n_role.value == role):
                result.add((term_to_str(n_deon), term_to_str(n_obj), term_to_str(n_deadline)))
        return result

    def _get_violation_set(self, agent: str, role: str) -> set[tuple[str, str]]:
        result = set()
        for args in self._facts.get_all("viol", 4):
            v_agent, v_role, v_deon, v_obj = args
            if (isinstance(v_agent, Atom) and v_agent.value == agent and
                    isinstance(v_role, Atom) and v_role.value == role):
                result.add((term_to_str(v_deon), term_to_str(v_obj)))
        return result

    # --- OG phase (translation of og.pl) ---

    def _query_options(self, agent: str) -> list[dict]:
        options = []
        options.extend(self._og_enact(agent))
        options.extend(self._og_deact(agent))
        options.extend(self._og_norm(agent))
        options.extend(self._og_violation(agent))
        options.extend(self._og_delegate(agent))
        options.extend(self._og_inform(agent))
        return options

    def _og_enact(self, agent: str) -> list[dict]:
        """Roles the agent hasn't enacted that have capabilities."""
        results = []
        for role_args in self._facts.get_all("role", 2):
            role_name = role_args[0]
            if not isinstance(role_name, Atom):
                continue
            # Not already enacted
            if self._facts.has_fact("rea", (Atom(agent), role_name)):
                continue
            # Has capabilities
            if self._facts.has_fact("cap", (role_name, Var("_Cap"))):
                results.append({"type": "enact", "role": role_name.value})
        return results

    def _og_deact(self, agent: str) -> list[dict]:
        """Roles where all objectives are achieved."""
        results = []
        for rea_args in self._facts.get_all("rea", 2):
            if not (isinstance(rea_args[0], Atom) and rea_args[0].value == agent):
                continue
            role_atom = rea_args[1]
            if not isinstance(role_atom, Atom):
                continue

            # Get role objectives
            role_matches = self._facts.query("role", (role_atom, Var("Objs")))
            if not role_matches:
                continue
            objs_term = role_matches[0].get("Objs")
            if objs_term is None:
                continue

            # Extract objectives from list
            objectives = self._list_to_terms(objs_term)
            if not objectives:
                continue

            # All must be achieved
            all_achieved = all(
                self._facts.has_fact("achieved", (obj,)) for obj in objectives
            )
            if all_achieved:
                results.append({"type": "deact", "role": role_atom.value})
        return results

    def _og_norm(self, agent: str) -> list[dict]:
        """Active norms become options."""
        results = []
        for args in self._facts.get_all("norm", 5):
            n_agent, n_role, n_deon, n_obj, n_deadline = args
            if isinstance(n_agent, Atom) and n_agent.value == agent:
                results.append({
                    "type": "norm",
                    "deontic": term_to_str(n_deon),
                    "objective": term_to_str(n_obj),
                })
        return results

    def _og_violation(self, agent: str) -> list[dict]:
        """Violations become options."""
        results = []
        for args in self._facts.get_all("viol", 4):
            v_agent, v_role, v_deon, v_obj = args
            if isinstance(v_agent, Atom) and v_agent.value == agent:
                results.append({
                    "type": "violation",
                    "deontic": term_to_str(v_deon),
                    "objective": term_to_str(v_obj),
                })
        return results

    def _og_delegate(self, agent: str) -> list[dict]:
        """Dependency relations generate delegation options."""
        results = []
        for rea_args in self._facts.get_all("rea", 2):
            if not (isinstance(rea_args[0], Atom) and rea_args[0].value == agent):
                continue
            role = rea_args[1]
            for dep_args in self._facts.get_all("dep", 3):
                d_role, d_dep_role, d_obj = dep_args
                if d_role != role:
                    continue
                if not self._facts.has_fact("achieved", (d_obj,)):
                    results.append({
                        "type": "delegate",
                        "to_role": term_to_str(d_dep_role),
                        "objective": term_to_str(d_obj),
                    })
        return results

    def _og_inform(self, agent: str) -> list[dict]:
        """When an objective is achieved that another role depends on."""
        results = []
        for rea_args in self._facts.get_all("rea", 2):
            if not (isinstance(rea_args[0], Atom) and rea_args[0].value == agent):
                continue
            role = rea_args[1]
            for dep_args in self._facts.get_all("dep", 3):
                d_role, d_dep_role, d_obj = dep_args
                if d_dep_role != role:
                    continue
                if self._facts.has_fact("achieved", (d_obj,)):
                    results.append({
                        "type": "inform",
                        "to_role": term_to_str(d_role),
                        "objective": term_to_str(d_obj),
                    })
        return results

    @staticmethod
    def _list_to_terms(lst: TermType) -> list[TermType]:
        """Convert a Prolog-style list term to a Python list of terms."""
        result = []
        current = lst
        while isinstance(current, Term) and current.functor == "." and len(current.args) == 2:
            result.append(current.args[0])
            current = current.args[1]
        return result
