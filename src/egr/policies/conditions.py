"""Safe condition evaluator for policy rules.

No `eval`, no imports, no attribute access: only literals, names from the
evaluation context, boolean operators, comparisons, subscripts and a small
whitelist of functions. Anything else raises ConditionError, which the engine
treats as DENY (fail closed).
"""

from __future__ import annotations

import ast
import operator
from collections.abc import Mapping
from typing import Any

from ..core.errors import ConditionError

_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
}

_CMPOPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda left, right: left in right if right is not None else False,
    ast.NotIn: lambda left, right: left not in right if right is not None else False,
}

_FUNCTIONS = {
    "len": len,
    "abs": abs,
    "min": min,
    "max": max,
    "sum": lambda value: sum(value),
    "any": any,
    "all": all,
    "str": str,
    "int": int,
    "float": float,
    "round": round,
    "lower": lambda value: str(value).lower(),
    "upper": lambda value: str(value).upper(),
    "contains": lambda haystack, needle: needle in haystack if haystack is not None else False,
    "startswith": lambda value, prefix: str(value).startswith(prefix),
    "endswith": lambda value, suffix: str(value).endswith(suffix),
}


class Condition:
    """A compiled policy condition."""

    def __init__(self, expression: str | None):
        self.expression = (expression or "true").strip()
        try:
            self.tree = ast.parse(self.expression, mode="eval")
        except SyntaxError as exc:
            raise ConditionError(f"invalid condition '{self.expression}': {exc}") from exc

    def evaluate(self, context: Mapping[str, Any]) -> bool:
        try:
            value = self._eval(self.tree.body, context)
        except ConditionError:
            raise
        except Exception as exc:  # defensive: anything unexpected fails closed
            raise ConditionError(f"failed to evaluate '{self.expression}': {exc}") from exc
        return bool(value)

    # ---- internals ---------------------------------------------------
    def _eval(self, node: ast.AST, context: Mapping[str, Any]) -> Any:
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in ("true", "True"):
                return True
            if node.id in ("false", "False"):
                return False
            if node.id == "None":
                return None
            return context.get(node.id)
        if isinstance(node, ast.BoolOp):
            values = [self._eval(value, context) for value in node.values]
            if isinstance(node.op, ast.And):
                return all(values)
            return any(values)
        if isinstance(node, ast.UnaryOp):
            operand = self._eval(node.operand, context)
            if isinstance(node.op, ast.Not):
                return not operand
            if isinstance(node.op, ast.USub):
                return -operand
            raise ConditionError(f"unsupported unary operator in '{self.expression}'")
        if isinstance(node, ast.BinOp):
            handler = _BINOPS.get(type(node.op))
            if handler is None:
                raise ConditionError(f"unsupported operator in '{self.expression}'")
            return handler(self._eval(node.left, context), self._eval(node.right, context))
        if isinstance(node, ast.Compare):
            left = self._eval(node.left, context)
            for op, comparator in zip(node.ops, node.comparators, strict=False):
                right = self._eval(comparator, context)
                if not self._compare(op, left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.Subscript):
            target = self._eval(node.value, context)
            key = self._eval(node.slice, context)
            try:
                return target[key]
            except Exception as exc:
                raise ConditionError(f"cannot access key '{key}': {exc}") from exc
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ConditionError("only whitelisted functions may be called")
            function = _FUNCTIONS.get(node.func.id)
            if function is None:
                raise ConditionError(f"function '{node.func.id}' is not allowed in policy conditions")
            return function(*[self._eval(arg, context) for arg in node.args])
        if isinstance(node, (ast.List, ast.Tuple)):
            return [self._eval(item, context) for item in node.elts]
        raise ConditionError(f"unsupported expression in '{self.expression}'")

    def _compare(self, op: ast.AST, left: Any, right: Any) -> bool:
        handler = _CMPOPS.get(type(op))
        if handler is None:
            raise ConditionError(f"unsupported comparison in '{self.expression}'")
        if left is None or right is None:
            # fail closed on missing data: unknown facts do not authorize
            if isinstance(op, ast.Eq):
                return left is right
            if isinstance(op, ast.NotEq):
                return left is not right
            return False
        try:
            return bool(handler(left, right))
        except TypeError:
            return False


def evaluate(expression: str | None, context: Mapping[str, Any]) -> bool:
    return Condition(expression).evaluate(context)
