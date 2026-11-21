"""FastAPI entrypoint: HTTP API + the voice WebSocket.

Run from backend/:
    python -m uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import logging
import asyncio
import hmac
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from . import db
from . import danger_signs
from .config import settings
from .gnani import open_stt, parse_stt_message
from .pipeline import VisitSession
from .whatsapp import apply_webhook, verify_signature, send_test_followup

log = logging.getLogger("swarvani")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not settings.demo_mode and not settings.speech_ready:
        raise RuntimeError("Live mode requires GNANI_API_KEY and the configured Gnani Prisma WebSocket endpoint")
    if not settings.demo_mode and not db.encryption_enabled():
        raise RuntimeError("Live mode requires a valid SV_DB_KEY so visit records are encrypted at rest")
    db.init_db()
    log.info(
        "SwarVani ready | demo=%s speech=%s llm=%s encryption=%s",
        settings.demo_mode,
        "gnani" if settings.use_speech_api else "browser",
        settings.llm_model if settings.use_llm else "rules",
        db.encryption_enabled(),
    )
    yield


app = FastAPI(title="SwarVani API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "demoMode": settings.demo_mode,
        "speech": "gnani" if settings.use_speech_api else "browser",
        "llm": settings.llm_model if settings.use_llm else "rules",
        "speechModels": {"stt": "Prisma v3.0", "tts": None} if settings.use_speech_api else None,
        "assistantOutput": "text-only",
        "whatsapp": {
            "dashboardSync": True,
            "webhookConfigured": settings.whatsapp_webhook_ready,
            "sendingConfigured": settings.whatsapp_sending_ready,
        },
        "encryption": db.encryption_enabled(),
        "stats": db.stats(),
    }


@app.get("/api/records")
async def records(limit: int = 50) -> dict:
    return {"records": db.list_records(limit=max(1, min(limit, 200)))}


@app.get("/api/followups")
async def followups() -> dict:
    return {"followUps": db.follow_ups()}


@app.get("/api/whatsapp/webhook", response_class=PlainTextResponse)
async def verify_whatsapp_webhook(
    mode: str = Query("", alias="hub.mode"),
    token: str = Query("", alias="hub.verify_token"),
    challenge: str = Query("", alias="hub.challenge"),
) -> str:
    """Complete Meta's webhook verification handshake."""
    if not settings.whatsapp_verify_token:
        raise HTTPException(status_code=503, detail="WhatsApp webhook is not configured")
    if mode != "subscribe" or not hmac.compare_digest(token, settings.whatsapp_verify_token):
        raise HTTPException(status_code=403, detail="Webhook verification failed")
    return challenge


@app.post("/api/whatsapp/webhook")
async def receive_whatsapp_webhook(request: Request) -> dict:
    """Verify Meta's signature, then synchronize quick replies to the dashboard."""
    if not settings.whatsapp_app_secret:
        raise HTTPException(status_code=503, detail="WhatsApp webhook is not configured")
    body = await request.body()
    if not verify_signature(body, request.headers.get("X-Hub-Signature-256"), settings.whatsapp_app_secret):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Webhook payload must be an object")
    return {"received": True, "updates": apply_webhook(payload)}


@app.get("/api/whatsapp/updates")
async def whatsapp_updates(limit: int = 25) -> dict:
    """Latest verified beneficiary replies plus current dashboard totals."""
    return {
        "updates": db.whatsapp_updates(limit),
        "summary": db.whatsapp_status_summary(),
        "simulatorEnabled": settings.demo_mode,
        "webhookConfigured": settings.whatsapp_webhook_ready,
        "sendingConfigured": settings.whatsapp_sending_ready,
    }


@app.post("/api/whatsapp/simulate/{record_id}")
async def simulate_whatsapp_reply(record_id: str, status: str, request: Request) -> dict:
    if not settings.demo_mode:
        raise HTTPException(404, "The reply simulator is available in demo mode.")
    if request.headers.get("X-SwarVani-Test") != "1":
        raise HTTPException(403, "Use the follow-up board to simulate replies.")
    labels = {
        "confirmed": "Confirm",
        "reschedule_requested": "Reschedule",
        "asha_callback_requested": "Request ASHA call",
    }
    if status not in labels:
        raise HTTPException(400, "Choose one of the three demo replies.")
    if not any(item["recordId"] == record_id for item in db.follow_ups()) or not db.update_whatsapp_status(
        record_id, status, last_reply=f"Simulated reply: {labels[status]}",
    ):
        raise HTTPException(404, "Save a visit with a follow-up first.")
    return {"status": status}


@app.post("/api/whatsapp/test-followup/{record_id}")
async def test_followup(record_id: str, request: Request) -> dict:
    if request.client is None or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(403, "Test sending is available locally only.")
    if request.headers.get("X-SwarVani-Test") != "1":
        raise HTTPException(403, "Use the local follow-up board to send test messages.")
    if not any(item["recordId"] == record_id for item in db.follow_ups()):
        raise HTTPException(404, "Save a visit with a follow-up first.")
    return {"messageId": await send_test_followup(record_id), "status": "accepted"}


@app.get("/api/danger-signs")
async def danger_sign_catalog() -> dict:
    return {
        "source": danger_signs.SOURCE,
        "signs": [
            {"key": item.key, "labelEn": item.label_en, "labelHi": item.label_hi, "who": item.who, "level": item.level}
            for item in danger_signs.DANGER_SIGNS
        ],
    }


@app.post("/api/purge-transcripts")
async def purge_transcripts() -> dict:
    """Post-judging retention step: remove raw transcripts, keep the records."""
    return {"purged": db.purge_transcripts()}


@app.websocket("/ws/session")
async def voice_session(websocket: WebSocket) -> None:
    await websocket.accept()
    session = VisitSession()
    send_lock = asyncio.Lock()
    transcript_queue: asyncio.Queue[str] = asyncio.Queue()
    final_transcript = asyncio.Event()
    stt = None
    reader = None

    async def send_event(event: dict) -> None:
        # The assistant is deliberately text-only. Keep `speak` false at the
        # protocol boundary so no client can accidentally play server or
        # browser-generated speech, while microphone STT remains enabled.
        event["speak"] = False
        event.pop("audio", None)
        async with send_lock:
            await websocket.send_json(event)

    async def send_events(events: list[dict]) -> None:
        for event in events:
            if event.get("type") == "saved":
                try:
                    db.save_record(event["record"])
                except Exception:
                    session.saved = False
                    session.state = "awaiting_confirmation"
                    log.exception("Could not save visit for session %s", session.id)
                    await send_event({"type": "error", "message": "Could not save the visit. Please try confirming again."})
                    return
            await send_event(event)

    async def handle_message(message: dict) -> None:
        # Show deterministic flags immediately, before extraction or optional
        # speech synthesis can block the visual alert.
        alerted = False
        if message.get("type") in {"utterance", "correct"} and not session.saved:
            signs = session.updated_findings(str(message.get("text") or ""))
            if signs is not None:
                await send_event({"type": "danger", "signs": signs,
                                  "message": danger_signs.assessment_message(signs), "speak": True})
                alerted = True
        events = await session.handle(message)
        await send_events([event for event in events if not (alerted and event["type"] == "danger")])

    async def read_transcripts(upstream) -> None:
        async for frame in upstream:
            parsed = await parse_stt_message(frame)
            if not parsed:
                continue
            if parsed.get("error"):
                await send_event({"type": "error", "message": parsed["error"]})
                continue
            if parsed.get("text"):
                if not parsed["final"]:
                    await send_event({"type": "transcript", "text": parsed["text"], "final": False})
                    continue
                await transcript_queue.put(parsed["text"])
                final_transcript.set()

    async def process_transcripts() -> None:
        while True:
            text = await transcript_queue.get()
            try:
                await send_event({"type": "transcript", "text": text, "final": True})
                kind = "correct" if session.state == "awaiting_confirmation" and not session._is_confirmation(text) else "utterance"
                await handle_message({"type": kind, "text": text})
            except Exception:
                log.exception("voice response failed for session %s", session.id)
                await send_event({"type": "error", "message": "Voice response failed. Check Gnani Timbre configuration and try again."})
            finally:
                transcript_queue.task_done()

    await websocket.send_json(
        {
            "type": "hello",
            "sessionId": session.id,
            "demo": settings.demo_mode or not settings.speech_ready,
            "speech": "gnani" if settings.use_speech_api else "browser",
            "llm": settings.llm_model if settings.use_llm else "rules",
            "language": settings.language,
        }
    )
    processor = asyncio.create_task(process_transcripts())
    try:
        while True:
            packet = await websocket.receive()
            if packet.get("type") == "websocket.disconnect":
                break
            if packet.get("bytes") is not None:
                if stt is None:
                    await send_event({"type": "error", "message": "Live speech is not configured. Use the demo microphone or type a visit note."})
                else:
                    await stt.send(packet["bytes"])
                continue
            raw = packet.get("text")
            if not raw:
                continue
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                await send_event({"type": "error", "message": "Invalid JSON message"})
                continue
            if not isinstance(message, dict) or not isinstance(message.get("type"), str):
                await send_event({"type": "error", "message": "Message must include a string type"})
                continue
            if message.get("type") == "audio_start":
                if not settings.use_speech_api:
                    await send_event({"type": "error", "message": "Gnani live speech is not configured. Use demo browser speech or type a note."})
                    continue
                if stt is None:
                    try:
                        final_transcript.clear()
                        stt = await open_stt()
                        reader = asyncio.create_task(read_transcripts(stt))
                    except Exception:
                        log.exception("Prisma stream could not start for session %s", session.id)
                        stt = None
                        await send_event({"type": "error", "message": "Could not connect to Gnani Prisma. Check the API key and streaming access."})
                continue
            if message.get("type") == "audio_stop":
                if stt is not None:
                    await stt.send(json.dumps({"type": "commit"}))
                    try:
                        # Wait for Prisma's final transcript only. Queue processing
                        # also includes extraction and TTS, which can take much
                        # longer and must not be reported as a transcription error.
                        await asyncio.wait_for(final_transcript.wait(), timeout=15)
                    except asyncio.TimeoutError:
                        await send_event({
                            "type": "error",
                            "message": "No transcript came back from Gnani Prisma. Check the speech connection and try again.",
                        })
                    await stt.close()
                    stt = None
                    if reader is not None:
                        try:
                            await asyncio.wait_for(reader, timeout=1)
                        except asyncio.TimeoutError:
                            reader.cancel()
                        reader = None
                continue
            await handle_message(message)
    except WebSocketDisconnect:
        pass
    except Exception:  # keep the socket usable, never leak a traceback to the phone
        log.exception("session %s failed", session.id)
        try:
            await websocket.send_json({"type": "error", "message": "The session hit an error. Check the service settings and retry."})
        except Exception:
            pass
    finally:
        if reader is not None:
            reader.cancel()
        processor.cancel()
        if stt is not None:
            await stt.close()
