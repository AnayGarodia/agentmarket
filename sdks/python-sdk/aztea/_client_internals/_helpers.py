from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from typing import TYPE_CHECKING, Any

from ..errors import AzteaError, ContractVerificationError
from ..models import VerificationContract
from ..types import JSONObject

if TYPE_CHECKING:
    from .client_core import AzteaClient


def _ensure_object(value: Any, *, context: str) -> JSONObject:
    if isinstance(value, dict):
        return value
    raise AzteaError(f"{context} expected a JSON object response, got: {type(value).__name__}.")


def _coerce_payload(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _coerce_model(model_type: Any, value: Any) -> Any:
    if not isinstance(value, dict):
        raise AzteaError(f"Expected object payload for {getattr(model_type, '__name__', 'model')}.")
    if not is_dataclass(model_type):
        return model_type(**value)
    allowed = {item.name for item in fields(model_type)}
    payload = {key: raw for key, raw in value.items() if key in allowed}
    return model_type(**payload)


def _verify_contract(output: dict[str, Any], contract: VerificationContract) -> None:
    failures: list[str] = []
    for key in contract.required_keys:
        if key not in output:
            failures.append(f"Missing required key: {key}")
    for key, expected in contract.field_types.items():
        if key not in output:
            continue
        value = output[key]
        kind = str(expected).strip().lower()
        if kind == "string" and not isinstance(value, str):
            failures.append(f"{key} expected string, got {type(value).__name__}")
        elif kind == "number" and not isinstance(value, (int, float)):
            failures.append(f"{key} expected number, got {type(value).__name__}")
        elif kind == "boolean" and not isinstance(value, bool):
            failures.append(f"{key} expected boolean, got {type(value).__name__}")
        elif kind == "array" and not isinstance(value, list):
            failures.append(f"{key} expected array, got {type(value).__name__}")
        elif kind == "object" and not isinstance(value, dict):
            failures.append(f"{key} expected object, got {type(value).__name__}")
    for key, bounds in contract.field_ranges.items():
        if key not in output or not isinstance(output[key], (int, float)) or not isinstance(bounds, dict):
            continue
        if "min" in bounds and output[key] < bounds["min"]:
            failures.append(f"{key} is below minimum {bounds['min']}")
        if "max" in bounds and output[key] > bounds["max"]:
            failures.append(f"{key} is above maximum {bounds['max']}")
    if failures:
        raise ContractVerificationError(failures)


@dataclass
class _NamespaceBase:
    _client: "AzteaClient"
