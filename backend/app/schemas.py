"""Visit-record shape shared by the extractor, pipeline and HTTP API."""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone, timedelta
from typing import Any

from .danger_signs import Age, signal_level

VISIT_TYPES = ("ANC", "PNC", "IMMUNISATION", "NEWBORN", "GROWTH", "OTHER")
FIELD_TIMEZONE = timezone(timedelta(hours=5, minutes=30))

MONTH_NAMES = (
    ("january", "jan", "जनवरी"), ("february", "feb", "फ़रवरी", "फरवरी"),
    ("march", "mar", "मार्च"), ("april", "apr", "अप्रैल"),
    ("may", "मई"), ("june", "jun", "जून"), ("july", "jul", "जुलाई"),
    ("august", "aug", "अगस्त"), ("september", "sep", "सितंबर", "सितम्बर"),
    ("october", "oct", "अक्टूबर"), ("november", "nov", "नवंबर", "नवम्बर"),
    ("december", "dec", "दिसंबर", "दिसम्बर"),
)
MONTH_BY_NAME = {name: month for month, names in enumerate(MONTH_NAMES, 1) for name in names}
MONTH_PATTERN = "|".join(MONTH_BY_NAME)

# Fields the voice agent is allowed to read back / patch.
EDITABLE_FIELDS = (
    "beneficiaryName",
    "beneficiaryId",
    "visitType",
    "weightKg",
    "bp",
    "hb",
    "vaccination",
    "supplements",
    "nextVisit",
    "notes",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class VisitRecord:
    id: str = field(default_factory=lambda: f"SV-{uuid.uuid4().hex[:8].upper()}")
    beneficiaryName: str | None = None
    beneficiaryId: str | None = None
    age: Age | None = None
    visitType: str = "OTHER"
    weightKg: float | None = None
    bp: str | None = None
    hb: float | None = None
    vaccination: str | None = None
    supplements: list[str] = field(default_factory=list)
    nextVisit: str | None = None
    notes: str | None = None
    dangerSigns: list[dict[str, Any]] = field(default_factory=list)
    followUps: list[str] = field(default_factory=list)
    transcript: str = ""
    corrections: list[dict[str, Any]] = field(default_factory=list)
    createdAt: str = field(default_factory=_now)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def empty_record() -> VisitRecord:
    return VisitRecord()


def missing_fields(record: VisitRecord) -> list[str]:
    required = ("beneficiaryName", "visitType", "weightKg", "nextVisit")
    return [f for f in required if not getattr(record, f)]


def follow_up_lines(record: VisitRecord) -> list[str]:
    """Deterministic follow-up text derived from the record, never free-form."""
    lines: list[str] = []
    when = record.nextVisit or "date not captured"
    if record.visitType == "IMMUNISATION":
        lines.append(f"Next immunisation due: {when}")
    elif record.visitType == "ANC":
        lines.append(f"Next ANC checkup: {when}")
        lines.append("Check BP and weight at next visit")
    elif record.visitType == "PNC":
        lines.append(f"Next postnatal check: {when}")
    elif record.visitType == "NEWBORN":
        lines.append(f"Next newborn follow-up: {when}")
    else:
        lines.append(f"Next visit: {when}")

    if record.vaccination:
        lines.append(f"Record vaccination: {record.vaccination}")
    if any(signal_level(sign) == "urgent" for sign in record.dangerSigns):
        lines.append("Urgent referral flag: confirm referral with the ANM/PHC")
    elif record.dangerSigns:
        lines.append("Check-up needed: contact the ANM/PHC promptly to assess the reported symptoms")
    if record.visitType == "ANC" and not record.hb:
        lines.append("Hb not recorded - capture at next contact")
    return lines


def parse_date_hint(value: str | None, today: date | None = None) -> str | None:
    """Parse a date at the start of a follow-up phrase into ISO."""
    if not value:
        return None
    text = value.strip().lower()
    iso_match = re.match(r"(\d{4}-\d{2}-\d{2})(?!\d)", text)
    if iso_match:
        try:
            return date.fromisoformat(iso_match.group(1)).isoformat()
        except ValueError:
            return None
    today = today or datetime.now(FIELD_TIMEZONE).date()
    for pattern, days in (
        (r"day\s+after\s+tomorrow|parso[n]?|परसों|परसो", 2),
        (r"tomorrow|kal|कल", 1),
        (r"today|aaj|आज", 0),
        (r"next\s+week|agle\s+hafte|अगले\s+हफ्ते|अगले\s+सप्ताह", 7),
    ):
        if re.match(rf"(?:{pattern})(?![\w\u0900-\u0963])", text):
            return (today + timedelta(days=days)).isoformat()

    named = re.match(
        rf"(\d{{1,2}})(?:st|nd|rd|th)?\s+({MONTH_PATTERN})(?![\w\u0900-\u0963])(?:\s+(\d{{4}})(?!\d))?",
        text,
    )
    if named:
        day = int(named[1])
        month = MONTH_BY_NAME[named[2]]
        year = int(named[3]) if named[3] else today.year + ((month, day) < (today.month, today.day))
    else:
        numeric = re.match(
            r"(?:(agle\s+mahine|next\s+month|अगले\s+महीने|is\s+mahine|this\s+month|इस\s+महीने)\s+"
            r"(?:(?:ki|की)\s+)?)?(\d{1,2})(?:st|nd|rd|th)?(?=$|[,.।]|\s+(?:ko|को|tareekh|तारीख[़]?)(?![\w\u0900-\u0963]))",
            text,
        )
        if not numeric:
            return None
        day = int(numeric[2])
        year, month = today.year, today.month
        if numeric[1] in {"agle mahine", "next month", "अगले महीने"} or (not numeric[1] and day < today.day):
            month = month + 1 if month < 12 else 1
            year += month == 1
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None
