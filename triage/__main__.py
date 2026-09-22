import argparse
import sys
from dataclasses import replace
from pathlib import Path

from .config import ALLOWED_MODELS, Settings
from .pipeline import run


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Classify inbox messages using OpenRouter.")
    parser.add_argument("--inbox", type=Path, default=Path("inbox"))
    parser.add_argument("--db", type=Path, default=Path("data/triage.sqlite3"))
    parser.add_argument("--reports", type=Path, default=Path("reports"))
    parser.add_argument("--model", choices=ALLOWED_MODELS, help="Override OPENROUTER_MODEL for this run.")
    parser.add_argument("--retry-errors", action="store_true", help="Retry failed records; FX failures reuse extraction.")
    parser.add_argument("--refresh-cache", action="store_true",
                        help="Explicitly reprocess cached messages with the current prompt/model; may incur API costs.")
    args = parser.parse_args()
    try:
        settings = Settings.from_env()
        if args.model:
            settings = replace(settings, model=args.model)
        if not settings.api_key:
            print("OPENROUTER_API_KEY is missing. Set it in .env (see .env.example).", file=sys.stderr)
            return 2
        report = run(args.inbox, args.db, args.reports, settings, args.retry_errors,
                     refresh_cache=args.refresh_cache)
        return 1 if report["error_files"] else 0
    except (ValueError, OSError) as exc:
        print(f"Cannot run: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
