"""Compare saved run results against independently specified expectations."""

import argparse
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from triage.pipeline import write_json


def get_path(record: dict, path: str):
    current = record
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def equal(expected, actual) -> bool:
    if expected is None or actual is None:
        return expected is actual
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        try:
            return not isinstance(actual, bool) and Decimal(str(expected)) == Decimal(str(actual))
        except InvalidOperation:
            return False
    if isinstance(expected, str) and isinstance(actual, str):
        return " ".join(expected.casefold().split()) == " ".join(actual.casefold().split())
    return expected == actual


def normalize_text(value: str) -> str:
    # The integration product 1C is commonly spelled with Latin or Cyrillic C.
    return " ".join(value.casefold().replace("1с", "1c").split())


def evaluate(expected: dict, report: dict) -> dict:
    results = report.get("results", {})
    details = []
    categories_correct = field_correct = field_total = complete = 0
    for filename, specification in expected.items():
        actual = results.get(filename, {})
        category_ok = actual.get("category") == specification["category"]
        categories_correct += category_ok
        mismatches = []
        for path, wanted in specification.get("checks", {}).items():
            exists, got = get_path(actual, path)
            correct = exists and equal(wanted, got)
            field_correct += correct
            field_total += 1
            if not correct:
                mismatches.append({"field": path, "expected": wanted, "actual": got, "exists": exists})
        for path, terms in specification.get("contains_all", {}).items():
            exists, got = get_path(actual, path)
            for term in terms:
                correct = exists and isinstance(got, str) and normalize_text(term) in normalize_text(got)
                field_correct += correct
                field_total += 1
                if not correct:
                    mismatches.append({"field": path, "expected_contains": term, "actual": got, "exists": exists})
        if not category_ok:
            mismatches.insert(0, {"field": "category", "expected": specification["category"], "actual": actual.get("category")})
        complete += not mismatches
        details.append({"filename": filename, "passed": not mismatches, "mismatches": mismatches})
    count = len(expected)
    unexpected = sorted(set(results) - set(expected))
    return {
        "run_id": report.get("run_id"), "run_mode": report.get("mode"),
        "expected_messages": count, "present_messages": sum(name in results for name in expected),
        "category_accuracy": categories_correct / count if count else 0,
        "categories_correct": categories_correct,
        "key_field_accuracy": field_correct / field_total if field_total else 0,
        "fields_correct": field_correct, "fields_total": field_total,
        "fully_correct_messages": complete, "unexpected_files": unexpected,
        "processing_error_files": report.get("error_files", 0),
        "passed": bool(count) and complete == count and not unexpected and not report.get("error_files", 0),
        "details": details,
    }


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", type=Path, default=Path("expected.json"))
    parser.add_argument("--report", type=Path, default=Path("reports/latest.json"))
    parser.add_argument("--output", type=Path, default=Path("reports/evaluation.json"))
    args = parser.parse_args()
    try:
        expected = json.loads(args.expected.read_text(encoding="utf-8"))
        report = json.loads(args.report.read_text(encoding="utf-8"))
        result = evaluate(expected, report)
        write_json(args.output, result)
    except FileNotFoundError:
        if not args.report.exists():
            parser.exit(
                2,
                f"Evaluation failed: report not found: {args.report}\n"
                "Run `python -m triage` first, then run `python evaluate.py`.\n",
            )
        parser.exit(2, f"Evaluation failed: expected file or report is unreadable: {args.report}\n")
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Evaluation failed: {exc}\n")
    print(f"Categories: {result['categories_correct']}/{result['expected_messages']} ({result['category_accuracy']:.1%})")
    print(f"Key fields: {result['fields_correct']}/{result['fields_total']} ({result['key_field_accuracy']:.1%})")
    print(f"Fully correct: {result['fully_correct_messages']}/{result['expected_messages']}; processing errors: {result['processing_error_files']}")
    for detail in result["details"]:
        if not detail["passed"]:
            print(json.dumps(detail, ensure_ascii=False))
    print(f"Evaluation: {args.output}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
