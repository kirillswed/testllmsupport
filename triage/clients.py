"""HTTP clients with bounded retries, safe diagnostics and usage accounting."""

import json
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

import httpx
from pydantic import ValidationError

from .config import Settings
from .models import Extraction, Fields
from .llm_profiles import get_profile
from .llm_request import build_request, request_contract

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
FX_URL = "https://open.er-api.com/v6/latest/USD"
FX_ATTRIBUTION = "Rates By Exchange Rate API — https://www.exchangerate-api.com"


class ExternalError(Exception):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def retry_delay(response: httpx.Response | None, attempt: int) -> float | None:
    """None means the server asks for a longer wait than this CLI should take."""
    value = response.headers.get("Retry-After") if response is not None else None
    if value:
        try:
            seconds = float(value)
        except ValueError:
            from email.utils import parsedate_to_datetime
            try:
                seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                seconds = 2 ** attempt
        if seconds > 30:
            return None
        return max(seconds, 0)
    return min(2 ** attempt, 8)


class OpenRouterClient:
    def __init__(self, settings: Settings, client: httpx.Client, on_call=None, sleep=time.sleep):
        self.settings = settings
        self.profile = get_profile(settings.model)
        self.contract = request_contract(settings)
        self.client = client
        self.on_call = on_call or (lambda _: None)
        self.sleep = sleep
        self.last_request_at = 0.0
        self.calls: list[dict] = []
        self.fatal_error: str | None = None

    def classify(self, text: str, content_hash: str) -> Extraction:
        if not self.settings.api_key:
            raise ExternalError("llm:missing_api_key")
        if self.fatal_error:
            raise ExternalError(self.fatal_error)
        payload = build_request(self.settings, text)
        base_system_prompt = payload["messages"][0]["content"]
        last_error = "llm:unknown_error"
        for attempt in range(1, self.settings.llm_attempts + 1):
            elapsed = time.monotonic() - self.last_request_at
            self.sleep(max(0, self.settings.llm_min_interval - elapsed))
            response = None
            retryable = True
            extraction = None
            record = {
                "content_hash": content_hash, "attempt": attempt,
                "requested_model": self.settings.model, "model": None,
                "generation_id": None, "prompt_tokens": None, "completion_tokens": None,
                "cost_usd": "0" if self.profile.free else None,
                "cost_source": "free_model_tariff" if self.profile.free else "unavailable",
                "prompt_version": self.contract["prompt_version"],
                "processing_fingerprint": self.contract["fingerprint"],
                "started_at": utc_now(), "error": None,
                "max_tokens": payload["max_tokens"],
            }
            self.last_request_at = time.monotonic()
            try:
                response = self.client.post(
                    OPENROUTER_URL, json=payload,
                    headers={"Authorization": f"Bearer {self.settings.api_key}"},
                    timeout=self.settings.llm_timeout,
                )
                response.raise_for_status()
                body = response.json()
                if not isinstance(body, dict):
                    raise ValueError("response must be an object")
                record["model"] = body.get("model")
                record["generation_id"] = body.get("id")
                usage = body.get("usage") or {}
                if not isinstance(usage, dict):
                    raise ValueError("invalid usage")
                for name in ("prompt_tokens", "completion_tokens"):
                    value = usage.get(name)
                    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                        record[name] = value
                if usage.get("cost") is not None:
                    cost = Decimal(str(usage["cost"]))
                    if cost.is_finite() and cost >= 0:
                        record.update(cost_usd=str(cost), cost_source="api_usage")
                if "error" in body:
                    last_error = "llm:provider_error"
                    error = body["error"] if isinstance(body["error"], dict) else {}
                    record["provider_error_code"] = error.get("code")
                    record["provider_error_message"] = str(error.get("message", ""))[:500].replace(self.settings.api_key, "[REDACTED]")
                else:
                    choice = body["choices"][0]
                    if not isinstance(choice, dict):
                        raise ValueError("invalid choice")
                    if choice.get("finish_reason") == "length":
                        last_error = "llm:truncated_response"
                        payload["max_tokens"] = 8192
                    else:
                        content = choice["message"]["content"]
                        if not isinstance(content, str):
                            raise ValueError("missing text content")
                        content = content.strip()
                        if content.startswith(("```json\n", "```\n")) and content.endswith("\n```"):
                            content = content.split("\n", 1)[1].rsplit("\n", 1)[0]
                        extraction = Extraction.model_validate_json(content)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                last_error = f"llm:http_{status}"
                try:
                    error = exc.response.json().get("error", {})
                    if isinstance(error, dict):
                        record["provider_error_code"] = error.get("code")
                        record["provider_error_message"] = str(error.get("message", ""))[:500].replace(self.settings.api_key, "[REDACTED]")
                except (ValueError, AttributeError):
                    pass
                retryable = status in (408, 429) or status >= 500
                if status in (401, 402, 403):
                    self.fatal_error = last_error
            except httpx.RequestError:
                last_error = "llm:network_error"
            except ValidationError as exc:
                # Do not include raw input values or model content in error logs.
                known = set(Fields.model_fields) | set(Extraction.model_fields)
                locations = [".".join(str(p) if p in known else "unknown_field" for p in e["loc"]) or "root" for e in exc.errors()]
                last_error = "llm:invalid_output:" + ",".join(locations[:5])
                # Feedback describes schema locations only, never promotes an
                # untrusted model response into system instructions.
                payload["messages"][0]["content"] = base_system_prompt + "\nCorrect invalid fields: " + ", ".join(locations[:5])
            except (ValueError, KeyError, IndexError, TypeError, InvalidOperation):
                last_error = "llm:invalid_response"
            record["error"] = None if extraction is not None else last_error
            self.calls.append(record)
            self.on_call(record)
            if extraction is not None:
                return extraction
            if not retryable or attempt == self.settings.llm_attempts:
                break
            delay = retry_delay(response, attempt)
            if delay is None:
                if response is not None and response.status_code == 429:
                    self.fatal_error = last_error
                break
            self.sleep(delay)
        raise ExternalError(last_error)


class ExchangeRateClient:
    def __init__(self, client: httpx.Client, cache_path: Path, sleep=time.sleep):
        self.client = client
        self.cache_path = cache_path
        self.sleep = sleep
        self.snapshot: dict | None = None
        self.requests = 0
        self.last_error: str | None = None

    @staticmethod
    def validate(body: dict) -> dict:
        if not isinstance(body, dict):
            raise ValueError("FX response must be an object")
        if body.get("result") != "success" or body.get("base_code") != "USD":
            raise ValueError("invalid FX response")
        if not isinstance(body.get("rates"), dict):
            raise ValueError("missing FX rates")
        last = int(body["time_last_update_unix"])
        next_update = int(body["time_next_update_unix"])
        now = time.time()
        if last > now + 300 or last <= 0 or next_update <= last or next_update <= now:
            raise ValueError("stale or invalid FX timestamps")
        usd = Decimal(str(body["rates"]["USD"]))
        if usd != 1:
            raise ValueError("invalid USD base rate")
        return body

    def load(self) -> dict:
        if self.snapshot is not None:
            return self.snapshot
        if self.last_error:
            raise ExternalError(self.last_error)
        try:
            cached = json.loads(self.cache_path.read_text(encoding="utf-8"))
            self.snapshot = self.validate(cached)
            return self.snapshot
        except (OSError, ValueError, KeyError, TypeError, InvalidOperation):
            pass
        for attempt in range(1, 3):
            response = None
            retryable = True
            self.requests += 1
            try:
                response = self.client.get(FX_URL, timeout=15)
                response.raise_for_status()
                self.snapshot = self.validate(response.json())
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                temp = self.cache_path.with_suffix(".tmp")
                temp.write_text(json.dumps(self.snapshot), encoding="utf-8")
                temp.replace(self.cache_path)
                return self.snapshot
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                self.last_error = f"fx:http_{status}"
                retryable = status in (408, 429) or status >= 500
            except httpx.RequestError:
                self.last_error = "fx:network_error"
            except (ValueError, KeyError, TypeError, InvalidOperation):
                self.last_error = "fx:invalid_or_stale_response"
            if not retryable or attempt == 2:
                break
            delay = retry_delay(response, attempt)
            if delay is None:
                break
            self.sleep(delay)
        raise ExternalError(self.last_error or "fx:unknown_error")

    def convert(self, amount: Decimal, currency: str) -> dict:
        if currency == "USD":
            return {"amount_usd": str(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
                    "units_per_usd": "1", "source": "identity", "rate_date": None}
        body = self.load()
        try:
            rate = Decimal(str(body["rates"][currency]))
            if not rate.is_finite() or rate <= 0:
                raise ValueError("invalid rate")
            converted = (amount / rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except (KeyError, InvalidOperation, ValueError):
            raise ExternalError(f"fx:unsupported_or_invalid_currency:{currency}") from None
        return {
            "amount_usd": str(converted), "units_per_usd": str(rate),
            "source": FX_URL,
            "rate_date": datetime.fromtimestamp(body["time_last_update_unix"], timezone.utc).isoformat(),
        }
