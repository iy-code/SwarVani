"""Turn a code-mixed visit note into a structured VisitRecord.

Two paths:
  * `extract_with_llm`  - Qwen via an OpenAI-compatible endpoint, JSON-only
    output, every value coerced back into the fixed schema.
  * `extract_heuristic` - deterministic Hinglish/English pattern extractor.
    Also the fallback whenever the LLM is unavailable or malformed.

Either way the model only fills known fields. Nothing it returns can reach the
TTS layer as free text.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from .config import settings
from .normalization import normalize_numbers, latin_name
from .schemas import EDITABLE_FIELDS, VisitRecord, parse_date_hint

WEIGHT_RE = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d+)?)\s*(?:kilograms?|kilos?|kgs?|किलोग्राम|किलो)(?!\w)", re.I)
BP_RE = re.compile(r"(?:bp|blood pressure|बीपी|ब्लड प्रेशर)\D{0,6}(\d{2,3})\s*(?:/|by|upon|over|बाय)\s*(\d{2,3})", re.I)
HB_RE = re.compile(r"(?:hb|haemoglobin|hemoglobin|हीमोग्लोबिन|एचबी)\D{0,6}(\d{1,2}(?:\.\d+)?)", re.I)
_NUMBER_WORDS = {
    "zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}
_NUMBER_WORD_PATTERN = "(?:" + "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True)) + ")"
SPOKEN_WEIGHT_RE = re.compile(
    rf"(?:weight|wajan|वजन)\s+(({_NUMBER_WORD_PATTERN})(?:[ -]+(?:{_NUMBER_WORD_PATTERN})){{0,2}})\s*(?:kilo|kilos|kg|kgs|किलो|किलोग्राम)",
    re.I,
)
SPOKEN_BP_RE = re.compile(
    rf"(?:bp|blood pressure|बीपी|ब्लड प्रेशर)\s+(({_NUMBER_WORD_PATTERN})(?:[ -]+(?:{_NUMBER_WORD_PATTERN})){{0,2}})\s+(?:by|upon)\s+(({_NUMBER_WORD_PATTERN})(?:[ -]+(?:{_NUMBER_WORD_PATTERN})){{0,2}})",
    re.I,
)
_NAME_TOKEN = r"[A-Za-z\u0900-\u0963\u0972-\u097F]+(?:[-'][A-Za-z\u0900-\u0963\u0972-\u097F]+)*"
_NAME_WORDS = rf"({_NAME_TOKEN}(?:\s+{_NAME_TOKEN}){{0,3}})"
_NAME_LABEL = re.compile(
    rf"(?<![\w\u0900-\u0963])(?:beneficiary(?:\s+name)?|patient\s+name|लाभार्थी(?:\s+का\s+नाम)?|naam|name|नाम)(?=\s|:)"
    rf"\s*(?:(?:is|hai|है)(?=\s|:)\s*)?:?\s*{_NAME_WORDS}",
    re.I,
)
_VISIT_NAME = re.compile(
    rf"{_NAME_WORDS}\s+(?:(?:ke|के)\s+(?:ghar|घर)|"
    r"(?:ka|ki|ke|का|की|के)\s+(?:anc|pnc|check\s?up|चेक\s?अप|जाँच|जांच|weight|wajan|व[ज़ज]+न|bp|बीपी|"
    r"visit|विजिट|मुलाक़ात|मुलाकात|vaccination|tika|टीका|नवजात|newborn|गर्भावस्था)|"
    r"(?:se|से)\s+(?:mili|mila|मिली|मिला))(?=\s|$|[,.।])",
    re.I,
)
_NAME_DELIMITERS = set("""
    i am went visited saw met at today yesterday tomorrow the her his patient beneficiary
    aaj kal main mein mai ne uske uska uski ghar visit checkup kiya ka ki ke ko se ji
    is hai hain and aur name naam id number weight wajan bp hb anc pnc age check
    vaccination vaccine supplements next fever follow-up unknown unclear unavailable not no missing
    मैं में आज कल ने उसके उसका उसकी उसको घर विजिट मुलाकात मुलाक़ात चेकअप का की के को से जी
    नाम लाभार्थी आईडी आई संख्या नंबर वजन वज़न बीपी एचबी है हैं और अगली अगले दवा बुखार फीवर नहीं पता
""".split())
ID_RE = re.compile(r"(?:beneficiary\s*(?:id|number)|patient\s*(?:id|number)|लाभार्थी\s*(?:आईडी|आई डी|संख्या)|आईडी|आई डी|\bid\b)\s*(?:is|hai|है|हैं|number|नंबर|:)?\s*:?\s*([A-Za-z]*-?\d+(?:-\d+)*)", re.I)


VISIT_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ANC", ("anc", "antenatal", "pregnancy", "pregnan", "garbhvati", "गर्भवती")),
    ("PNC", ("pnc", "postnatal", "post natal", "delivery ke baad", "प्रसव")),
    ("IMMUNISATION", ("immunisation", "immunization", "tika", "teeka", "vaccine", "vaccination", "टीका")),
    ("NEWBORN", ("newborn", "naya bachcha", "navjat", "नवजात")),
    ("GROWTH", ("growth", "weight monitoring", "विकास")),
)

VACCINES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("TT", ("tt injection", "tt", "tetanus", "टीटी")),
    ("BCG", ("bcg", "बीसीजी")),
    ("OPV", ("opv", "polio", "पोलियो")),
    ("PENTA", ("pentavalent", "penta", "पेंटावेलेंट")),
    ("MR", ("mr vaccine", "measles", "rubella", "खसरा")),
    ("HEP_B", ("hepatitis b", "hep b")),
)

SUPPLEMENTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("IFA", ("ifa", "iron folic", "iron tablet", "ifa goli", "आईएफए")),
    ("Calcium", ("calcium", "कैल्शियम")),
    ("Folic acid", ("folic acid", "folic")),
    ("Vitamin A", ("vitamin a", "विटामिन ए")),
)

NEXT_VISIT_RE = re.compile(
    r"(?:next\s+(?:visit|check\s?up)|follow[- ]up(?:\s+date)?|agli\s+(?:visit|baar|mulakat)|"
    r"अगली\s+(?:विजिट|बार|मुलाक[़]?ात|जाँच|जांच))\s*(?:(?:date|is|on|hai|है|की\s+तारीख[़]?)\s+)*(?:[:=]\s*)?"
    r"(?P<stated>[^.,;।!?]{1,48})"
    r"|(?<![\w\u0900-\u0963])(?P<scheduled>(?:(?:agle|agli|next|अगले)\s+(?:mahine|month|hafte|week|महीने|हफ्ते|सप्ताह))[^.,;।!?]{0,24})",
    re.I,
)
_RELATIVE_DATE = r"day\s+after\s+tomorrow|tomorrow|today|parso[n]?|kal|aaj|परसों|परसो|कल|आज"
RETURN_VISIT_RE = re.compile(
    rf"(?<![\w\u0900-\u0963])(?P<relative>{_RELATIVE_DATE})(?:\s+ही)?\s+"
    r"(?:(?:wapas|vaapas|वापस|phir|फिर|dobara|दोबारा)\s+)?"
    r"(?:aaung[ai]|aaoong[ai]|aaungi|aayenge|aayegi|milung[ai]|मिलूँगी|मिलूंगी|"
    r"आऊँगी|आऊंगी|आऊँगा|आऊंगा|आउंगी|आउंगा|आएँगे|आएंगे|आएगी|आना\s+है|देखने\s+आऊंगी|"
    r"(?:i|we)(?:['’]ll|\s+will)\s+(?:return|come\s+back|visit)|(?:returning|coming\s+back))"
    r"(?![\w\u0900-\u0963]|\s+(?:nahi|nahin|नहीं|नही)(?=\s|$|[,.।]))",
    re.I,
)
ENGLISH_RETURN_RE = re.compile(
    rf"\b(?:i|we)(?:['’]ll|\s+will)\s+(?:return|come\s+back|visit)(?:\s+again)?\s+(?P<relative>{_RELATIVE_DATE})(?![\w\u0900-\u0963])",
    re.I,
)


def _extract_name(text: str) -> str | None:
    for match in _NAME_LABEL.finditer(text):
        words = []
        for word in match.group(1).split():
            if word.casefold() in _NAME_DELIMITERS:
                break
            words.append(word)
        if words:
            return latin_name(" ".join(words))
    for match in _VISIT_NAME.finditer(text):
        words = match.group(1).split()
        # Keep the name immediately before the visit phrase. Earlier words
        # such as “मैं आज” describe the speaker or timing.
        name = []
        for word in reversed(words):
            if word.casefold() in _NAME_DELIMITERS:
                break
            name.insert(0, word)
        if name:
            return latin_name(" ".join(name))
    return None


def _spoken_number(phrase: str) -> int | None:
    """Parse common English number words emitted by code-mixed STT."""
    words = re.findall(r"[a-z]+", phrase.lower())
    values = [_NUMBER_WORDS.get(word) for word in words]
    if not values or any(value is None for value in values):
        return None
    nums = [int(value) for value in values if value is not None]
    if len(nums) == 1:
        return nums[0]
    if len(nums) == 2:
        first, second = nums
        if first in {20, 30, 40, 50, 60, 70, 80, 90} and 0 <= second <= 9:
            return first + second
        # Common spoken blood pressure shorthand: "one twenty by eighty".
        if 1 <= first <= 9 and second in {20, 30, 40, 50, 60, 70, 80, 90}:
            return first * 100 + second
    if len(nums) == 3 and nums[1] == 10:
        return nums[0] * 100 + nums[2]
    return None


def extract_heuristic(text: str) -> dict[str, Any]:
    name = _extract_name(text)
    text = normalize_numbers(text)
    data: dict[str, Any] = {}

    identifier = ID_RE.search(text)
    if identifier:
        data["beneficiaryId"] = identifier.group(1)

    if name:
        data["beneficiaryName"] = name

    for visit_type, keywords in VISIT_KEYWORDS:
        if any(re.search(rf"(?<![\w\u0900-\u0963]){re.escape(k)}(?![\w\u0900-\u0963])", text, re.I) for k in keywords):
            data["visitType"] = visit_type
            break

    m = WEIGHT_RE.search(text)
    if m:
        data["weightKg"] = float(m.group(1))
    else:
        spoken_weight = SPOKEN_WEIGHT_RE.search(text)
        if spoken_weight:
            weight = _spoken_number(spoken_weight.group(1))
            if weight is not None and 0.4 <= weight <= 200:
                data["weightKg"] = float(weight)

    m = BP_RE.search(text)
    if m:
        data["bp"] = f"{m.group(1)}/{m.group(2)}"
    else:
        spoken_bp = SPOKEN_BP_RE.search(text)
        if spoken_bp:
            systolic = _spoken_number(spoken_bp.group(1))
            diastolic = _spoken_number(spoken_bp.group(3))
            if systolic is not None and diastolic is not None:
                data["bp"] = f"{systolic}/{diastolic}"

    m = HB_RE.search(text)
    if m:
        data["hb"] = float(m.group(1))

    for label, keywords in VACCINES:
        if any(k in text.lower() for k in keywords):
            data["vaccination"] = label
            break

    supplements = [label for label, keywords in SUPPLEMENTS if any(k in text.lower() for k in keywords)]
    if supplements:
        data["supplements"] = supplements

    for pattern in (NEXT_VISIT_RE, RETURN_VISIT_RE, ENGLISH_RETURN_RE):
        for match in pattern.finditer(text):
            hint = next(value for value in match.groups() if value is not None)
            iso = parse_date_hint(hint)
            if iso:
                data["nextVisit"] = iso
                break
        if "nextVisit" in data:
            break
    return sanitize(data)


SYSTEM_PROMPT = """You are a clinical form extractor for an ASHA worker's visit note.
The note is Hinglish/Hindi/English code-mixed speech. Return ONLY a JSON object.

Allowed keys and types:
  beneficiaryName: string | null      (full name in Latin script; transliterate Hindi, e.g. अम्मार -> Ammar; retain surnames)
  beneficiaryId: string | null       (spoken beneficiary ID; preserve leading zeros)
  visitType: one of "ANC","PNC","IMMUNISATION","NEWBORN","GROWTH","OTHER"
  weightKg: number | null             (kilograms, e.g. 52 or 2.8)
  bp: string | null                   (form "120/80")
  hb: number | null                   (g/dL)
  vaccination: string | null          (TT, BCG, OPV, PENTA, MR, HEP_B)
  supplements: string[]               (IFA, Calcium, Folic acid, Vitamin A)
  nextVisit: string | null            (ISO date, YYYY-MM-DD)
  notes: null

Rules: convert spoken numbers to numeric values (fifty kilo -> weightKg 50). Never invent values. Omit a key if not stated. No diagnosis, no advice,
no explanation, no markdown fences. JSON only."""


async def extract_with_llm(text: str) -> tuple[dict[str, Any], str]:
    """Call Qwen; fall back to heuristics on any failure. Returns (data, source)."""
    if not settings.use_llm:
        return extract_heuristic(text), "heuristic"
    payload = {
        "model": settings.llm_model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            headers = {"Authorization": f"Bearer {settings.llm_api_key}"} if settings.llm_api_key else {}
            resp = await client.post(
                f"{settings.llm_base_url.rstrip('/')}/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
        raw = content.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
        data = json.loads(raw)
        clean = sanitize(data)
        clean.update(extract_heuristic(text))
        return clean, "llm"
    except Exception:
        return extract_heuristic(text), "heuristic"


def sanitize(data: dict[str, Any]) -> dict[str, Any]:
    """Coerce whatever the model returned back into our fixed schema."""
    clean: dict[str, Any] = {}
    if not isinstance(data, dict):
        return clean
    for key in EDITABLE_FIELDS:
        if key not in data:
            continue
        value = data[key]
        if value in (None, "", [], "null"):
            continue
        if key == "beneficiaryName":
            clean[key] = latin_name(str(value))[:80]
        elif key == "weightKg":
            try:
                weight = float(value)
            except (TypeError, ValueError):
                continue
            if 0.4 <= weight <= 200:
                clean[key] = weight
        elif key == "hb":
            try:
                hb = float(value)
            except (TypeError, ValueError):
                continue
            if 2 <= hb <= 25:
                clean[key] = hb
        elif key == "supplements":
            if isinstance(value, list):
                clean[key] = [str(v).strip()[:32] for v in value if str(v).strip()][:6]
        elif key == "visitType":
            upper = str(value).upper()
            if upper in {"ANC", "PNC", "IMMUNISATION", "NEWBORN", "GROWTH", "OTHER"}:
                clean[key] = upper
        elif key == "bp":
            if re.fullmatch(r"\d{2,3}/\d{2,3}", str(value)):
                clean[key] = str(value)
        elif key == "nextVisit":
            iso = parse_date_hint(str(value))
            if iso:
                clean[key] = iso
        elif key == "notes":
            continue  # free-form medical text is never stored from the model
        else:
            clean[key] = str(value)[:80]
    return clean


def apply_to_record(record: VisitRecord, data: dict[str, Any]) -> list[str]:
    """Merge extracted data into the record, returning changed field names."""
    changed: list[str] = []
    for key, value in data.items():
        if not hasattr(record, key):
            continue
        if getattr(record, key) != value:
            setattr(record, key, value)
            changed.append(key)
    return changed
