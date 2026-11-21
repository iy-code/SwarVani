"""SQLite storage with optional encryption at rest.

Records (including the transcript) are serialised to JSON and encrypted with
Fernet when SV_DB_KEY is set. Without a key the app still runs, but the mode is
reported so the UI can warn; the hackathon build must ship with a key.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import settings
from .danger_signs import signal_level

WHATSAPP_STATUSES = {
    "confirmed",
    "reschedule_requested",
    "no_response",
    "asha_callback_requested",
}

try:  # cryptography is optional at runtime
    from cryptography.fernet import Fernet, InvalidToken

    _FERNET_OK = True
except Exception:  # pragma: no cover
    Fernet = None  # type: ignore[assignment]
    InvalidToken = Exception  # type: ignore[assignment,misc]
    _FERNET_OK = False


_fernet = None


def _cipher():
    global _fernet
    if _fernet is None and settings.db_key and _FERNET_OK:
        _fernet = Fernet(settings.db_key.encode())
    return _fernet


def encryption_enabled() -> bool:
    return _cipher() is not None


@contextmanager
def _connect():
    path: Path = settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS records (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                payload TEXT NOT NULL,
                encrypted INTEGER NOT NULL DEFAULT 0,
                danger INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_records_created ON records(created_at DESC)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS whatsapp_followups (
                record_id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'no_response',
                last_reply TEXT,
                last_message_id TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS whatsapp_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                record_id TEXT NOT NULL,
                status TEXT NOT NULL,
                reply TEXT,
                message_id TEXT UNIQUE,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_whatsapp_events_created ON whatsapp_events(created_at DESC)")
        conn.commit()


def save_record(record: dict[str, Any]) -> None:
    payload = json.dumps(record, ensure_ascii=False)
    cipher = _cipher()
    encrypted = 0
    if cipher is not None:
        payload = cipher.encrypt(payload.encode()).decode()
        encrypted = 1
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO records (id, created_at, payload, encrypted, danger) VALUES (?,?,?,?,?)",
            (
                record["id"],
                record.get("createdAt") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
                payload,
                encrypted,
                1 if record.get("dangerSigns") else 0,
            ),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO whatsapp_followups (record_id, status, updated_at)
            VALUES (?, 'no_response', ?)
            """,
            (record["id"], datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        conn.commit()


def _decode(payload: str, encrypted: int) -> dict[str, Any]:
    if encrypted:
        cipher = _cipher()
        if cipher is None:
            return {"id": "locked", "error": "record encrypted, SV_DB_KEY not set"}
        try:
            payload = cipher.decrypt(payload.encode()).decode()
        except InvalidToken:
            return {"id": "locked", "error": "cannot decrypt record"}
    return json.loads(payload)


def list_records(limit: int = 50) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT payload, encrypted FROM records ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    records = []
    for row in rows:
        item = _decode(row["payload"], row["encrypted"])
        item.pop("transcript", None)  # transcripts are not served over HTTP
        records.append(item)
    return records


def follow_ups() -> list[dict[str, Any]]:
    with _connect() as conn:
        status_rows = conn.execute(
            "SELECT record_id, status, last_reply, updated_at FROM whatsapp_followups"
        ).fetchall()
    whatsapp = {row["record_id"]: dict(row) for row in status_rows}
    out: list[dict[str, Any]] = []
    for record in list_records(limit=100):
        status = whatsapp.get(record.get("id"), {})
        signs = record.get("dangerSigns") or []
        urgent = any(signal_level(sign) == "urgent" for sign in signs)
        for line in record.get("followUps", []):
            if signs and not urgent and line == "DANGER FLAG OPEN - confirm PHC referral was completed":
                line = "Check-up needed: contact the ANM/PHC promptly to assess the reported symptoms"
            out.append(
                {
                    "recordId": record.get("id"),
                    "beneficiaryName": record.get("beneficiaryName"),
                    "visitType": record.get("visitType"),
                    "nextVisit": record.get("nextVisit"),
                    "line": line,
                    "danger": urgent,
                    "checkUp": bool(signs) and not urgent,
                    "whatsappStatus": status.get("status", "no_response"),
                    "whatsappLastReply": status.get("last_reply"),
                    "whatsappUpdatedAt": status.get("updated_at"),
                }
            )
    out.sort(key=lambda item: (item["nextVisit"] or "9999", item["beneficiaryName"] or ""))
    return out


def update_whatsapp_status(
    record_id: str,
    status: str,
    *,
    last_reply: str | None = None,
    message_id: str | None = None,
) -> bool:
    """Apply a verified WhatsApp reply to an existing visit record."""
    if status not in WHATSAPP_STATUSES:
        raise ValueError(f"unsupported WhatsApp status: {status}")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _connect() as conn:
        exists = conn.execute("SELECT 1 FROM records WHERE id = ?", (record_id,)).fetchone()
        if not exists:
            return False
        conn.execute(
            """
            INSERT INTO whatsapp_followups
                (record_id, status, last_reply, last_message_id, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(record_id) DO UPDATE SET
                status = excluded.status,
                last_reply = excluded.last_reply,
                last_message_id = excluded.last_message_id,
                updated_at = excluded.updated_at
            """,
            (record_id, status, last_reply, message_id, now),
        )
        if message_id:
            conn.execute(
                """
                INSERT OR IGNORE INTO whatsapp_events
                    (record_id, status, reply, message_id, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (record_id, status, last_reply, message_id, now),
            )
        else:
            conn.execute(
                """
                INSERT INTO whatsapp_events (record_id, status, reply, message_id, created_at)
                VALUES (?, ?, ?, NULL, ?)
                """,
                (record_id, status, last_reply, now),
            )
        conn.commit()
    return True


def whatsapp_updates(limit: int = 25) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT record_id, status, reply, created_at
            FROM whatsapp_events
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (max(1, min(limit, 100)),),
        ).fetchall()
    names = {record.get("id"): record.get("beneficiaryName") for record in list_records(limit=200)}
    return [
        {
            "recordId": row["record_id"],
            "beneficiaryName": names.get(row["record_id"]),
            "status": row["status"],
            "reply": row["reply"],
            "createdAt": row["created_at"],
        }
        for row in rows
    ]


def whatsapp_status_summary() -> dict[str, int]:
    summary = {status: 0 for status in WHATSAPP_STATUSES}
    with _connect() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS count FROM whatsapp_followups GROUP BY status"
        ).fetchall()
    for row in rows:
        if row["status"] in summary:
            summary[row["status"]] = row["count"]
    return summary


def purge_transcripts() -> int:
    """Delete transcript text from every stored record (DPDP retention step)."""
    removed = 0
    with _connect() as conn:
        rows = conn.execute("SELECT id, payload, encrypted FROM records").fetchall()
        for row in rows:
            record = _decode(row["payload"], row["encrypted"])
            if not record.get("transcript"):
                continue
            record["transcript"] = ""
            record["transcriptPurged"] = True
            payload = json.dumps(record, ensure_ascii=False)
            encrypted = 0
            cipher = _cipher()
            if cipher is not None:
                payload = cipher.encrypt(payload.encode()).decode()
                encrypted = 1
            conn.execute(
                "UPDATE records SET payload = ?, encrypted = ? WHERE id = ?",
                (payload, encrypted, row["id"]),
            )
            removed += 1
        conn.commit()
    return removed


def stats() -> dict[str, Any]:
    with _connect() as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM records").fetchone()["c"]
        flagged = conn.execute("SELECT COUNT(*) AS c FROM records WHERE danger = 1").fetchone()["c"]
    return {"visits": total, "flagged": flagged}
