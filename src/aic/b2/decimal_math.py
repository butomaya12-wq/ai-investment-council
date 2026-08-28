from __future__ import annotations

from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from typing import Iterable


DECIMAL_CONTEXT_ID = "DECIMAL128_34_HALF_EVEN_V1"
_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)


def decimal_add(left: Decimal, right: Decimal) -> Decimal:
    with localcontext(_CONTEXT):
        return +(left + right)


def decimal_subtract(left: Decimal, right: Decimal) -> Decimal:
    with localcontext(_CONTEXT):
        return +(left - right)


def decimal_multiply(left: Decimal, right: Decimal) -> Decimal:
    with localcontext(_CONTEXT):
        return +(left * right)


def decimal_divide(numerator: Decimal, denominator: Decimal) -> Decimal:
    if denominator == 0:
        raise ZeroDivisionError("decimal denominator must not be zero")
    with localcontext(_CONTEXT):
        return +(numerator / denominator)


def decimal_sum(values: Iterable[Decimal]) -> Decimal:
    total = Decimal("0")
    with localcontext(_CONTEXT):
        for value in values:
            total = +(total + value)
    return total
