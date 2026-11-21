"""Deterministic danger-sign matcher.

This module is the safety layer. It is deliberately *not* an LLM: every
transcript (and every voice correction) is scanned here, before extraction,
so the referral alert can never depend on the model noticing a sign.

The prototype phrase list is grounded in the public NHM/MoHFW materials cited
in README.md. These keyword rules are a safety prompt, not a clinical protocol.

Findings prompt either a check-up or an urgent referral. Phrase confidence
and urgency are separate.
"""

from __future__ import annotations

import re
import json
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent / "data" / "danger_signs.json"

# Words that turn a symptom into its absence. Checked within a short window
# *before* the matched phrase.
NEGATIONS = (
    "nahi",
    "nahin",
    "nai",
    "no",
    "not",
    "without",
    "denies",
    "नहीं",
    "नही",
)


@dataclass(frozen=True)
class DangerSign:
    key: str
    label_en: str
    label_hi: str
    action_hi: str
    phrases: tuple[str, ...]
    fuzzy: tuple[str, ...] = ()
    who: str = "newborn"
    source: str = ""
    level: str = "urgent"


_catalog = json.loads(DATA_PATH.read_text(encoding="utf-8"))
SOURCE = str(_catalog["source"])
DANGER_SIGNS: tuple[DangerSign, ...] = tuple(
    DangerSign(
        key=item["key"],
        label_en=item["label_en"],
        label_hi=item["label_hi"],
        action_hi=item.get("action_hi", ""),
        phrases=tuple(item.get("phrases", [])),
        fuzzy=tuple(item.get("fuzzy", [])),
        who=item.get("who", "newborn"),
        source=item.get("source", SOURCE),
        level=item.get("level", "urgent"),
    )
    for item in _catalog["signs"]
)

DANGER_BY_KEY = {sign.key: sign for sign in DANGER_SIGNS}


@dataclass
class Match:
    key: str
    label_en: str
    label_hi: str
    action_hi: str
    matched_phrase: str
    confident: bool
    source: str = SOURCE
    level: str = "urgent"

    def as_dict(self) -> dict:
        sign = {
            "key": self.key,
            "labelEn": self.label_en,
            "labelHi": self.label_hi,
            "actionHi": self.action_hi,
            "matched": self.matched_phrase,
            "confident": self.confident,
            "source": self.source,
            "level": self.level,
        }
        if self.key == "fever":
            sign["ageGroup"] = "under_two_months" if self.level == "urgent" else "unknown"
        return sign


def normalize(text: str) -> str:
    """Lowercase, strip punctuation/diacritics, collapse whitespace."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^\w\s\u0900-\u097F]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return f" {text.strip()} "


def _negated(normalized: str, phrase: str) -> bool:
    """True when the phrase is denied, before or after it.

    Hindi negates after the noun ("bukhar nahi hai") while English often
    negates before ("no fever"), so both sides are checked -- but only within
    a very short window, so an unrelated "nahi" in the next clause does not
    erase a real sign.
    """
    # Evaluate each occurrence independently; negation in a separate clause
    # must not suppress a later positive mention. Internal negation belongs
    # to the sign itself (e.g. "not feeding" / "doodh nahi").
    occurrences = list(re.finditer(r"(?<!\S)" + re.escape(phrase) + r"(?!\S)", normalized))
    if not occurrences:
        return False
    for match in occurrences:
        before = normalized[:match.start()].split()[-3:]
        after = normalized[match.end():].split()[:3]
        if not any(normalize(token).strip() in {normalize(n).strip() for n in NEGATIONS} for token in before + after):
            return False
    return True


def scan(text: str) -> list[Match]:
    """Return every danger sign mentioned in `text` (deduplicated by key)."""
    if not text or not text.strip():
        return []
    # Preserve clause boundaries before normalization removes punctuation.
    clauses = re.split(r"[,;.!।?]|\b(?:but|and|aur|lekin)\b|(?:लेकिन|और|परंतु)", text, flags=re.I)
    norms = [normalize(clause) for clause in clauses if clause.strip()]
    found: dict[str, Match] = {}

    for sign in DANGER_SIGNS:
        if sign.key in found:
            continue
        for phrase in sign.phrases:
            normalized_phrase = normalize(phrase).strip()
            needle = f" {normalized_phrase} "
            if any(needle in norm and not _negated(norm, normalized_phrase) for norm in norms):
                found[sign.key] = Match(
                    key=sign.key,
                    label_en=sign.label_en,
                    label_hi=sign.label_hi,
                    action_hi=sign.action_hi,
                    matched_phrase=phrase,
                    confident=True,
                    source=sign.source,
                    level=sign.level,
                )
                break
        if sign.key in found:
            continue
        # Weak matches are still flags: an uncertain sign is never dropped.
        for phrase in sign.fuzzy:
            normalized_phrase = normalize(phrase).strip()
            needle = f" {normalized_phrase} "
            if any(needle in norm and not _negated(norm, normalized_phrase) for norm in norms):
                found[sign.key] = Match(
                    key=sign.key,
                    label_en=sign.label_en,
                    label_hi=sign.label_hi,
                    action_hi=sign.action_hi,
                    matched_phrase=phrase,
                    confident=False,
                    source=sign.source,
                    level=sign.level,
                )
                break

    if "maternal_bleeding" in found:
        found.pop("bleeding_reported", None)
    return list(found.values())


YOUNG_INFANT = re.compile(
    r"\b(?:newborn|navjat)\b|नवजात|"
    r"\b(?:baby|infant)\s+(?:is\s+)?(?P<age>\d{1,2})\s*(?P<unit>days?|weeks?)\s+old\b|"
    r"(?P<hindi_age>\d{1,2})\s*(?P<hindi_unit>दिन|हफ्ते|सप्ताह)\s+(?:का|की)\s+(?:बच्चा|बच्ची|शिशु)"
)


def has_young_infant_context(text: str) -> bool:
    """Use an explicit newborn mention or stated age, rather than guessing."""
    from .normalization import normalize_numbers

    for match in YOUNG_INFANT.finditer(normalize_numbers(text.casefold())):
        age = match["age"] or match["hindi_age"]
        if age is None:
            return True
        unit = match["unit"] or match["hindi_unit"]
        days = int(age) * (7 if unit.startswith("week") or unit in {"हफ्ते", "सप्ताह"} else 1)
        if days < 60:
            return True
    return False


def apply_context(matches: list[Match], young_infant: bool) -> list[Match]:
    if not young_infant:
        return matches
    return [
        replace(
            match, level="urgent", label_en="Fever reported in a young infant", label_hi="Chhote shishu ko bukhar",
            source="NHM IMNCI Health Worker Chart Booklet (2023), young infant assessment",
        )
        if match.key == "fever" else match
        for match in matches
    ]


def signal_level(sign: dict) -> str:
    """Give older saved findings a tier when they predate this field."""
    catalog_entry = DANGER_BY_KEY.get(sign.get("key"))
    return sign.get("level") or (catalog_entry.level if catalog_entry else "urgent")


def assessment_message(signs: list[dict]) -> str:
    """Use the highest tier for the main action while retaining all findings."""
    if any(signal_level(sign) == "urgent" for sign in signs):
        return "Urgent referral flag. Contact your ANM/PHC now and follow the local emergency referral protocol."
    fever = next((sign for sign in signs if sign["key"] == "fever"), None)
    if fever:
        check = "Measure temperature, ask about other symptoms, and contact your ANM/PHC promptly for assessment."
        if fever.get("ageGroup") in {None, "unknown"}:
            return f"Age is unknown. Check the patient's age now. {check}"
        return check
    return "Check now: confirm what you observed and contact your ANM/PHC promptly to assess whether referral is needed."
