"""Term representation, parsing, and unification for the pure-Python engine.

Provides a minimal term algebra sufficient to replicate the Prolog unification
used by the governance engine: matching action terms against prohibition
objectives, binding shared variables in conditions.
"""

from __future__ import annotations

from dataclasses import dataclass


# --- Term types -----------------------------------------------------------

@dataclass(frozen=True)
class Var:
    """An unbound logic variable. Uppercase-initial names follow Prolog convention."""
    name: str

    def __repr__(self) -> str:
        return self.name


@dataclass(frozen=True)
class Atom:
    """A ground atom (string constant). Includes quoted and unquoted atoms."""
    value: str

    def __repr__(self) -> str:
        if self.value == "[]":
            return "[]"
        # Quote if contains special chars or starts lowercase with parens nearby
        if any(c in self.value for c in " ,()'\"") or "/" in self.value:
            return f"'{self.value}'"
        return self.value


@dataclass(frozen=True)
class Term:
    """A compound term: functor(arg1, arg2, ...)."""
    functor: str
    args: tuple[Atom | Var | Term, ...]

    def __repr__(self) -> str:
        args_str = ", ".join(repr(a) for a in self.args)
        return f"{self.functor}({args_str})"


# Wildcard: anonymous variable (matches anything, never binds)
WILDCARD = Var("_")

# Type alias for substitutions (variable name -> bound term)
TermType = Atom | Var | Term
Substitution = dict[str, TermType]


# --- Unification -----------------------------------------------------------

def unify(t1: TermType, t2: TermType, subst: Substitution | None = None) -> Substitution | None:
    """Unify two terms, returning a substitution or None on failure.

    Standard structural unification: variables bind to terms, atoms must
    match exactly, compound terms must have the same functor/arity and
    all arguments must unify pairwise.
    """
    if subst is None:
        subst = {}

    t1 = _walk(t1, subst)
    t2 = _walk(t2, subst)

    if isinstance(t1, Var) and t1.name == "_":
        return subst
    if isinstance(t2, Var) and t2.name == "_":
        return subst

    if isinstance(t1, Var):
        return _bind(t1.name, t2, subst)
    if isinstance(t2, Var):
        return _bind(t2.name, t1, subst)

    if isinstance(t1, Atom) and isinstance(t2, Atom):
        return subst if t1.value == t2.value else None

    if isinstance(t1, Term) and isinstance(t2, Term):
        if t1.functor != t2.functor or len(t1.args) != len(t2.args):
            return None
        for a1, a2 in zip(t1.args, t2.args):
            subst = unify(a1, a2, subst)
            if subst is None:
                return None
        return subst

    # Atom vs Term or vice versa — no match
    return None


def _walk(t: TermType, subst: Substitution) -> TermType:
    """Follow variable bindings to their current value."""
    while isinstance(t, Var) and t.name != "_" and t.name in subst:
        t = subst[t.name]
    return t


def _bind(var_name: str, term: TermType, subst: Substitution) -> Substitution:
    """Bind a variable to a term, returning updated substitution."""
    if isinstance(term, Var) and term.name == var_name:
        return subst  # X = X, no-op
    # Simple occurs check for direct self-reference
    new_subst = dict(subst)
    new_subst[var_name] = term
    return new_subst


def apply_subst(t: TermType, subst: Substitution) -> TermType:
    """Apply a substitution to a term, replacing bound variables."""
    t = _walk(t, subst)
    if isinstance(t, Var):
        return t  # Unbound variable
    if isinstance(t, Atom):
        return t
    if isinstance(t, Term):
        new_args = tuple(apply_subst(a, subst) for a in t.args)
        return Term(t.functor, new_args)
    return t


def is_ground(t: TermType, subst: Substitution | None = None) -> bool:
    """Check whether a term is ground (contains no unbound variables)."""
    if subst:
        t = apply_subst(t, subst)
    if isinstance(t, Atom):
        return True
    if isinstance(t, Var):
        return t.name == "_"
    if isinstance(t, Term):
        return all(is_ground(a) for a in t.args)
    return False


def term_to_str(t: TermType) -> str:
    """Convert a term to its string representation (matching Prolog's term_to_atom)."""
    if isinstance(t, Atom):
        return t.value
    if isinstance(t, Var):
        return t.name
    if isinstance(t, Term):
        args_str = ", ".join(term_to_str(a) for a in t.args)
        return f"{t.functor}({args_str})"
    return str(t)


# --- Parser ----------------------------------------------------------------

class ParseError(Exception):
    """Raised when a term string cannot be parsed."""


def parse_term(s: str) -> TermType:
    """Parse a Prolog-syntax term string into a Term/Atom/Var.

    Grammar:
        term     = variable | list | compound | atom
        compound = atom '(' termlist ')'
        termlist = term (',' term)*
        variable = [A-Z_][a-zA-Z0-9_]*
        atom     = quoted_atom | bare_atom | number
        list     = '[' ']' | '[' termlist ']'
    """
    parser = _Parser(s.strip())
    result = parser.parse_term()
    parser.skip_ws()
    if parser.pos < len(parser.s):
        raise ParseError(f"Unexpected trailing text: {parser.s[parser.pos:]!r}")
    return result


class _Parser:
    """Recursive descent parser for Prolog term syntax."""

    def __init__(self, s: str):
        self.s = s
        self.pos = 0

    def skip_ws(self):
        while self.pos < len(self.s) and self.s[self.pos] in " \t\n\r":
            self.pos += 1

    def peek(self) -> str | None:
        self.skip_ws()
        return self.s[self.pos] if self.pos < len(self.s) else None

    def parse_term(self) -> TermType:
        self.skip_ws()
        if self.pos >= len(self.s):
            raise ParseError("Unexpected end of input")

        ch = self.s[self.pos]

        # List literal
        if ch == "[":
            return self._parse_list()

        # Quoted atom
        if ch == "'":
            return self._parse_quoted_atom()

        # Number (possibly negative)
        if ch.isdigit() or (ch == "-" and self.pos + 1 < len(self.s) and self.s[self.pos + 1].isdigit()):
            return self._parse_number()

        # Parenthesized expression / conjunction
        if ch == "(":
            return self._parse_parens()

        # Variable or atom/compound
        if ch.isalpha() or ch == "_":
            return self._parse_name()

        # Operator: \+, \==
        if ch == "\\":
            return self._parse_backslash_op()

        raise ParseError(f"Unexpected character {ch!r} at position {self.pos}")

    def _parse_list(self) -> TermType:
        self.pos += 1  # skip '['
        self.skip_ws()
        if self.peek() == "]":
            self.pos += 1
            return Atom("[]")
        items = self._parse_termlist()
        self.skip_ws()
        if self.pos >= len(self.s) or self.s[self.pos] != "]":
            raise ParseError("Expected ']'")
        self.pos += 1
        # Build Prolog-style list: [a, b, c] -> '.'(a, '.'(b, '.'(c, '[]')))
        # But for simplicity, represent as a Term with functor '[]' and items as args
        # Actually, keep as a special list term
        result: TermType = Atom("[]")
        for item in reversed(items):
            result = Term(".", (item, result))
        return result

    def _parse_quoted_atom(self) -> Atom:
        self.pos += 1  # skip opening quote
        start = self.pos
        while self.pos < len(self.s) and self.s[self.pos] != "'":
            if self.s[self.pos] == "\\" and self.pos + 1 < len(self.s):
                self.pos += 2
            else:
                self.pos += 1
        if self.pos >= len(self.s):
            raise ParseError("Unterminated quoted atom")
        value = self.s[start:self.pos]
        self.pos += 1  # skip closing quote
        return Atom(value)

    def _parse_number(self) -> Atom:
        start = self.pos
        if self.s[self.pos] == "-":
            self.pos += 1
        while self.pos < len(self.s) and (self.s[self.pos].isdigit() or self.s[self.pos] == "."):
            self.pos += 1
        return Atom(self.s[start:self.pos])

    def _parse_parens(self) -> TermType:
        self.pos += 1  # skip '('
        terms = self._parse_termlist()
        self.skip_ws()
        if self.pos >= len(self.s) or self.s[self.pos] != ")":
            raise ParseError("Expected ')'")
        self.pos += 1
        # Conjunction of terms: (A, B, C) -> conj(A, conj(B, C))
        if len(terms) == 1:
            return terms[0]
        result = terms[-1]
        for t in reversed(terms[:-1]):
            result = Term(",", (t, result))
        return result

    def _parse_name(self) -> TermType:
        start = self.pos
        while self.pos < len(self.s) and (self.s[self.pos].isalnum() or self.s[self.pos] == "_"):
            self.pos += 1
        name = self.s[start:self.pos]

        # Check for infix operators after the name
        self.skip_ws()

        # Check if this is a compound term
        if self.pos < len(self.s) and self.s[self.pos] == "(":
            self.pos += 1  # skip '('
            args = self._parse_termlist()
            self.skip_ws()
            if self.pos >= len(self.s) or self.s[self.pos] != ")":
                raise ParseError(f"Expected ')' after arguments of {name}")
            self.pos += 1
            term = Term(name, tuple(args))
        elif name == "_":
            term = WILDCARD
        elif name[0].isupper() or name[0] == "_":
            term = Var(name)
        elif name == "true":
            term = Atom("true")
        elif name == "false":
            term = Atom("false")
        else:
            term = Atom(name)

        # Check for infix operator
        return self._maybe_parse_infix(term)

    def _maybe_parse_infix(self, left: TermType) -> TermType:
        """Check for and parse infix operators like \\==, =, :-."""
        self.skip_ws()
        if self.pos >= len(self.s):
            return left

        # \== operator
        if (self.pos + 2 < len(self.s) and
                self.s[self.pos:self.pos + 3] == "\\=="):
            self.pos += 3
            right = self.parse_term()
            return Term("\\==", (left, right))

        # = operator (but not ==)
        if (self.pos < len(self.s) and self.s[self.pos] == "=" and
                (self.pos + 1 >= len(self.s) or self.s[self.pos + 1] != "=")):
            self.pos += 1
            right = self.parse_term()
            return Term("=", (left, right))

        return left

    def _parse_backslash_op(self) -> TermType:
        # \+ (negation)
        if self.pos + 1 < len(self.s) and self.s[self.pos + 1] == "+":
            self.pos += 2
            self.skip_ws()
            arg = self.parse_term()
            return Term("not", (arg,))
        raise ParseError(f"Unknown operator at position {self.pos}")

    def _parse_termlist(self) -> list[TermType]:
        terms = [self.parse_term()]
        while True:
            self.skip_ws()
            if self.pos < len(self.s) and self.s[self.pos] == ",":
                self.pos += 1
                terms.append(self.parse_term())
            else:
                break
        return terms
