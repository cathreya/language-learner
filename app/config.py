from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: str = ""
    # Random secret used in the webhook URL path AND as the X-Telegram-Bot-Api-Secret-Token
    # value. Set this to a 32+ char random string before deploying.
    telegram_webhook_secret: str = ""

    groq_api_key: str = ""
    groq_stt_model: str = "whisper-large-v3-turbo"

    mistral_api_key: str = ""
    mistral_llm_model: str = "mistral-large-latest"

    google_tts_api_key: str = ""
    google_tts_voice: str = "it-IT-Chirp3-HD-Aoede"
    google_tts_language: str = "it-IT"

    # GCS bucket for audio storage (public-read). Same GCP project as the TTS API key.
    # Uses Application Default Credentials for uploads (gcloud auth application-default login
    # locally, service account on Cloud Run).
    gcs_bucket: str = ""
    gcs_audio_prefix: str = "audio/"

    data_dir: Path = Path("./data")
    public_base_url: str = "http://localhost:8000"

    # Firestore — uses ADC for auth. PROJECT_ID defaults to whatever ADC discovers.
    gcp_project: str = ""
    firestore_database: str = "(default)"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"

    source_lang: str = "en"
    target_lang: str = "it"
    target_lang_name: str = "Italian"

    # Max number of "new" (never-reviewed) cards to introduce per calendar day.
    # FSRS doesn't enforce this; we filter in app.db.due_cards. Anki's default is 20.
    daily_new_card_limit: int = 20

    @property
    def audio_dir(self) -> Path:
        return self.data_dir / "audio"

    @property
    def cards_dir(self) -> Path:
        return self.data_dir / "cards"

    def ensure_dirs(self) -> None:
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self.audio_dir.mkdir(parents=True, exist_ok=True)
            self.cards_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            # Read-only / non-writable parent (e.g. Cloud Run's root FS). Local
            # dirs are only used as a fallback when GCS isn't configured; if we
            # can't create them, the storage helpers will fail loudly later.
            pass


settings = Settings()
settings.ensure_dirs()
