"""Parse execution controls consistently for the public JSON routes."""
from fastapi import HTTPException


def execution_flag(body: dict, name: str, alias: str, default: bool | None) -> bool | None:
    values = []
    for field in (name, alias):
        if field not in body:
            continue
        value = body[field]
        if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
            value = value.strip().lower() == "true"
        if not isinstance(value, bool):
            raise HTTPException(400, f"{field} must be true or false")
        values.append(value)
    if len(set(values)) > 1:
        raise HTTPException(400, f"Conflicting values for {name} and {alias}")
    return values[0] if values else default
