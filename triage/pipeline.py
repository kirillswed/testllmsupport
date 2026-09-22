import hashlib
import json
import uuid
from collections import Counter
from decimal import Decimal
from pathlib import Path

import httpx

from .clients import ExchangeRateClient, ExternalError, FX_ATTRIBUTION, OpenRouterClient, utc_now
from .config import Settings
from .models import CATEGORIES, Extraction, business_issues
from .security import quarantine
from .storage import Store

MAX_MESSAGE_BYTES = 100_000


def digest_text(text: str) -> str:
    # Only transport differences are normalized; message meaning is not changed.
    canonical = text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def failure(reason: str, stage: str) -> dict:
    return {
        **Extraction(category="unknown", summary="Автоматическая обработка не завершена.").model_dump(mode="json"),
        "status": "error", "issues": [reason], "error_stage": stage,
        "enrichment": None, "classification_source": "error", "model": None,
    }


def process(text, digest, cached, llm, fx, settings) -> dict:
    guarded = quarantine(text)
    model = None
    if guarded:
        extraction = guarded
        source = "security_guard"
    elif cached and cached.get("error_stage") == "fx":
        # Retry enrichment without paying for classification a second time.
        extraction = Extraction.model_validate({key: cached[key] for key in ("category", "summary", "fields", "security_flags")})
        source, model = cached["classification_source"], cached["model"]
    else:
        try:
            extraction = llm.classify(text, digest)
            source = "llm"
            model = llm.calls[-1]["model"]
        except ExternalError as exc:
            return failure(str(exc), "llm")
    if extraction.security_flags:
        # Defense in depth: even if a model flags an attack but emits invoice
        # fields, do not allow those fields to reach the invoice workflow.
        extraction = Extraction(category="spam", summary=extraction.summary,
                                security_flags=["prompt_injection"])
    issues = business_issues(extraction)
    result = {
        **extraction.model_dump(mode="json"),
        "status": "needs_review" if issues else "completed", "issues": issues,
        "error_stage": None, "enrichment": None, "classification_source": source, "model": model,
    }
    if extraction.category == "finance_invoice" and extraction.fields.amount and extraction.fields.currency:
        try:
            result["enrichment"] = fx.convert(extraction.fields.amount, extraction.fields.currency)
        except ExternalError as exc:
            result["issues"].append(str(exc))
            result.update(status="error", error_stage="fx")
    return result


def alerts(result: dict, settings: Settings) -> list[tuple[str, str, str]]:
    """(stable notification key, owner, explanation)."""
    notices = []
    if result["category"] == "unknown":
        notices.append(("unknown", "operations", "unknown: требуется ручной разбор"))
    if result["security_flags"]:
        notices.append(("security", "security", "обнаружена попытка подмены инструкций"))
    if result["category"] == "bug_report" and result["fields"]["severity"] == "critical":
        notices.append(("critical_bug", "engineering", "критичная ошибка блокирует работу"))
    enrichment = result.get("enrichment")
    if result["category"] == "finance_invoice" and enrichment:
        amount = Decimal(enrichment["amount_usd"])
        if amount > settings.invoice_alert_usd:
            notices.append(("large_invoice", "finance", f"счёт {amount} USD выше порога {settings.invoice_alert_usd} USD"))
    return notices


def write_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def run(inbox: Path, db_path: Path, reports: Path, settings: Settings,
        retry_errors=False, client: httpx.Client | None = None, emit=print,
        refresh_cache=False) -> dict:
    if not inbox.is_dir():
        raise ValueError(f"Inbox directory does not exist: {inbox}")
    paths = sorted(inbox.glob("*.txt"))
    if not paths:
        raise ValueError(f"No .txt files in {inbox}")
    started = utc_now()
    run_id = started.replace(":", "").replace("+", "_") + "_" + uuid.uuid4().hex[:8]
    store = Store(db_path)
    own_client = client is None
    client = client or httpx.Client(follow_redirects=False)
    try:
        store.start_run(run_id)
        llm = OpenRouterClient(settings, client, on_call=lambda record: store.add_call(run_id, record))
        fx = ExchangeRateClient(client, db_path.parent / "fx_cache.json")
        results, notifications, seen = {}, [], {}
        dispositions = Counter()
        for path in paths:
            emit(f"Processing {path.name}...")
            digest = None
            try:
                if path.stat().st_size > MAX_MESSAGE_BYTES:
                    raise ValueError("file_too_large")
                text = path.read_text(encoding="utf-8-sig")
                if not text.strip():
                    raise ValueError("empty_message")
            except (OSError, UnicodeError, ValueError) as exc:
                reason = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, UnicodeError) else type(exc).__name__
                result, disposition = failure(f"input:{reason}", "input"), "read_error"
                emit(f"[ALERT][operations] {path.name}: unknown, ошибка чтения входного файла")
                notifications.append({"filename": path.name, "owner": "operations", "reason": "input_error"})
            else:
                digest = digest_text(text)
                cached = store.get(digest)
                duplicate_of = seen.get(digest)
                if duplicate_of:
                    result, disposition = cached, "duplicate"
                elif cached and not refresh_cache and not (retry_errors and cached["status"] == "error"):
                    result, disposition = cached, "cached"
                else:
                    disposition = ("refreshed" if refresh_cache else "retried") if cached else "new"
                    result = process(text, digest, None if refresh_cache else cached, llm, fx, settings)
                    result["processing_fingerprint"] = llm.contract["fingerprint"]
                    store.save(digest, path.name, text, result)
                seen.setdefault(digest, path.name)
                for key, owner, explanation in alerts(result, settings):
                    message = f"[ALERT][{owner}] {path.name}: {explanation}"
                    if store.notify_once(digest, key, message, emit=emit):
                        notifications.append({"filename": path.name, "owner": owner, "reason": key})
            record = {**result, "content_hash": digest, "disposition": disposition,
                      "cache_stale": bool(digest and result.get("processing_fingerprint") != llm.contract["fingerprint"]),
                      "duplicate_of": seen.get(digest) if disposition == "duplicate" else None}
            results[path.name] = record
            dispositions[disposition] += 1
            store.add_item(run_id, path.name, digest, disposition, record)
        manual = {}
        for filename, result in results.items():
            reasons = result["issues"] + [key for key, _, _ in alerts(result, settings)]
            if reasons:
                key = result["content_hash"] or filename
                item = manual.setdefault(key, {"filenames": [], "category": result["category"],
                                               "reasons": list(dict.fromkeys(reasons))})
                item["filenames"].append(filename)
        unique_results = [results[name] for name in seen.values()]
        costs = sum((Decimal(call["cost_usd"]) for call in llm.calls if call["cost_usd"] is not None), Decimal(0))
        unknown_costs = sum(call["cost_usd"] is None for call in llm.calls)
        report = {
            "run_id": run_id, "started_at": started, "finished_at": utc_now(),
            "mode": "live", "requested_model": settings.model,
            "llm_contract": llm.contract,
            "refreshed_records": dispositions["refreshed"],
            "stale_cache_records": sum(r["cache_stale"] for r in unique_results),
            "input_files": len(paths), "unique_messages": len(seen),
            "new_records": dispositions["new"], "retried_records": dispositions["retried"],
            "cached_records": dispositions["cached"], "duplicates_in_run": dispositions["duplicate"],
            "read_errors": dispositions["read_error"],
            "error_files": sum(r["status"] == "error" for r in results.values()),
            "categories_by_file": {c: sum(r["category"] == c for r in results.values()) for c in CATEGORIES},
            "categories_unique": {c: sum(r["category"] == c for r in unique_results) for c in CATEGORIES},
            "manual_attention": list(manual.values()), "notifications_sent": notifications,
            "llm": {
                "http_attempts": len(llm.calls),
                "successful_calls": sum(c["error"] is None for c in llm.calls),
                "prompt_tokens": sum(c["prompt_tokens"] or 0 for c in llm.calls),
                "completion_tokens": sum(c["completion_tokens"] or 0 for c in llm.calls),
                "calls_without_token_usage": sum(c["prompt_tokens"] is None or c["completion_tokens"] is None for c in llm.calls),
                "cost_usd": str(costs) if not unknown_costs else None,
                "known_cost_usd": str(costs), "calls_without_cost": unknown_costs,
                "cost_note": "API usage.cost where available; zero tariff fallback only for free models. Paid calls with missing usage have unknown cost. Token totals include only reported usage.",
                "calls": llm.calls,
            },
            "fx_http_requests": fx.requests, "fx_attribution": FX_ATTRIBUTION,
            "invoice_alert_usd": str(settings.invoice_alert_usd), "results": results,
        }
        store.finish_run(run_id, report)
        write_json(reports / f"{run_id}.json", report)
        write_json(reports / "latest.json", report)
        emit(f"Files: {len(paths)}; unique: {len(seen)}; new: {dispositions['new']}; "
             f"cached: {dispositions['cached']}; duplicates: {dispositions['duplicate']}; errors: {report['error_files']}")
        if report["stale_cache_records"]:
            emit(f"WARNING: {report['stale_cache_records']} cached records use an older prompt/schema/model. "
                 "Use --refresh-cache to explicitly reprocess them (may incur API costs).")
        emit("Categories (files): " + json.dumps(report["categories_by_file"]))
        cost_label = f"{costs} USD" if not unknown_costs else f"at least {costs} USD ({unknown_costs} calls without cost data)"
        emit(f"Manual attention: {len(manual)}; LLM attempts: {len(llm.calls)}; cost: {cost_label}")
        emit(FX_ATTRIBUTION)
        emit(f"Report: {reports / 'latest.json'}")
        return report
    finally:
        store.close()
        if own_client:
            client.close()
