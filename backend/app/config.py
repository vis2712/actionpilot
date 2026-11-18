from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    # Infrastructure (defaults match docker compose, ports chosen so they don't clash with DocuMind)
    database_url: str = "postgresql://actionpilot:actionpilot@localhost:5434/actionpilot"
    redis_url: str = "redis://localhost:6380/0"
    queue_backend: Literal["rq", "background"] = "rq"
    upload_dir: str = "data/uploads"
    max_upload_mb: int = 25  # Whisper APIs accept up to 25 MB
    root_path: str = ""  # "/api" when served behind the web container's /api proxy (makes /api/docs work)

    # LLM: any OpenAI-compatible API (Gemini by default). "fake" runs without a key (for tests).
    llm_provider: Literal["openai_compatible", "fake"] = "openai_compatible"
    llm_api_key: str = ""
    llm_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    chat_model: str = "gemini-2.5-flash"
    embed_model: str = "gemini-embedding-001"
    embed_dim: int = 768
    llm_max_retries: int = 4
    price_input_per_m: float = 0.30
    price_output_per_m: float = 2.50

    # Speech-to-text (Whisper). Groq's free tier by default; OpenAI works too.
    stt_api_key: str = ""
    stt_base_url: str = "https://api.groq.com/openai/v1"
    stt_model: str = "whisper-large-v3-turbo"

    # Agent
    agent_max_steps: int = 8
    timezone: str = "Asia/Kolkata"
    process_max_retries: int = 3

    # Auth
    jwt_secret: str = ""
    jwt_expire_minutes: int = 60 * 12

    # Email: "console" logs emails instead of sending them; "smtp" really sends
    email_backend: Literal["console", "smtp"] = "console"
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    email_from: str = ""

    # Webhooks (e.g. a meeting tool posting transcripts). Empty = webhook disabled.
    webhook_secret: str = ""
    webhook_tolerance_seconds: int = 300

    def cost_usd(self, prompt_tokens: int, completion_tokens: int) -> float:
        return round(
            prompt_tokens / 1e6 * self.price_input_per_m + completion_tokens / 1e6 * self.price_output_per_m, 6
        )


settings = Settings()
