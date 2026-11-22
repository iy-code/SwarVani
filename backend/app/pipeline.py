"""Voice session state machine.

Framework-free on purpose: the FastAPI route only moves JSON in and out, so the
whole flow (disclosure -> danger scan -> extraction -> read-back -> field-level
correction -> save) is testable without a socket.

Order of operations per utterance is fixed and safety-first:
    danger scan  ->  extraction  ->  record update  ->  read-back
The referral alert is emitted *before* the read-back text so the worker hears
it first, even if the rest of the form is still incomplete.
"""

from __future__ import annotations

import re
import uuid
from datetime import date
from typing import Any

from . import danger_signs
from .config import settings
from .normalization import latin_name
from .extract import apply_to_record, extract_heuristic, extract_with_llm
from .schemas import VisitRecord, empty_record, follow_up_lines, missing_fields

MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

CONFIRM_WORDS = ("haan", "han", "ha", "yes", "sahi hai", "theek hai", "thik hai", "correct", "ok", "okay", "हाँ", "हां")
CORRECTION_MARKERS = ("nahi", "no", "galat", "galti", "correction", "actually", "sorry", "नहीं", "गलत")
NAME_ONLY_RE = re.compile(r"[A-Za-z\u0900-\u097F]+(?:[ '-][A-Za-z\u0900-\u097F]+){0,2}")
NON_NAME_ANSWERS = {
    "haan", "han", "ha", "yes", "ok", "okay", "nahi", "nahin", "no",
    "ji", "jee", "anc", "pnc", "visit", "newborn", "baby", "mother",
    "naam", "name", "weight", "wajan", "जी", "हाँ", "हां", "नहीं",
}


def _fmt_weight(weight: float | None) -> str:
    if weight is None:
        return ""
    return f"{weight:g} kilo"


def _fmt_date(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{d.day} {MONTHS[d.month - 1]}"


class VisitSession:
    def __init__(self, session_id: str | None = None) -> None:
        self.id = session_id or uuid.uuid4().hex[:10]
        self.record: VisitRecord = empty_record()
        self.state = "disclosure"
        self.utterances: list[str] = []
        self.extraction_source = "heuristic"
        self.saved = False

    # ---------------------------------------------------------------- inbound

    async def handle(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        kind = (message.get("type") or "").strip()
        if kind == "start":
            return self._start()
        if kind == "utterance":
            return await self._utterance(str(message.get("text") or ""))
        if kind == "correct":
            return await self._correction(str(message.get("text") or ""))
        if kind == "confirm":
            return self._confirm()
        if kind == "fever_age":
            return self._set_fever_age(message.get("ageGroup"))
        if kind == "set_age":
            return self._set_age(message.get("value"), message.get("unit"))
        if kind == "summary":
            return self._summary()
        if kind == "reset":
            self.__init__(self.id)  # type: ignore[misc]
            return self._start()
        return [{"type": "error", "message": f"unknown message type: {kind}"}]

    def _start(self) -> list[dict[str, Any]]:
        self.state = "listening"
        disclosure = (
            "Namaste. Main SwarVani hoon, ek AI assistant. "
            "Main aapki aawaz sunkar visit ka record banaungi. "
            "Aap kis patient se mili, yeh bataiye."
        )
        return [
            {
                "type": "session",
                "sessionId": self.id,
                "demo": settings.demo_mode or not settings.speech_ready,
                "stt": "gnani-prisma" if settings.use_speech_api else "browser",
                "tts": "text-only",
                "llm": settings.llm_model if settings.use_llm else "rules",
                "language": settings.language,
                "source": danger_signs.SOURCE,
            },
            {"type": "state", "state": self.state},
            {"type": "agent", "text": disclosure, "speak": True, "kind": "disclosure"},
        ]

    async def _utterance(self, text: str) -> list[dict[str, Any]]:
        text = text.strip()
        if not text:
            return [{"type": "error", "message": "empty transcript"}]
        if self.saved:
            return [{"type": "error", "message": "visit already saved"}]

        # 1. Deterministic safety pass FIRST, on raw words.
        matches = self.updated_findings(text)
        events: list[dict[str, Any]] = []

        if matches is not None:
            events.append(self._apply_findings(matches))

        # 2. Confirmation intent (only while waiting, and only if it is a bare
        #    "haan") -- otherwise the words are a correction.
        if self.state == "awaiting_confirmation" and self._is_confirmation(text):
            self.utterances.append(text)
            return events + self._confirm()

        # 3. Extraction + merge.
        data, source = await extract_with_llm(text)
        # The opening prompt asks who the worker visited. Treat a short
        # name-only first answer as the beneficiary name, including Devanagari
        # transcripts such as "अम्मार" that have no Latin name marker.
        name_only = text.strip(" \t\r\n,.;:!?।")
        if (
            not self.record.beneficiaryName
            and NAME_ONLY_RE.fullmatch(name_only)
            and name_only.casefold() not in NON_NAME_ANSWERS
        ):
            if not data and not matches:
                data["beneficiaryName"] = latin_name(name_only)
        self.extraction_source = source
        changed = apply_to_record(self.record, data)
        self._capture_age(text, changed)
        self.record.transcript = (self.record.transcript + " " + text).strip()
        self.record.followUps = follow_up_lines(self.record)
        self.utterances.append(text)

        if not changed and not matches and not self.record.beneficiaryName:
            events.append(
                {
                    "type": "agent",
                    "text": "Samajh nahi aaya. Naam, visit type, wajan aur agli visit ki tareekh bataiye.",
                    "speak": True,
                    "kind": "retry",
                }
            )
            return events

        self.state = "awaiting_confirmation"
        events.append({"type": "state", "state": self.state})
        events.append(
            {
                "type": "record",
                "record": self.record.as_dict(),
                "missing": missing_fields(self.record),
                "extraction": source,
                "changed": changed,
            }
        )

        readback = self._readback()
        events.append(
            {
                "type": "agent",
                "text": readback,
                "speak": True,
                "kind": "readback",
                "readback": readback,
            }
        )
        return events

    async def _correction(self, text: str) -> list[dict[str, Any]]:
        if self.saved:
            return [{"type": "error", "message": "visit already saved"}]
        if not text.strip():
            return [{"type": "error", "message": "empty correction"}]
        # Voice corrections are also safety-checked: a newly mentioned sign
        # must still escalate.
        events: list[dict[str, Any]] = []
        matches = self.updated_findings(text)
        if matches is not None:
            events.append(self._apply_findings(matches))

        stripped = self._strip_correction_prefix(text)
        patch = self._parse_field_patch(stripped)
        changed: list[str] = []
        if patch:
            changed = apply_to_record(self.record, patch)
        else:
            # Fall back to a normal extraction pass, still field-by-field.
            data = extract_heuristic(stripped)
            changed = apply_to_record(self.record, data)
        self._capture_age(stripped, changed)

        self.record.corrections.append({"utterance": text, "changed": changed})
        self.record.transcript = (self.record.transcript + " " + text).strip()
        self.record.followUps = follow_up_lines(self.record)
        self.state = "awaiting_confirmation"

        if changed:
            label = ", ".join(changed)
            events.append(
                {
                    "type": "agent",
                    "text": f"Theek hai, {label} update kar diya. Baaki record waisa hi hai.",
                    "speak": True,
                    "kind": "correction",
                }
            )
        else:
            events.append(
                {
                    "type": "agent",
                    "text": "Kaunsa field badalna hai? Jaise: wajan 2.8 kilo.",
                    "speak": True,
                    "kind": "correction-help",
                }
            )

        events.append(
            {
                "type": "record",
                "record": self.record.as_dict(),
                "missing": missing_fields(self.record),
                "extraction": "correction",
                "changed": changed,
            }
        )
        events.append({"type": "state", "state": self.state})
        events.append({"type": "agent", "text": self._readback(), "speak": True, "kind": "readback"})
        return events

    def _confirm(self) -> list[dict[str, Any]]:
        if self.saved:
            return [{"type": "error", "message": "visit already saved"}]
        if self.state != "awaiting_confirmation":
            return [{"type": "error", "message": "Provide a visit note before confirming"}]
        self.saved = True
        self.state = "saved"
        self.record.followUps = follow_up_lines(self.record)
        return [
            {
                "type": "saved",
                "record": self.record.as_dict(),
                "followUps": self.record.followUps,
            },
            {"type": "state", "state": self.state},
            {
                "type": "agent",
                "text": "Record save ho gaya. Dhanyavaad.",
                "speak": True,
                "kind": "saved",
            },
        ]

    def _summary(self) -> list[dict[str, Any]]:
        if self.record.dangerSigns:
            text = (
                f"Aaj ki visit: {self.record.beneficiaryName or 'beneficiary'}. "
                + danger_signs.assessment_message(self.record.dangerSigns)
            )
        else:
            text = (
                f"Aaj ki visit: {self.record.beneficiaryName or 'beneficiary'}, "
                f"{self.record.visitType}. No phrase flagged in the note. Continue the usual visit checks."
            )
        return [{"type": "summary", "text": text, "speak": True, "record": self.record.as_dict()}]

    # ----------------------------------------------------------------- helpers

    def _capture_age(self, text: str, changed: list[str]) -> None:
        age = danger_signs.stated_age(text)
        if age is not None and age != self.record.age:
            self.record.age = age
            changed.append("age")

    def _set_age(self, value: Any, unit: Any) -> list[dict[str, Any]]:
        age = danger_signs.valid_age(value, unit)
        if self.saved or self.state != "awaiting_confirmation":
            return [{"type": "error", "message": "Age can be entered while reviewing an unsaved visit."}]
        if age is None:
            return [{"type": "error", "message": "Enter a valid age. For infants, choose months, weeks, or days."}]

        self.record.age = age
        self.record.corrections.append({"utterance": f"Age set manually: {age.value} {age.unit}", "changed": ["age"]})
        if any(sign["key"] == "fever" for sign in self.record.dangerSigns):
            events = self._set_fever_age(age.group)
            events[-1]["changed"] = ["age"]
            return events
        return [{"type": "record", "record": self.record.as_dict(), "missing": missing_fields(self.record),
                 "extraction": self.extraction_source, "changed": ["age"]}]

    def _set_fever_age(self, group: Any) -> list[dict[str, Any]]:
        if group not in ("under_two_months", "two_months_or_older", "unknown"):
            return [{"type": "error", "message": "Choose one of the listed age groups."}]
        if self.saved or not any(sign["key"] == "fever" for sign in self.record.dangerSigns):
            return [{"type": "error", "message": "Age can be set on a fever alert before saving the visit."}]
        if self.record.age is not None and group != self.record.age.group:
            return [{"type": "error", "message": "Edit the Age field to update the fever age group."}]

        young = group == "under_two_months"
        fever = danger_signs.DANGER_BY_KEY["fever"]
        signs = [
            {
                **sign, "ageGroup": group, "ageConfirmed": True,
                "level": "urgent" if young else "check",
                "labelEn": "Fever reported in a young infant" if young else fever.label_en,
                "labelHi": "Chhote shishu ko bukhar" if young else fever.label_hi,
                "source": "NHM IMNCI Health Worker Chart Booklet (2023), young infant assessment" if young else fever.source,
            }
            if sign["key"] == "fever" else sign
            for sign in self.record.dangerSigns
        ]
        event = self._apply_findings(signs)
        self.record.followUps = follow_up_lines(self.record)
        return [
            event,
            {"type": "record", "record": self.record.as_dict(), "missing": missing_fields(self.record),
             "extraction": self.extraction_source, "changed": []},
        ]

    def updated_findings(self, text: str) -> list[dict[str, Any]] | None:
        before = {sign["key"]: sign for sign in self.record.dangerSigns}
        updated = before.copy()
        matches = danger_signs.scan(text)
        new_age = danger_signs.stated_age(text)
        age = new_age or self.record.age
        younger_age_stated = new_age is not None and new_age.group == "under_two_months"
        young_now = younger_age_stated or danger_signs.has_young_infant_context(text)
        young_infant = (young_now or (age is not None and age.group == "under_two_months")
                        or danger_signs.has_young_infant_context(self.record.transcript))
        # Permit a later age clarification to escalate a previously reported fever.
        if young_now and "fever" in before and not any(match.key == "fever" for match in matches):
            matches.extend(match for match in danger_signs.scan(self.record.transcript) if match.key == "fever")
        for match in danger_signs.apply_context(matches, young_infant):
            existing = before.get(match.key)
            if existing and existing.get("ageConfirmed") and not younger_age_stated:
                continue
            if existing is None or (match.level == "urgent" and danger_signs.signal_level(existing) == "check"):
                updated[match.key] = match.as_dict()
        fever = updated.get("fever")
        if (
            fever is not None
            and not fever.get("ageConfirmed")
            and fever.get("ageGroup") == "unknown"
            and not young_infant
            and age is not None
            and age.group == "two_months_or_older"
        ):
            updated["fever"] = {**fever, "ageGroup": "two_months_or_older"}
        return list(updated.values()) if updated != before else None

    def _apply_findings(self, signs: list[dict[str, Any]]) -> dict[str, Any]:
        self.record.dangerSigns = signs
        return {
            "type": "danger", "signs": signs, "message": danger_signs.assessment_message(signs),
            "speak": True, "source": danger_signs.SOURCE,
        }

    def _is_confirmation(self, text: str) -> bool:
        norm = danger_signs.normalize(text)
        words = norm.strip()
        if len(words.split()) > 4:
            return False
        return words in CONFIRM_WORDS

    def _strip_correction_prefix(self, text: str) -> str:
        out = text.strip()
        for marker in CORRECTION_MARKERS:
            out = re.sub(rf"^\s*{re.escape(marker)}\b[,:\s]*", "", out, flags=re.I)
        return out.strip()

    def _parse_field_patch(self, text: str) -> dict[str, Any]:
        return extract_heuristic(text)

    def _readback(self) -> str:
        r = self.record
        parts: list[str] = []
        visit_type = "general" if r.visitType == "OTHER" else r.visitType.lower()
        parts.append(f"{r.beneficiaryName or 'Naam abhi darj nahi hua'}, {visit_type} visit.")
        if r.beneficiaryId:
            parts.append(f"Beneficiary ID {r.beneficiaryId}.")
        if r.age is not None:
            unit = {"years": "saal", "months": "mahine", "weeks": "hafte", "days": "din"}[r.age.unit]
            parts.append(f"Umar {r.age.value} {unit}.")
        if r.weightKg is not None:
            parts.append(f"Wajan {_fmt_weight(r.weightKg)}.")
        if r.bp:
            parts.append(f"BP {r.bp}.")
        if r.hb is not None:
            parts.append(f"Hb {r.hb:g}.")
        if r.vaccination:
            parts.append(f"{r.vaccination} vaccination di gayi.")
        if r.supplements:
            parts.append(f"{' aur '.join(r.supplements)} di gayi.")
        if r.nextVisit:
            parts.append(f"Agli visit {_fmt_date(r.nextVisit)} ko.")
            if not r.beneficiaryName:
                parts.append("Jinse mili unka naam bataiye.")
        elif not r.beneficiaryName:
            parts.append("Naam aur agli visit ki tareekh bata sakti hain.")
        else:
            parts.append("Agli visit ki tareekh pata ho to bataiye.")
        if r.dangerSigns:
            parts.append("Please review the check-up or referral flag above.")
        parts.append("Sahi hai? Haan ya galat boliye, ya sirf ek field sudhariye.")
        return " ".join(parts)
