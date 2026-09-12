from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Konfigurasi gateway; bisa di-override lewat file .env / environment."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # --- Polling Keterbukaan Informasi IDX ---
    idx_poll_seconds: int = 180
    idx_lookback_minutes: int = 10
    idx_issuers: str = ""
    idx_keywords: str = ""
    idx_emiten_type: str = "*"
    idx_cookie: str = ""

    # --- Webhook keluar (outbound): push disclosure baru ke sistem Anda ---
    webhook_url: str = ""
    webhook_format: Literal["generic", "discord", "slack"] = "generic"
    webhook_secret: str = ""
    webhook_respect_filter: bool = True

    # --- Agen LLM + Telegram (opsional; kosongkan untuk mode webhook-only) ---
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
    llm_base_url: str | None = None
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # --- Kurasi oleh LLM (node triage) ---
    # Profil minat Anda; dipakai LLM untuk menilai apakah suatu pengumuman layak
    # dikirim. Tulis bebas dalam Bahasa Indonesia, sespesifik mungkin.
    agent_profile: str = (
        "Investor ritel jangka menengah di pasar saham Indonesia. Fokus pada aksi "
        "korporasi material: dividen, buyback, right issue, akuisisi, merger, stock "
        "split. Juga perubahan kepemilikan signifikan, kinerja keuangan kuartalan, "
        "serta sanksi dan suspensi dari bursa atau regulator. Abaikan laporan "
        "administratif rutin seperti registrasi pemegang efek atau perubahan alamat."
    )
    # Skor materialitas 1-5 dari triage. Di bawah ambang ini, pengumuman tidak
    # diringkas dan tidak dikirim. 3 = longgar, 4 = ketat.
    agent_min_importance: int = 3
    # Matikan untuk kembali ke filter kata kunci saja (hemat satu panggilan LLM).
    agent_llm_triage: bool = True

    # --- Secret endpoint inbound: /webhook/idx, /trigger, /stats, /ws/idx ---
    idx_webhook_secret: str = ""

    # --- Lain-lain ---
    database_path: Path = Path("idx_agent.sqlite3")
    host: str = "127.0.0.1"
    port: int = 8000

    @property
    def issuers(self) -> set[str]:
        return {value.strip().upper() for value in self.idx_issuers.split(",") if value.strip()}

    @property
    def keywords(self) -> tuple[str, ...]:
        return tuple(value.strip().lower() for value in self.idx_keywords.split(",") if value.strip())

    @property
    def agent_enabled(self) -> bool:
        return bool(self.openai_api_key and self.telegram_bot_token and self.telegram_chat_id)


@lru_cache
def get_settings() -> Settings:
    return Settings()
