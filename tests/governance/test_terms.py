"""Tests for term representation, parsing, and unification."""

import pytest

from aorta4llm.governance.terms import (
    Atom, Term, Var, WILDCARD,
    apply_subst, is_ground, parse_term, term_to_str, unify,
)


class TestParseTerm:
    """Test the term parser."""

    def test_parse_bare_atom(self):
        assert parse_term("hello") == Atom("hello")

    def test_parse_quoted_atom(self):
        assert parse_term("'src/auth/login.py'") == Atom("src/auth/login.py")

    def test_parse_variable(self):
        assert parse_term("Path") == Var("Path")

    def test_parse_underscore_variable(self):
        assert parse_term("_Deadline") == Var("_Deadline")

    def test_parse_wildcard(self):
        assert parse_term("_") == WILDCARD

    def test_parse_simple_compound(self):
        result = parse_term("write_file(Path)")
        assert result == Term("write_file", (Var("Path"),))

    def test_parse_compound_with_quoted_atom(self):
        result = parse_term("write_file('src/auth/login.py')")
        assert result == Term("write_file", (Atom("src/auth/login.py"),))

    def test_parse_nested_compound(self):
        result = parse_term("not(in_scope(Path, Scope))")
        expected = Term("not", (Term("in_scope", (Var("Path"), Var("Scope"))),))
        assert result == expected

    def test_parse_boolean_atoms(self):
        assert parse_term("true") == Atom("true")
        assert parse_term("false") == Atom("false")

    def test_parse_empty_list(self):
        assert parse_term("[]") == Atom("[]")

    def test_parse_cond_fact(self):
        """Parse a full cond/5 fact as the compiler emits."""
        result = parse_term(
            "cond(implementer, forbidden, write_file(Path), false, not(in_scope(Path, AssignedScope)))"
        )
        assert isinstance(result, Term)
        assert result.functor == "cond"
        assert len(result.args) == 5
        assert result.args[0] == Atom("implementer")
        assert result.args[1] == Atom("forbidden")
        assert result.args[2] == Term("write_file", (Var("Path"),))
        assert result.args[3] == Atom("false")
        assert result.args[4] == Term("not", (Term("in_scope", (Var("Path"), Var("AssignedScope"))),))

    def test_parse_role_fact(self):
        """Parse a role/2 fact with list argument."""
        result = parse_term("role(implementer, [feature_implemented(F), tests_passing(F)])")
        assert isinstance(result, Term)
        assert result.functor == "role"
        assert result.args[0] == Atom("implementer")

    def test_parse_backslash_negation(self):
        result = parse_term("\\+ achieved(Obj)")
        assert result == Term("not", (Term("achieved", (Var("Obj"),)),))

    def test_parse_inequality(self):
        result = parse_term("X \\== false")
        assert result == Term("\\==", (Var("X"), Atom("false")))

    def test_parse_conjunction(self):
        result = parse_term("(a, b)")
        assert result == Term(",", (Atom("a"), Atom("b")))

    def test_parse_trailing_whitespace(self):
        assert parse_term("  hello  ") == Atom("hello")

    def test_parse_number(self):
        assert parse_term("42") == Atom("42")


class TestUnify:
    """Test structural unification."""

    def test_atom_atom_same(self):
        assert unify(Atom("a"), Atom("a")) == {}

    def test_atom_atom_different(self):
        assert unify(Atom("a"), Atom("b")) is None

    def test_var_atom(self):
        result = unify(Var("X"), Atom("hello"))
        assert result == {"X": Atom("hello")}

    def test_atom_var(self):
        result = unify(Atom("hello"), Var("X"))
        assert result == {"X": Atom("hello")}

    def test_var_var(self):
        result = unify(Var("X"), Var("Y"))
        assert result is not None
        # One should be bound to the other
        assert "X" in result or "Y" in result

    def test_wildcard_matches_anything(self):
        assert unify(WILDCARD, Atom("anything")) == {}
        assert unify(Atom("anything"), WILDCARD) == {}
        assert unify(WILDCARD, WILDCARD) == {}

    def test_compound_same_functor(self):
        t1 = Term("f", (Var("X"), Atom("b")))
        t2 = Term("f", (Atom("a"), Atom("b")))
        result = unify(t1, t2)
        assert result == {"X": Atom("a")}

    def test_compound_different_functor(self):
        t1 = Term("f", (Atom("a"),))
        t2 = Term("g", (Atom("a"),))
        assert unify(t1, t2) is None

    def test_compound_different_arity(self):
        t1 = Term("f", (Atom("a"),))
        t2 = Term("f", (Atom("a"), Atom("b")))
        assert unify(t1, t2) is None

    def test_write_file_unification(self):
        """The core governance use case: unify action with prohibition pattern."""
        pattern = Term("write_file", (Var("Path"),))
        action = Term("write_file", (Atom("src/auth/login.py"),))
        result = unify(pattern, action)
        assert result == {"Path": Atom("src/auth/login.py")}

    def test_nested_unification(self):
        """Unify nested terms with shared variables."""
        t1 = Term("cond", (
            Atom("implementer"),
            Atom("forbidden"),
            Term("write_file", (Var("Path"),)),
        ))
        t2 = Term("cond", (
            Atom("implementer"),
            Atom("forbidden"),
            Term("write_file", (Atom("src/api/routes.py"),)),
        ))
        result = unify(t1, t2)
        assert result is not None
        assert result["Path"] == Atom("src/api/routes.py")

    def test_shared_variable_binding(self):
        """Variables with the same name in different positions bind consistently."""
        t1 = Term("pair", (Var("X"), Var("X")))
        t2 = Term("pair", (Atom("a"), Atom("a")))
        result = unify(t1, t2)
        assert result is not None
        assert result["X"] == Atom("a")

    def test_shared_variable_conflict(self):
        """Same variable can't bind to two different values."""
        t1 = Term("pair", (Var("X"), Var("X")))
        t2 = Term("pair", (Atom("a"), Atom("b")))
        assert unify(t1, t2) is None

    def test_unify_with_existing_substitution(self):
        subst = {"X": Atom("hello")}
        result = unify(Var("X"), Atom("hello"), subst)
        assert result is not None

    def test_unify_conflict_with_existing_substitution(self):
        subst = {"X": Atom("hello")}
        result = unify(Var("X"), Atom("world"), subst)
        assert result is None


class TestApplySubst:
    """Test substitution application."""

    def test_apply_to_var(self):
        result = apply_subst(Var("X"), {"X": Atom("hello")})
        assert result == Atom("hello")

    def test_apply_to_unbound_var(self):
        result = apply_subst(Var("Y"), {"X": Atom("hello")})
        assert result == Var("Y")

    def test_apply_to_atom(self):
        result = apply_subst(Atom("hello"), {"X": Atom("world")})
        assert result == Atom("hello")

    def test_apply_to_compound(self):
        term = Term("f", (Var("X"), Atom("b")))
        result = apply_subst(term, {"X": Atom("a")})
        assert result == Term("f", (Atom("a"), Atom("b")))

    def test_apply_nested(self):
        term = Term("not", (Term("in_scope", (Var("Path"), Var("Scope"))),))
        subst = {"Path": Atom("src/auth/login.py"), "Scope": Atom("src/api/")}
        result = apply_subst(term, subst)
        expected = Term("not", (Term("in_scope", (Atom("src/auth/login.py"), Atom("src/api/"))),))
        assert result == expected


class TestIsGround:
    """Test groundness checking."""

    def test_atom_is_ground(self):
        assert is_ground(Atom("hello")) is True

    def test_var_is_not_ground(self):
        assert is_ground(Var("X")) is False

    def test_wildcard_is_ground(self):
        assert is_ground(WILDCARD) is True

    def test_ground_compound(self):
        assert is_ground(Term("f", (Atom("a"), Atom("b")))) is True

    def test_non_ground_compound(self):
        assert is_ground(Term("f", (Atom("a"), Var("X")))) is False

    def test_ground_with_substitution(self):
        assert is_ground(Var("X"), {"X": Atom("a")}) is True


class TestTermToStr:
    """Test term-to-string conversion."""

    def test_atom(self):
        assert term_to_str(Atom("hello")) == "hello"

    def test_var(self):
        assert term_to_str(Var("X")) == "X"

    def test_compound(self):
        assert term_to_str(Term("f", (Atom("a"), Atom("b")))) == "f(a, b)"

    def test_nested(self):
        t = Term("write_file", (Atom("src/auth/login.py"),))
        assert term_to_str(t) == "write_file(src/auth/login.py)"
