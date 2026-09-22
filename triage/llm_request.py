"""Build model requests without mixing external content with instructions."""

import hashlib
import json
from dataclasses import asdict

from .llm_profiles import get_profile
from .models import response_schema
from .prompt import PROMPT_VERSION, SYSTEM_PROMPT, build_messages


def sha256_json(value) -> str:
    serialized = json.dumps(value, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def request_contract(settings) -> dict:
    profile = get_profile(settings.model)
    contract = {
        "prompt_version": PROMPT_VERSION,
        "system_prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest(),
        "schema_sha256": sha256_json(response_schema()),
        "requested_model": settings.model,
        "profile": asdict(profile),
        "input_envelope": "untrusted-json-v1",
    }
    return {**contract, "fingerprint": sha256_json(contract)}


def build_request(settings, text: str) -> dict:
    profile = get_profile(settings.model)
    schema = response_schema()
    payload = {
        "model": settings.model,
        "messages": build_messages(text, schema if profile.response_mode == "prompt_json" else None),
        "temperature": 0,
        "max_tokens": 4096,
        "provider": {"require_parameters": True},
    }
    if profile.response_mode == "json_schema":
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "inbox_extraction", "strict": True, "schema": schema},
        }
    if profile.reasoning_enabled is not None:
        payload["reasoning"] = {"enabled": profile.reasoning_enabled}
    if profile.max_prompt_price is not None:
        payload["provider"]["max_price"] = {
            "prompt": profile.max_prompt_price,
            "completion": profile.max_completion_price,
        }
    return payload
