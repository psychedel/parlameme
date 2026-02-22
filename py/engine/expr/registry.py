"""Open function registry for expression evaluation.

Games can register custom functions without editing evaluator.py.
Core functions are registered at import time via @fn_registry.register().
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from engine.expr.evaluator import Context


@dataclass(frozen=True)
class FunctionDef:
    name: str
    handler: Callable  # (args: tuple, ctx: Context) -> Any
    doc: str = ""
    min_args: int = 0
    max_args: int | None = None


class FunctionRegistry:
    """Registry for expression functions.

    Usage:
        @fn_registry.register("alive?", doc="Check if entity is active")
        def _alive(args, ctx):
            ...
    """

    def __init__(self):
        self._fns: dict[str, FunctionDef] = {}

    def register(
        self,
        name: str,
        *,
        doc: str = "",
        min_args: int = 0,
        max_args: int | None = None,
    ):
        """Decorator for registering expression functions."""

        def decorator(fn: Callable) -> Callable:
            self._fns[name] = FunctionDef(
                name=name,
                handler=fn,
                doc=doc,
                min_args=min_args,
                max_args=max_args,
            )
            return fn

        return decorator

    def call(self, name: str, args: tuple, ctx: Context) -> Any:
        fdef = self._fns.get(name)
        if fdef is None:
            raise ValueError(
                f"Unknown function: {name}. Registered: {sorted(self._fns.keys())}"
            )
        nargs = len(args)
        if nargs < fdef.min_args:
            raise ValueError(f"{name} needs >= {fdef.min_args} args, got {nargs}")
        if fdef.max_args is not None and nargs > fdef.max_args:
            raise ValueError(f"{name} accepts <= {fdef.max_args} args, got {nargs}")
        return fdef.handler(args, ctx)

    def has(self, name: str) -> bool:
        return name in self._fns

    def list_functions(self) -> list[FunctionDef]:
        """List all registered functions — useful for MCP schema generation."""
        return sorted(self._fns.values(), key=lambda f: f.name)


# Global registry — import and use this everywhere
fn_registry = FunctionRegistry()
