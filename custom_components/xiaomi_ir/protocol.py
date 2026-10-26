"""Small, testable adapter for Chuangmi IR protocol responses."""

import base64
import binascii
import re

from miio import ChuangmiIr
from miio.exceptions import DeviceError

from .const import MODEL


class InvalidResponse(ValueError):
    """The device returned an unexpected response."""


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


def check_ack(response) -> None:
    """Reject errors or empty/unexpected acknowledgements."""
    if response not in ("ok", ["ok"], {"result": "ok"}, {"result": ["ok"]}):
        raise InvalidResponse("Device did not acknowledge the request")


def read_code(device: ChuangmiIr, slot: int) -> str | None:
    """Return a learned code; only documented pending responses are ignored."""
    try:
        result = device.read(slot)
    except DeviceError as err:
        if err.code in (-5002, -5003):
            return None
        raise
    if isinstance(result, dict) and isinstance(result.get("error"), dict):
        if result["error"].get("code") in (-5002, -5003):
            return None
        raise InvalidResponse("IR read failed")
    if not isinstance(result, dict) or str(result.get("key")) != str(slot):
        raise InvalidResponse("Unexpected IR slot response")
    code = result.get("code")
    if not isinstance(code, str) or not code:
        raise InvalidResponse("Missing learned IR code")
    try:
        if not base64.b64decode(code, validate=True):
            raise InvalidResponse("Empty learned IR code")
    except (binascii.Error, ValueError) as err:
        raise InvalidResponse("Invalid learned IR code") from err
    return f"raw:{code}"
