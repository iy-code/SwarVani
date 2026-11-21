"""Verified WhatsApp reply handling for follow-up status synchronization."""

from __future__ import annotations

import hashlib
import hmac
import re
import httpx
from fastapi import HTTPException
from .config import settings
from typing import Any, Iterator

from . import db

PAYLOAD_PREFIX = "swarvani_followup"


async def send_test_followup(record_id: str) -> str:
    """Send only to the server-configured test recipient; never include clinical data."""
    if not settings.whatsapp_sending_ready or not re.fullmatch(r"[1-9][0-9]{7,14}", settings.whatsapp_test_recipient):
        raise HTTPException(503, "Set Meta credentials and WHATSAPP_TEST_RECIPIENT (country code and digits only) in backend/.env.")
    payload = {
        "messaging_product": "whatsapp", "to": settings.whatsapp_test_recipient,
        "type": "interactive", "interactive": {
            "type": "button", "body": {"text": "SwarVani test follow-up. Please select a response."},
            "action": {"buttons": [
                {"type": "reply", "reply": {"id": quick_reply_payload(record_id, status), "title": title}}
                for status, title in [("confirmed", "Confirm"), ("reschedule_requested", "Reschedule"), ("asha_callback_requested", "Request ASHA call")]
            ]},
        },
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"https://graph.facebook.com/{settings.whatsapp_api_version}/{settings.whatsapp_phone_number_id}/messages",
                headers={"Authorization": f"Bearer {settings.whatsapp_access_token}"}, json=payload,
            )
        if response.is_error:
            raise HTTPException(502, "Meta rejected the message. Check the token and test recipient, and message the test number first to open the 24-hour reply window.")
        return response.json()["messages"][0]["id"]
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        raise HTTPException(502, "Could not confirm Meta accepted the message. Check WhatsApp before retrying.") from None

STATUS_ALIASES = {
    "confirmed": "confirmed",
    "confirm": "confirmed",
    "yes": "confirmed",
    "haan": "confirmed",
    "reschedule": "reschedule_requested",
    "reschedule_requested": "reschedule_requested",
    "change_date": "reschedule_requested",
    "callback": "asha_callback_requested",
    "request_callback": "asha_callback_requested",
    "asha_callback_requested": "asha_callback_requested",
    "no_response": "no_response",
}


def quick_reply_payload(record_id: str, status: str) -> str:
    if status not in db.WHATSAPP_STATUSES:
        raise ValueError(f"unsupported WhatsApp status: {status}")
    return f"{PAYLOAD_PREFIX}|{record_id}|{status}"


def verify_signature(body: bytes, signature: str | None, app_secret: str) -> bool:
    if not signature or not app_secret:
        return False
    expected = "sha256=" + hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def _messages(payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for entry in payload.get("entry", []):
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes", []):
            value = change.get("value", {}) if isinstance(change, dict) else {}
            for message in value.get("messages", []):
                if isinstance(message, dict):
                    yield message


def _reply_value(message: dict[str, Any]) -> tuple[str, str]:
    interactive = message.get("interactive") or {}
    button_reply = interactive.get("button_reply") or {}
    if button_reply.get("id"):
        return str(button_reply["id"]), str(button_reply.get("title") or "")
    button = message.get("button") or {}
    if button.get("payload"):
        return str(button["payload"]), str(button.get("text") or "")
    text = message.get("text") or {}
    return str(text.get("body") or ""), str(text.get("body") or "")


def parse_reply(message: dict[str, Any]) -> tuple[str, str, str] | None:
    raw, label = _reply_value(message)
    parts = raw.strip().split("|")
    if len(parts) == 3 and parts[0] == PAYLOAD_PREFIX:
        record_id = parts[1].strip()
        status = STATUS_ALIASES.get(parts[2].strip().lower())
        if record_id and status:
            return record_id, status, label or parts[2]

    # Text replies are accepted only when they include a SwarVani record ID,
    # preventing an unrelated WhatsApp message from changing a visit.
    record_match = re.search(r"\bSV-[A-Z0-9]{4,16}\b", raw.upper())
    normalized = re.sub(r"[^a-z_ ]", "", raw.lower()).strip().replace(" ", "_")
    status = STATUS_ALIASES.get(normalized)
    if record_match and status:
        return record_match.group(0), status, label or raw
    return None


def apply_webhook(payload: dict[str, Any]) -> list[dict[str, str]]:
    updates: list[dict[str, str]] = []
    for message in _messages(payload):
        parsed = parse_reply(message)
        if not parsed:
            continue
        record_id, status, reply = parsed
        if db.update_whatsapp_status(
            record_id,
            status,
            last_reply=reply[:240],
            message_id=str(message.get("id") or "") or None,
        ):
            updates.append({"recordId": record_id, "status": status})
    return updates
