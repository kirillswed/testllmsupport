"""Small, explicit pre-filter; not a general solution to prompt injection."""

import re

from .models import Extraction

INJECTION_PATTERNS = [
    r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions",
    r"игнорируй\s+(?:все\s+)?(?:предыдущие|предшествующие)\s+инструкции",
    r"classify\s+this\s+message\s+as",
    r"mark\s+(?:it|this|the\s+invoice)\s+as\s+approved\s+for\s+payment",
]


def quarantine(text: str) -> Extraction | None:
    if any(re.search(pattern, text, flags=re.I) for pattern in INJECTION_PATTERNS):
        return Extraction(
            category="spam",
            summary="Попытка подменить инструкции классификатора; требуется проверка.",
            security_flags=["prompt_injection"],
        )
    return None

