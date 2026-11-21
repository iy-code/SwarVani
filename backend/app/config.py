"""Environment-backed configuration.

Everything is read once at import time and exposed as a frozen Settings object.
No secret is ever logged or returned by an API route.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _flag(name: str, default: bool) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    demo_mode: bool = field(default_factory=lambda: _flag("SV_DEMO_MODE", True))
    db_path: Path = field(
        default_factory=lambda: BASE_DIR / (_env("SV_DB_PATH", ".data/swarvani.db") or ".data/swarvani.db")
    )
    db_key: str = field(default_factory=lambda: _env("SV_DB_KEY"))
    gnani_api_key: str = field(default_factory=lambda: _env("GNANI_API_KEY"))
    gnani_stt_ws_url: str = field(
        default_factory=lambda: _env("GNANI_STT_WS_URL", "wss://api.vachana.ai/stt/v3/stream")
    )
    gnani_tts_ws_url: str = field(
        default_factory=lambda: _env("GNANI_TTS_WS_URL", "wss://api.vachana.ai/api/v1/tts")
    )
    gnani_tts_voice: str = field(default_factory=lambda: _env("GNANI_TTS_VOICE", "Shivani"))
    language: str = field(default_factory=lambda: _env("GNANI_LANGUAGE", "hi-IN"))
    llm_base_url: str = field(default_factory=lambda: _env("LLM_BASE_URL"))
    llm_api_key: str = field(default_factory=lambda: _env("LLM_API_KEY"))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL"))
    whatsapp_verify_token: str = field(default_factory=lambda: _env("WHATSAPP_VERIFY_TOKEN"))
    whatsapp_app_secret: str = field(default_factory=lambda: _env("WHATSAPP_APP_SECRET"))
    whatsapp_access_token: str = field(default_factory=lambda: _env("WHATSAPP_ACCESS_TOKEN"))
    whatsapp_phone_number_id: str = field(default_factory=lambda: _env("WHATSAPP_PHONE_NUMBER_ID"))
    whatsapp_test_recipient: str = field(default_factory=lambda: _env("WHATSAPP_TEST_RECIPIENT"))
    whatsapp_api_version: str = field(default_factory=lambda: _env("WHATSAPP_API_VERSION", "v24.0"))
    allowed_origins: list[str] = field(
        default_factory=lambda: [
            o.strip()
            for o in _env(
                "SV_ALLOWED_ORIGINS",
                "http://localhost:5173,http://127.0.0.1:5173",
            ).split(",")
            if o.strip()
        ]
    )

    @property
    def speech_ready(self) -> bool:
        """True when Gnani speech-to-text is configured."""
        return bool(self.gnani_api_key and self.gnani_stt_ws_url)

    @property
    def llm_ready(self) -> bool:
        return bool(self.llm_base_url and self.llm_model)

    @property
    def use_speech_api(self) -> bool:
        return (not self.demo_mode) and self.speech_ready

    @property
    def use_llm(self) -> bool:
        return (not self.demo_mode) and self.llm_ready

    @property
    def whatsapp_webhook_ready(self) -> bool:
        return bool(self.whatsapp_verify_token and self.whatsapp_app_secret)

    @property
    def whatsapp_sending_ready(self) -> bool:
        return bool(self.whatsapp_access_token and self.whatsapp_phone_number_id)


settings = Settings()
