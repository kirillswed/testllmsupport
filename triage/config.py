import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from dotenv import load_dotenv

from .llm_profiles import MODEL_PROFILES

ALLOWED_MODELS = tuple(MODEL_PROFILES)


@dataclass(frozen=True)
class Settings:
    api_key: str = ""
    model: str = "openrouter/free"
    invoice_alert_usd: Decimal = Decimal("1000")
    llm_timeout: float = 60
    llm_attempts: int = 3
    llm_min_interval: float = 3.1

    @classmethod
    def from_env(cls):
        load_dotenv(".env", encoding="utf-8-sig")
        try:
            result = cls(
                api_key=os.getenv("OPENROUTER_API_KEY", "").strip(),
                model=os.getenv("OPENROUTER_MODEL", "openrouter/free").strip(),
                invoice_alert_usd=Decimal(os.getenv("INVOICE_ALERT_USD", "1000")),
                llm_timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "60")),
                llm_attempts=int(os.getenv("LLM_ATTEMPTS", "3")),
                llm_min_interval=float(os.getenv("LLM_MIN_INTERVAL_SECONDS", "3.1")),
            )
            if not result.invoice_alert_usd.is_finite() or result.invoice_alert_usd <= 0:
                raise ValueError("INVOICE_ALERT_USD must be positive and finite")
            if not 1 <= result.llm_attempts <= 5 or not 1 <= result.llm_timeout <= 120:
                raise ValueError("LLM_ATTEMPTS must be 1..5; timeout 1..120 seconds")
            if not 0 <= result.llm_min_interval <= 60:
                raise ValueError("LLM_MIN_INTERVAL_SECONDS must be 0..60")
            if result.model not in ALLOWED_MODELS:
                raise ValueError("Supported models: " + ", ".join(ALLOWED_MODELS))
            return result
        except (ValueError, InvalidOperation) as exc:
            raise ValueError(f"Invalid configuration: {exc}") from None
