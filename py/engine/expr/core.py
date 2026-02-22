"""Expression AST — typed, composable, with operator overloading.

Every expression is a frozen dataclass that builds an AST at definition time.
Operator overloading lets you write `actor.role == "seer"` instead of string DSLs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _wrap(x: Any) -> Expr:
    """Wrap a plain Python value into a Lit node."""
    if isinstance(x, Expr):
        return x
    return Lit(x)


# ---------------------------------------------------------------------------
# Base class with operator overloading
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Expr:
    """Base expression node. All expressions are immutable data."""

    # -- Comparison --
    def __eq__(self, other: Any) -> Cmp:  # type: ignore[override]
        return Cmp("==", self, _wrap(other))

    def __ne__(self, other: Any) -> Cmp:  # type: ignore[override]
        return Cmp("!=", self, _wrap(other))

    def __gt__(self, other: Any) -> Cmp:
        return Cmp(">", self, _wrap(other))

    def __ge__(self, other: Any) -> Cmp:
        return Cmp(">=", self, _wrap(other))

    def __lt__(self, other: Any) -> Cmp:
        return Cmp("<", self, _wrap(other))

    def __le__(self, other: Any) -> Cmp:
        return Cmp("<=", self, _wrap(other))

    # -- Logic (& | ~ because Python can't override and/or/not) --
    def __and__(self, other: Any) -> And:
        left = self.exprs if isinstance(self, And) else (self,)
        right = _wrap(other).exprs if isinstance(other, And) else (_wrap(other),)
        return And((*left, *right))

    def __or__(self, other: Any) -> Or:
        left = self.exprs if isinstance(self, Or) else (self,)
        right = _wrap(other).exprs if isinstance(other, Or) else (_wrap(other),)
        return Or((*left, *right))

    def __invert__(self) -> Not:
        return Not(self)

    # -- Arithmetic --
    def __add__(self, other: Any) -> Arith:
        return Arith("+", self, _wrap(other))

    def __radd__(self, other: Any) -> Arith:
        return Arith("+", _wrap(other), self)

    def __sub__(self, other: Any) -> Arith:
        return Arith("-", self, _wrap(other))

    def __rsub__(self, other: Any) -> Arith:
        return Arith("-", _wrap(other), self)

    def __mul__(self, other: Any) -> Arith:
        return Arith("*", self, _wrap(other))

    def __rmul__(self, other: Any) -> Arith:
        return Arith("*", _wrap(other), self)

    def __truediv__(self, other: Any) -> Arith:
        return Arith("/", self, _wrap(other))

    def __rtruediv__(self, other: Any) -> Arith:
        return Arith("/", _wrap(other), self)

    # -- Attribute access builds Ref paths --
    def __getattr__(self, name: str) -> Ref:
        if name.startswith("_"):
            raise AttributeError(name)
        match self:
            case Ref(parts):
                return Ref(*parts, name)
            case _:
                raise TypeError(
                    f"Cannot access .{name} on {type(self).__name__}. "
                    f"Attribute access is only available on Ref expressions."
                )

    # -- Hashing (needed because __eq__ returns Expr, not bool) --
    def __hash__(self) -> int:
        return id(self)

    def __bool__(self) -> bool:
        raise TypeError(
            "Cannot use Expr in boolean context. "
            "Use & (and), | (or), ~ (not) operators instead of and/or/not."
        )


# ---------------------------------------------------------------------------
# Concrete AST nodes
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True, eq=False)
class Ref(Expr):
    """Path reference. Ref("actor", "role") corresponds to :actor.role"""
    parts: tuple[str, ...]

    def __init__(self, *parts: str):
        object.__setattr__(self, "parts", parts)

    def __repr__(self) -> str:
        return f'Ref({".".join(self.parts)})'

    def __hash__(self) -> int:
        return hash(("Ref", self.parts))


@dataclass(frozen=True, slots=True, eq=False)
class Lit(Expr):
    """Literal value — string, int, float, bool, None."""
    value: Any

    def __repr__(self) -> str:
        return f"Lit({self.value!r})"

    def __hash__(self) -> int:
        try:
            return hash(("Lit", self.value))
        except TypeError:
            return hash(("Lit", id(self.value)))


@dataclass(frozen=True, slots=True, eq=False)
class Cmp(Expr):
    """Comparison: ==, !=, >, >=, <, <="""
    op: str
    lhs: Expr
    rhs: Expr

    def __repr__(self) -> str:
        return f"({self.lhs!r} {self.op} {self.rhs!r})"

    def __hash__(self) -> int:
        return hash(("Cmp", self.op, id(self.lhs), id(self.rhs)))


@dataclass(frozen=True, slots=True, eq=False)
class Arith(Expr):
    """Arithmetic: +, -, *, /"""
    op: str
    lhs: Expr
    rhs: Expr

    def __repr__(self) -> str:
        return f"({self.lhs!r} {self.op} {self.rhs!r})"

    def __hash__(self) -> int:
        return hash(("Arith", self.op, id(self.lhs), id(self.rhs)))


@dataclass(frozen=True, slots=True, eq=False)
class And(Expr):
    """Logical AND — flattens nested Ands via __and__."""
    exprs: tuple[Expr, ...]

    def __repr__(self) -> str:
        return f'And({", ".join(repr(e) for e in self.exprs)})'

    def __hash__(self) -> int:
        return hash(("And", tuple(id(e) for e in self.exprs)))


@dataclass(frozen=True, slots=True, eq=False)
class Or(Expr):
    """Logical OR — flattens nested Ors via __or__."""
    exprs: tuple[Expr, ...]

    def __repr__(self) -> str:
        return f'Or({", ".join(repr(e) for e in self.exprs)})'

    def __hash__(self) -> int:
        return hash(("Or", tuple(id(e) for e in self.exprs)))


@dataclass(frozen=True, slots=True, eq=False)
class Not(Expr):
    """Logical NOT."""
    inner: Expr

    def __repr__(self) -> str:
        return f"Not({self.inner!r})"

    def __hash__(self) -> int:
        return hash(("Not", id(self.inner)))


@dataclass(frozen=True, slots=True, eq=False)
class Call(Expr):
    """Named function call: Call("count_where", (pred,))"""
    fn: str
    args: tuple[Expr, ...]

    def __repr__(self) -> str:
        args_str = ", ".join(repr(a) for a in self.args)
        return f'{self.fn}({args_str})'

    def __hash__(self) -> int:
        return hash(("Call", self.fn, tuple(id(a) for a in self.args)))


@dataclass(frozen=True, slots=True, eq=False)
class If(Expr):
    """Conditional expression."""
    condition: Expr
    then_: Expr
    else_: Expr

    def __repr__(self) -> str:
        return f"If({self.condition!r}, {self.then_!r}, {self.else_!r})"

    def __hash__(self) -> int:
        return hash(("If", id(self.condition), id(self.then_), id(self.else_)))


# ---------------------------------------------------------------------------
# Predefined context variables
# ---------------------------------------------------------------------------

actor = Ref("actor")
target = Ref("target")
game = Ref("game")
proposer = Ref("proposer")
responder = Ref("responder")
subject = Ref("subject")
self_ = Ref("self")
params = Ref("params")
