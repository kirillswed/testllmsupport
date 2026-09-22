"""Typed extraction and deterministic business validation."""

import re
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CATEGORIES = ("support", "finance_invoice", "sales_lead", "bug_report", "spam", "unknown")
Category = Literal["support", "finance_invoice", "sales_lead", "bug_report", "spam", "unknown"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Fields(StrictModel):
    # A shared nullable schema makes routing across free models simpler. Business
    # rules below determine which fields are mandatory for a given category.
    vendor: str | None = None
    amount: Decimal | None = Field(default=None, gt=0, le=Decimal("1000000000000000"), allow_inf_nan=False)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    invoice_number: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    payment_terms: str | None = None
    company: str | None = None
    contact_name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    employee_count: int | None = Field(default=None, gt=0, strict=True)
    interest: str | None = None
    product: str | None = None
    version: str | None = None
    browser: str | None = None
    severity: Literal["low", "medium", "high", "critical"] | None = None
    affected_feature: str | None = None

    @field_validator("invoice_date", "due_date", mode="before")
    @classmethod
    def iso_date_only(cls, value):
        if value is not None and not isinstance(value, date):
            if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError("date must be YYYY-MM-DD or null")
        return value

    @field_validator("amount", mode="before")
    @classmethod
    def no_boolean_amount(cls, value):
        if isinstance(value, bool):
            raise ValueError("amount must be numeric, not boolean")
        return value

    @field_validator("contact_email")
    @classmethod
    def email_shape(cls, value):
        if value is not None and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("invalid email format")
        return value


class Extraction(StrictModel):
    category: Category
    summary: str = Field(min_length=1, max_length=1000)
    fields: Fields = Field(default_factory=Fields)
    security_flags: list[Literal["prompt_injection"]] = Field(default_factory=list)


ALLOWED_FIELDS = {
    "support": {"contact_name", "contact_email", "contact_phone", "product"},
    "finance_invoice": {"vendor", "amount", "currency", "invoice_number", "invoice_date", "due_date", "payment_terms"},
    "sales_lead": {"company", "contact_name", "contact_email", "contact_phone", "employee_count", "interest"},
    "bug_report": {"product", "version", "browser", "severity", "affected_feature"},
    "spam": set(),
    "unknown": set(),
}


def business_issues(extraction: Extraction) -> list[str]:
    fields = extraction.fields
    required = {
        "finance_invoice": ("vendor", "amount", "currency", "invoice_number"),
        "sales_lead": ("company", "interest"),
        "bug_report": ("severity",),
    }.get(extraction.category, ())
    issues = [f"missing:{name}" for name in required if getattr(fields, name) in (None, "")]
    if extraction.category == "sales_lead":
        phone = fields.contact_phone or ""
        phone_usable = bool(re.fullmatch(r"\+?[\d ()-]+", phone)) and 7 <= len(re.sub(r"\D", "", phone)) <= 15
        if not fields.contact_email and not phone_usable:
            issues.append("missing:usable_contact")
    if fields.invoice_date and fields.due_date and fields.due_date < fields.invoice_date:
        issues.append("invalid:due_date_before_invoice_date")
    for name, value in fields.model_dump().items():
        if value is not None and name not in ALLOWED_FIELDS[extraction.category]:
            issues.append(f"unexpected:{name}")
    if extraction.category == "unknown":
        issues.append("unknown_category")
    if extraction.security_flags:
        issues.append("security:prompt_injection")
    return issues


def response_schema() -> dict:
    schema = Extraction.model_json_schema()

    def make_strict(node):
        if isinstance(node, dict):
            node.pop("default", None)
            if node.get("type") == "object":
                node["required"] = list(node.get("properties", {}))
                node["additionalProperties"] = False
            for child in node.values():
                make_strict(child)
        elif isinstance(node, list):
            for child in node:
                make_strict(child)

    make_strict(schema)
    return schema
