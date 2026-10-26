"""Small, testable adapter for Chuangmi IR protocol responses."""

import base64
import binascii
import re

from miio import ChuangmiIr
from miio.exceptions import DeviceError

from .const import MODEL


def response_shape(value, depth=0):
    """Describe structure only; never include strings, IR codes or arbitrary keys."""
    if value is None:
        return "null"
    if type(value) in (bool, int, float):
        return type(value).__name__
    if isinstance(value, str):
        return f"str(length={len(value)})"
    if depth >= 2:
        return type(value).__name__
    if isinstance(value, list):
        items = ",".join(response_shape(item, depth + 1) for item in value[:2])
        return f"list(length={len(value)},items=[{items}])"
    if isinstance(value, dict):
        fields = ("result", "error", "key", "code", "message", "id")
        parts = [
            f"{key}={response_shape(value[key], depth + 1)}"
            for key in fields
            if key in value
        ]
        other = len(value) - sum(key in value for key in fields)
        if other:
            parts.append(f"other_fields={other}")
        return "dict(" + ",".join(parts) + ")"
    return "unsupported_type"


class InvalidResponse(ValueError):
    """The device returned an unexpected response, with payload-free details."""

    def __init__(self, message, response=None):
        super().__init__(message)
        self.response_shape = response_shape(response)


def create_device(host: str, token: str) -> ChuangmiIr:
    """Create a local client without replaying ambiguous IR transmissions."""
    device = ChuangmiIr(host, token, lazy_discover=False, timeout=5, model=MODEL)
    device.retry_count = 0
    return device


def validate_token(token: str) -> str:
    """Accept exactly 16 bytes encoded as hexadecimal."""
    token = token.strip()
    if not re.fullmatch(r"[0-9a-fA-F]{32}", token):
        raise ValueError("invalid_token")
    return token.lower()


def mac_id(mac: str) -> str:
    """Use a stable, validated device identifier."""
    if not isinstance(mac, str):
        raise InvalidResponse("Missing device MAC address")
    value = re.sub(r"[:-]", "", mac).lower()
    if not re.fullmatch(r"[0-9a-f]{12}", value) or value == "000000000000":
        raise InvalidResponse("Invalid device MAC address")
    return value


def check_ack(response, *, allow_zero: bool = False) -> None:
    """Accept known acknowledgements, including legacy ir_play/ir_learn zero.

    Zero is enabled only at the corresponding command call sites, not for
    arbitrary miIO methods. Do not equate False or 0.0 with the integer protocol status 0.
    """
    original_response = response
    if isinstance(response, dict):
        if not set(response) <= {"result", "id"} or "result" not in response:
            raise InvalidResponse(
                "Device did not acknowledge the request", original_response
            )
        response = response["result"]
    if isinstance(response, list) and len(response) == 1:
        response = response[0]
    if type(response) is str and response == "ok":
        return
    if allow_zero and type(response) is int and response == 0:
        return
    raise InvalidResponse("Device did not acknowledge the request", original_response)


def transmission_error_message(error: Exception) -> str:
    """Expose the failure category without response payloads or secrets."""
    reason = (
        "unexpected acknowledgement"
        if isinstance(error, InvalidResponse)
        else type(error).__name__
    )
    return (
        f"IR transmission failed ({reason}). It may have been sent; it was not retried."
    )


def _read_pending(code, message=None) -> bool:
    """Legacy firmware reports a known empty-slot condition with code zero."""
    if type(code) is not int:
        return False
    if code in (-5002, -5003):
        return True
    return code == 0 and message in ("no code for this key", "learn timeout")


def read_code(device: ChuangmiIr, slot: int) -> str | None:
    """Return a learned code; only documented pending responses are ignored."""
    try:
        result = device.read(slot)
    except DeviceError as err:
        if _read_pending(err.code, getattr(err, "message", None)):
            return None
        raise
    if isinstance(result, dict) and isinstance(result.get("error"), dict):
        if _read_pending(result["error"].get("code"), result["error"].get("message")):
            return None
        raise InvalidResponse("IR read failed", result)
    if not isinstance(result, dict) or str(result.get("key")) != str(slot):
        raise InvalidResponse("Unexpected IR slot response", result)
    code = result.get("code")
    # Observed on the receiver while learning: a matching slot with an empty
    # string means no signal has been captured yet, not a successfully learned code.
    if code == "":
        return None
    if not isinstance(code, str):
        raise InvalidResponse("Missing learned IR code", result)
    try:
        if not base64.b64decode(code, validate=True):
            raise InvalidResponse("Empty learned IR code", result)
    except (binascii.Error, ValueError) as err:
        raise InvalidResponse("Invalid learned IR code", result) from err
    return f"raw:{code}"
