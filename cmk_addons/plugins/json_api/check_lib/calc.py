#!/usr/bin/env python3
# Copyright (C) 2026 Benjamin Knapp
# SPDX-License-Identifier: GPL-2.0-only
"""The arithmetic transform a rule may apply to a numeric value.

On its own because it is the one part of the check that evaluates something a
rule wrote - which is easier to review, and to keep free of ``eval``, in a
module that does nothing else.
"""

import ast


def _apply_calc(value: float, expr: str, other: float | None = None) -> float:
    """Evaluate a small arithmetic expression over ``value`` (and ``other``).

    Only numeric literals, the names ``value`` and ``other``, parentheses and
    + - * / (incl. unary +/-) are supported; anything else raises
    ``ValueError``. This walks the AST and never uses ``eval``, so a rule cannot
    smuggle in code execution. Operators are dispatched with explicit
    ``isinstance`` branches (rather than a lookup table of ``operator``
    callables) so the arithmetic stays statically typed.

    ``other`` is the second path's value, resolved by the agent. An expression
    naming it when it did not resolve is an error rather than a default: silently
    substituting 0 or 1 would turn a missing field into a plausible-looking
    ratio.
    """

    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.BinOp):
            left, right = _eval(node.left), _eval(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            raise ValueError("unsupported operator")
        if isinstance(node, ast.UnaryOp):
            operand = _eval(node.operand)
            if isinstance(node.op, ast.UAdd):
                return +operand
            if isinstance(node.op, ast.USub):
                return -operand
            raise ValueError("unsupported operator")
        if (
            isinstance(node, ast.Constant)
            and not isinstance(node.value, bool)
            and isinstance(node.value, (int, float))
        ):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id == "value":
                return value
            if node.id == "other":
                if other is None:
                    raise ValueError("the second path did not resolve to a number")
                return other
        raise ValueError("unsupported expression")

    return float(_eval(ast.parse(expr, mode="eval")))
