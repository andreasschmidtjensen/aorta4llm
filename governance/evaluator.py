"""In-memory fact database and condition evaluator for the pure-Python engine.

Replaces SWI-Prolog's dynamic database and call/1 evaluation with a Python
implementation that supports the subset of Prolog used by the governance rules:
pattern-matching queries, negation-as-failure, atom_concat, user-defined rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from governance.terms import (
    Atom, Term, Var, WILDCARD, TermType, Substitution,
    apply_subst, is_ground, parse_term, unify,
)


@dataclass
class Rule:
    """A user-defined rule: head :- body_goal1, body_goal2, ..."""
    head: Term
    body: list[TermType]


class FactDatabase:
    """In-memory fact store indexed by (functor, arity)."""

    def __init__(self):
        self._facts: dict[tuple[str, int], list[tuple[TermType, ...]]] = {}

    def assert_fact(self, functor: str, args: tuple[TermType, ...]) -> None:
        key = (functor, len(args))
        if key not in self._facts:
            self._facts[key] = []
        self._facts[key].append(args)

    def retract_fact(self, functor: str, args: tuple[TermType, ...]) -> bool:
        """Remove the first matching fact. Returns True if found."""
        key = (functor, len(args))
        facts = self._facts.get(key, [])
        for i, stored_args in enumerate(facts):
            if unify(Term(functor, stored_args), Term(functor, args)) is not None:
                facts.pop(i)
                return True
        return False

    def retract_all(self, functor: str, arity: int) -> None:
        self._facts.pop((functor, arity), None)

    def query(self, functor: str, args: tuple[TermType, ...]) -> list[Substitution]:
        """Find all facts matching the pattern, returning substitutions."""
        key = (functor, len(args))
        results = []
        for stored_args in self._facts.get(key, []):
            subst = unify(Term(functor, args), Term(functor, stored_args))
            if subst is not None:
                results.append(subst)
        return results

    def has_fact(self, functor: str, args: tuple[TermType, ...]) -> bool:
        return len(self.query(functor, args)) > 0

    def get_all(self, functor: str, arity: int) -> list[tuple[TermType, ...]]:
        """Get all facts for a given functor/arity (for iteration)."""
        return list(self._facts.get((functor, arity), []))


class ConditionEvaluator:
    """Evaluates governance conditions against a fact database.

    Supports the subset of Prolog used in org spec conditions:
    - not(Goal) / negation-as-failure
    - atom_concat(A, B, C) — prefix/suffix checking
    - member(X, List) — list membership
    - true / false constants
    - ground(T) — groundness check
    - Conjunction via Term(",", (left, right))
    - User-defined rules (head :- body)
    - Fact database lookup
    """

    def __init__(self, facts: FactDatabase):
        self._facts = facts
        self._rules: dict[tuple[str, int], list[Rule]] = {}

    def add_rule(self, rule: Rule) -> None:
        key = (rule.head.functor, len(rule.head.args))
        if key not in self._rules:
            self._rules[key] = []
        self._rules[key].append(rule)

    def clear_rules(self):
        self._rules.clear()

    def evaluate(self, condition: TermType, subst: Substitution | None = None) -> list[Substitution]:
        """Evaluate a condition, returning all successful substitutions.

        Returns empty list on failure, list of substitutions on success.
        Each substitution is a dict mapping variable names to bound terms.
        """
        if subst is None:
            subst = {}
        condition = apply_subst(condition, subst)

        try:
            return self._eval(condition, subst)
        except _EvalError:
            return []

    def evaluate_bool(self, condition: TermType, subst: Substitution | None = None) -> bool:
        """Evaluate a condition, returning True if at least one solution exists."""
        return len(self.evaluate(condition, subst)) > 0

    def _eval(self, term: TermType, subst: Substitution) -> list[Substitution]:
        """Internal evaluation dispatcher."""
        if isinstance(term, Atom):
            if term.value == "true":
                return [subst]
            if term.value == "false":
                return []
            # Bare atom — check if it's a zero-arity fact
            if self._facts.has_fact(term.value, ()):
                return [subst]
            return []

        if isinstance(term, Var):
            # Unbound variable in condition — instantiation error equivalent
            raise _EvalError(f"Unbound variable in condition: {term}")

        if not isinstance(term, Term):
            return []

        # Conjunction: (A, B)
        if term.functor == ",":
            left, right = term.args
            results = []
            for s in self._eval(left, subst):
                right_applied = apply_subst(right, s)
                results.extend(self._eval(right_applied, s))
            return results

        # Negation: not(Goal)
        if term.functor == "not" and len(term.args) == 1:
            inner = term.args[0]
            if self._eval(inner, subst):
                return []  # Goal succeeded -> not fails
            return [subst]  # Goal failed -> not succeeds

        # Inequality: \==(X, Y)
        if term.functor == "\\==" and len(term.args) == 2:
            left = apply_subst(term.args[0], subst)
            right = apply_subst(term.args[1], subst)
            if left == right:
                return []
            return [subst]

        # Equality / unification: =(X, Y)
        if term.functor == "=" and len(term.args) == 2:
            result = unify(term.args[0], term.args[1], dict(subst))
            if result is not None:
                return [result]
            return []

        # ground(T) check
        if term.functor == "ground" and len(term.args) == 1:
            if is_ground(term.args[0], subst):
                return [subst]
            return []

        # call(Goal) — evaluate the inner goal
        if term.functor == "call" and len(term.args) == 1:
            return self._eval(term.args[0], subst)

        # catch(Goal, Catcher, Recovery) — try Goal, on error try Recovery
        if term.functor == "catch" and len(term.args) == 3:
            try:
                return self._eval(term.args[0], subst)
            except _EvalError:
                return []  # Equivalent to fail recovery

        # atom_concat(A, B, C)
        if term.functor == "atom_concat" and len(term.args) == 3:
            return self._eval_atom_concat(term.args[0], term.args[1], term.args[2], subst)

        # member(X, List)
        if term.functor == "member" and len(term.args) == 2:
            return self._eval_member(term.args[0], term.args[1], subst)

        # forall(Cond, Action) — not used in conditions, but present in NC phase
        if term.functor == "forall" and len(term.args) == 2:
            return self._eval_forall(term.args[0], term.args[1], subst)

        # Try user-defined rules
        key = (term.functor, len(term.args))
        if key in self._rules:
            results = []
            for rule in self._rules[key]:
                results.extend(self._try_rule(rule, term, subst))
            if results:
                return results

        # Fall through to fact database lookup
        return self._eval_fact_lookup(term, subst)

    def _eval_atom_concat(
        self, a: TermType, b: TermType, c: TermType, subst: Substitution
    ) -> list[Substitution]:
        """Evaluate atom_concat/3.

        Handles the two patterns used in governance rules:
        - atom_concat(Scope, _, Path) — prefix check: Path starts with Scope
        - atom_concat(_, '.py', Path) — suffix check: Path ends with '.py'
        """
        a = apply_subst(a, subst)
        b = apply_subst(b, subst)
        c = apply_subst(c, subst)

        a_ground = isinstance(a, Atom) or (isinstance(a, Var) and a.name == "_")
        b_ground = isinstance(b, Atom) or (isinstance(b, Var) and b.name == "_")
        c_ground = isinstance(c, Atom) or (isinstance(c, Var) and c.name == "_")

        a_val = a.value if isinstance(a, Atom) else None
        b_val = b.value if isinstance(b, Atom) else None
        c_val = c.value if isinstance(c, Atom) else None

        # Case: A and B ground, C is variable -> C = A + B
        if a_val is not None and b_val is not None and isinstance(c, Var) and c.name != "_":
            new_subst = dict(subst)
            new_subst[c.name] = Atom(a_val + b_val)
            return [new_subst]

        # Case: A ground, C ground, B is wildcard or variable -> prefix check
        if a_val is not None and c_val is not None:
            if c_val.startswith(a_val):
                remainder = c_val[len(a_val):]
                new_subst = dict(subst)
                if isinstance(b, Var) and b.name != "_":
                    new_subst[b.name] = Atom(remainder)
                return [new_subst]
            return []

        # Case: B ground, C ground, A is wildcard or variable -> suffix check
        if b_val is not None and c_val is not None:
            if c_val.endswith(b_val):
                prefix = c_val[:len(c_val) - len(b_val)]
                new_subst = dict(subst)
                if isinstance(a, Var) and a.name != "_":
                    new_subst[a.name] = Atom(prefix)
                return [new_subst]
            return []

        # Case: A ground, B is wildcard/var, C is wildcard -> always true (any C starting with A)
        # This shouldn't happen in practice, but handle gracefully
        if a_val is not None and (isinstance(b, Var)) and (isinstance(c, Var) and c.name == "_"):
            return [subst]

        # Can't evaluate — insufficient binding
        raise _EvalError("atom_concat: insufficient bindings")

    def _eval_member(self, elem: TermType, lst: TermType, subst: Substitution) -> list[Substitution]:
        """Evaluate member/2 — check list membership."""
        lst = apply_subst(lst, subst)
        elem = apply_subst(elem, subst)

        # Traverse Prolog-style list: .(Head, Tail)
        results = []
        current = lst
        while isinstance(current, Term) and current.functor == "." and len(current.args) == 2:
            head, tail = current.args
            s = unify(elem, head, dict(subst))
            if s is not None:
                results.append(s)
            current = tail
        return results

    def _eval_forall(self, cond: TermType, action: TermType, subst: Substitution) -> list[Substitution]:
        """Evaluate forall(Cond, Action) — succeeds if Action succeeds for every solution of Cond."""
        cond_results = self._eval(cond, subst)
        for s in cond_results:
            action_applied = apply_subst(action, s)
            if not self._eval(action_applied, s):
                return []
        return [subst]

    def _try_rule(self, rule: Rule, goal: Term, subst: Substitution) -> list[Substitution]:
        """Try to apply a user-defined rule to a goal."""
        # Rename rule variables to avoid conflicts
        rename_map = _make_rename_map(rule, subst)
        renamed_head = _rename_vars(rule.head, rename_map)
        renamed_body = [_rename_vars(b, rename_map) for b in rule.body]

        # Unify goal with rule head
        head_subst = unify(goal, renamed_head, dict(subst))
        if head_subst is None:
            return []

        # Evaluate body goals in sequence
        current_substs = [head_subst]
        for body_goal in renamed_body:
            next_substs = []
            for s in current_substs:
                applied = apply_subst(body_goal, s)
                next_substs.extend(self._eval(applied, s))
            current_substs = next_substs
            if not current_substs:
                return []

        # Filter substitution to only include original variables
        results = []
        for s in current_substs:
            filtered = {k: v for k, v in s.items() if k in subst or k in _term_vars(goal)}
            # Include all bindings that affect the original variables
            results.append(s)
        return results

    def _eval_fact_lookup(self, term: Term, subst: Substitution) -> list[Substitution]:
        """Look up a term in the fact database."""
        matches = self._facts.query(term.functor, term.args)
        results = []
        for match_subst in matches:
            # Merge the match substitution with the current one
            merged = dict(subst)
            conflict = False
            for k, v in match_subst.items():
                if k in merged:
                    if unify(merged[k], v) is None:
                        conflict = True
                        break
                merged[k] = v
            if not conflict:
                results.append(merged)
        return results


class _EvalError(Exception):
    """Internal error during condition evaluation (e.g., instantiation error)."""


_rename_counter = 0


def _make_rename_map(rule: Rule, subst: Substitution) -> dict[str, str]:
    """Create a variable renaming map to avoid capture."""
    global _rename_counter
    _rename_counter += 1
    suffix = f"_{_rename_counter}"

    all_vars: set[str] = set()
    _collect_vars(rule.head, all_vars)
    for b in rule.body:
        _collect_vars(b, all_vars)

    return {v: f"{v}{suffix}" for v in all_vars if v != "_"}


def _collect_vars(t: TermType, result: set[str]) -> None:
    if isinstance(t, Var) and t.name != "_":
        result.add(t.name)
    elif isinstance(t, Term):
        for a in t.args:
            _collect_vars(a, result)


def _rename_vars(t: TermType, rename_map: dict[str, str]) -> TermType:
    if isinstance(t, Var):
        if t.name == "_":
            return t
        return Var(rename_map.get(t.name, t.name))
    if isinstance(t, Atom):
        return t
    if isinstance(t, Term):
        new_args = tuple(_rename_vars(a, rename_map) for a in t.args)
        return Term(t.functor, new_args)
    return t


def _term_vars(t: TermType) -> set[str]:
    result: set[str] = set()
    _collect_vars(t, result)
    return result
