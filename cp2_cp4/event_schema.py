import json
from datetime import datetime, timezone
from typing import Any, Dict, Tuple

REQUIRED_FIELDS = {
    "event_id": str,
    "device_id": str,
    "timestamp_utc": str,
    "trigger_mode": str,
    "edge_model_version": str,
    "edge_pred_label": str,
    "edge_confidence": (int, float),
    "image_ref": (str, type(None)),
    "payload_version": str,
}

ALLOWED_TRIGGER_MODES = {"inside_bin", "outside_bin"}
SUPPORTED_PAYLOAD_VERSION = "1.0"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_event_payload(payload: Dict[str, Any]) -> Tuple[bool, str]:
    if not isinstance(payload, dict):
        return False, "payload must be a JSON object"

    for field, expected_type in REQUIRED_FIELDS.items():
        if field not in payload:
            return False, f"missing required field: {field}"
        if not isinstance(payload[field], expected_type):
            return False, f"invalid type for field: {field}"

    if payload["trigger_mode"] not in ALLOWED_TRIGGER_MODES:
        return False, "trigger_mode must be inside_bin or outside_bin"

    if payload["payload_version"] != SUPPORTED_PAYLOAD_VERSION:
        return False, "unsupported payload_version"

    confidence = float(payload["edge_confidence"])
    if confidence < 0.0 or confidence > 1.0:
        return False, "edge_confidence must be between 0.0 and 1.0"

    try:
        datetime.fromisoformat(payload["timestamp_utc"].replace("Z", "+00:00"))
    except ValueError:
        return False, "timestamp_utc must be ISO-8601"

    return True, "ok"


def encode_payload(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)


def decode_payload(payload_text: str) -> Dict[str, Any]:
    return json.loads(payload_text)
