"""Capabilities and pricing policy of explicitly supported OpenRouter models."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ModelProfile:
    response_mode: Literal["json_schema", "prompt_json"]
    free: bool
    reasoning_enabled: bool | None = None
    max_prompt_price: float | None = None
    max_completion_price: float | None = None


MODEL_PROFILES = {
    "openrouter/free": ModelProfile("json_schema", free=True),
    "z-ai/glm-5.2:free": ModelProfile("prompt_json", free=True, reasoning_enabled=False),
    "z-ai/glm-5.2": ModelProfile(
        "json_schema", free=False, reasoning_enabled=False,
        max_prompt_price=1, max_completion_price=3,
    ),
}


def get_profile(model: str) -> ModelProfile:
    try:
        return MODEL_PROFILES[model]
    except KeyError:
        raise ValueError("Unsupported model; add an explicit ModelProfile first") from None
