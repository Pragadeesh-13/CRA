"""Shared JSON body and field checks for public HTTP endpoints."""

import json
import math
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

OBJECT_ERROR = "Request body must be a JSON object"
COMPLAINT_FIELDS = ("complaint", "query", "text", "message", "ticket", "description", "body")
CUSTOMER_FIELDS = ("customer_id", "customerId")
ORDER_FIELDS = ("order_id", "orderId")
TRANSACTION_FIELDS = ("transaction_id", "transactionId")
REPLAY_FIELDS = ("idempotency_key", "idempotencyKey")
ID_TYPES = (str, int)


class EnvelopeError(ValueError):
    """An invalid field in a request envelope."""


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("JSON number is out of range")
    return number


async def read_json_object(
    request: Request, *, allow_empty: bool = False, exact_numbers: bool = False,
) -> dict | None:
    raw = await request.body()
    if allow_empty and not raw:
        return {}
    try:
        body = json.loads(
            raw, parse_float=Decimal if exact_numbers else _finite_float, parse_constant=_reject_json_constant,
        )
    except (ValueError, InvalidOperation, RecursionError):
        return None
    return body if isinstance(body, dict) else None


def envelope_error(message: str = OBJECT_ERROR) -> JSONResponse:
    return JSONResponse(status_code=400, content={"ok": False, "status": "Failed", "error": message})


def check_field(value: Any, field: str, kind: str, types: tuple[type, ...]) -> Any:
    # Exact types keep JSON booleans from being accepted as integer identifiers.
    if value is not None and type(value) not in types:
        raise EnvelopeError(f"{field} must be {kind}.")
    return value


def read_field(body: dict, *aliases: str, kind: str, types: tuple[type, ...]) -> Any:
    selected = None
    for name in aliases:
        value = check_field(body.get(name), name, kind, types)
        if selected is None and value is not None and value != "":
            selected = value
    return selected


def resolution_fields(body: dict, idempotency_header: str | None) -> dict:
    context = read_field(body, "context", kind="an object", types=(dict,))
    for field in (*CUSTOMER_FIELDS, *ORDER_FIELDS, *TRANSACTION_FIELDS, "txn_id", "invoice_id", "subscription_id", "event_id"):
        check_field((context or {}).get(field), f"context.{field}", "text or an integer", ID_TYPES)
    for field in ("category", "idempotency_key"):
        check_field((context or {}).get(field), f"context.{field}", "text", (str,))
    for field in ("amount", "credit_amount"):
        check_field((context or {}).get(field), f"context.{field}", "text or a number", (str, int, float))

    key = read_field(body, *REPLAY_FIELDS, kind="text", types=(str,))
    return {
        "context": context,
        "customer_id": read_field(body, *CUSTOMER_FIELDS, kind="text or an integer", types=ID_TYPES),
        "order_id": read_field(body, *ORDER_FIELDS, kind="text or an integer", types=ID_TYPES),
        "transaction_id": read_field(body, *TRANSACTION_FIELDS, kind="text or an integer", types=ID_TYPES),
        "idempotency_key": idempotency_header or key,
    }
