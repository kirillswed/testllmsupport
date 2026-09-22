# Inbound message triage

The prototype reads `inbox/*.txt`, classifies messages through **OpenRouter**,
extracts fields, validates them, converts invoices to USD,
stores the result in SQLite, and prints notifications to the console.

The verified configuration is **`z-ai/glm-5.2`**: 10/10 categories, 55/55 checked
fields, full-run cost **$0.00382280904**, about 43 seconds.
That result is on the given small sample, not a promise of 100% on new mail.
Details and source reports: [submission/VALIDATION.md](submission/VALIDATION.md).

## Running

Python **3.11+**. Run commands from the project root.

```Powershell
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

If PowerShell blocks local scripts, run `Set-ExecutionPolicy -Scope Process Bypass`
once for the current window, then activate again. After activation the prompt
shows the `(.venv)` prefix.

Set `OPENROUTER_API_KEY` in `.env`. The file is excluded from Git. If `.env`
already exists, do not copy the template over it. No other keys are required.
The template selects the **paid** GLM 5.2; for a free run set
`OPENROUTER_MODEL=openrouter/free` or `z-ai/glm-5.2:free` explicitly. The free
options had quality or availability errors in validation; those runs are kept
in the comparison reports.

```powershell
python -m triage
python evaluate.py
```

`evaluate.py` compares `expected.json` with the latest run report. Run it after
`python -m triage`. The `reports/` directory and its JSON files are generated
locally and are intentionally not committed to Git.

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
cp .env.example .env
python3 -m triage
python3 evaluate.py
```

To check a repeat run, execute `python -m triage` again: the database should
still hold **9 unique messages** from 10 files, with no new LLM calls and no
repeated notifications.

After a transient service error:

```powershell
python -m triage --retry-errors
```

This flag retries only records with status `error`. If only the currency
conversion failed, the extraction already obtained is reused without an LLM
call. `needs_review` is not reprocessed automatically: missing payment details
cannot be fixed by asking the model again. Changing the source file creates a
new message under a new hash.

CLI parameters: `--inbox`, `--db`, `--reports`, `--model`, `--retry-errors`.
Supported models are `openrouter/free`, `z-ai/glm-5.2:free`, and `z-ai/glm-5.2`.
The last model is **paid** and was enabled at the author's request after the
free options were checked. Change the model with `OPENROUTER_MODEL` or `--model`.
Use a separate database when comparing models: a normal repeat run reuses
previously stored results even if the model setting changed.
A fresh independent run, without deleting the existing database:

```powershell
python -m triage --db data/fresh.sqlite3 --reports reports/fresh
python evaluate.py --report reports/fresh/latest.json --output reports/fresh/evaluation.json
```

Service exit codes: `0` — processing finished (a manual queue is allowed),
`1` — technical errors on individual files, `2` — configuration or startup error.
Quality evaluation returns `0` only when every expectation matches and there
are no processing errors; mismatches return `1`.

## Results and layout

- `reports/latest.json` — the full latest report, per-file results, the manual
  attention list, tokens, cost, and the models actually selected.
- `reports/<run_id>.json` — immutable per-run reports, identified by filename.
- `reports/evaluation.json` — category and key-field scores, with mismatches.
- `data/triage.sqlite3` — messages, runs, run files, LLM call attempts, and
  sent notifications. Both the source text and the extracted data are stored.
- `data/fx_cache.json` — local rate cache until the API update time.
- `submission/` — checked results for handoff; the check status is described in
  [submission/VALIDATION.md](submission/VALIDATION.md).
- `expected.json` — expectations for all 10 files, written before the LLM run.
- `triage/` — data models, rules, API clients, SQLite, processing, and the CLI.
- `tests/` — tests with synthetic HTTP responses, without a key or the network.

## Schema and decisions

Shared fields: `category`, `summary`, `fields`, `security_flags`. `summary` holds
the support or bug problem, or explains the classification. A shared set of
nullable fields in `fields` makes structured output easier across different free
models; which fields are allowed or required depends on the category.

| Category | Data | Minimum for automatic processing |
|---|---|---|
| `support` | Contact, product; the problem is in `summary` | Non-empty description; contact may be absent |
| `finance_invoice` | Vendor, amount, currency, number, dates, payment terms | Vendor, positive amount, currency, number |
| `sales_lead` | Company, contact, state, interest | Company, interest, email or an unmasked phone |
| `bug_report` | Product, version, browser, severity, feature; description in `summary` | Description and severity |
| `spam` | Reason in `summary`, attack signals | Business fields empty |
| `unknown` | Reason in `summary` | Always manual review |

Category is not status. `completed` means extraction and enrichment succeeded,
`needs_review` means insufficient data or ambiguous or dangerous text,
`error` means a technical failure. A critical bug can be `completed` and still
enter the manual queue and notifications: extraction succeeded, but the problem
is urgent. An incomplete invoice stays `finance_invoice`; a currency API error
does not change the category either. A total classification failure uses a
technical `unknown` with `error_stage=llm`, rather than claiming the message
is genuinely ambiguous.

Pydantic rejects unknown fields and categories, checks dates and email format,
and checks that amounts are positive and finite (the prototype upper bound is
10^15). Amounts are `Decimal`; the USD total is rounded to cents with
`ROUND_HALF_UP`. `null` means "not stated"; the model must not invent payment
details. Currency is a three-letter uppercase code, and support is checked
against the API table.

Deduplication is SHA-256 of the text after normalizing BOM, newlines, and
surrounding whitespace. A unique database key prevents a second insert.
Separate `run_items` record that both files arrived. This deduplicates identical
content, not different emails that share one invoice number. The prototype is
built for one process at a time.

The LLM receives system rules separately from the untrusted message text, with
`require_parameters=true` and temperature 0. `openrouter/free` and the paid GLM
use `response_format=json_schema`. The free GLM does not support that parameter:
the same schema is passed in the system prompt. Optional reasoning is disabled
for both GLM variants because the task is short field extraction. Local
validation is required even with structured outputs. Bounded retries apply to
408/429/5xx, network errors, and invalid output. `Retry-After` is honored; a
wait longer than 30 seconds is deferred until the next run. An authorization
error blocks further LLM calls for the current run. Invalid JSON after all
attempts is stored as a technical error; the raw model response is not executed
and is not copied into business fields.

### Untrusted input boundary

The message is not concatenated with the instructions as a plain string. The
client builds a separate user-role envelope:

```text
<untrusted_message>
{"source":"external_inbox","trust":"untrusted","content":"..."}
</untrusted_message>
```

The JSON content escapes `<`, `>`, and `&`, so the message text cannot close
the outer tag, insert a new role, or replace the JSON structure. This is a data
boundary for the model, not a cryptographic guarantee: the result still goes
through local Pydantic validation.

The system prompt separately lists fake role tokens, nested JSON, HTML comments,
code blocks, false closing tags, and encoded payloads as untrusted data. It
forbids following links, executing code, revealing the prompt, and approving
payments. An ordinary business request to "pay the invoice" remains an invoice
fact; an attack discussed inside a bug report is not treated as an attack unless
it tries to change the classifier's behavior.

The keyword filter in `triage/security.py` is a fast quarantine for a few obvious
phrases, not the main defense. A bypass of the filter still hits this boundary.
If the model sets `prompt_injection` together with business fields, the pipeline
clears the fields and turns the result into `spam` / `needs_review`; currency
enrichment does not run. Adversarial tests cover fake roles, nested JSON,
boundary spoofing, whitespace, base64, and HTML comments.

The model profile is separate from the prompt builder: `triage/llm_profiles.py`
describes native JSON schema support, reasoning, and the price ceiling. The run
contract stores the prompt version, system-prompt hash, schema hash, model, and
fingerprint. An old result is not silently replaced by a new model. For an
intentional reclassification use `--refresh-cache` — that can create new paid
calls.

`openrouter/free` picks the actual free model, so even at temperature 0 the
results of clean runs can differ. Its name and generation ID are stored. There
is no automatic switch from a free model to a paid one: the paid GLM is selected
explicitly. Its provider price ceiling is $1 / 1M input tokens and $3 / 1M
output tokens; that is a price ceiling, not a run budget. Cost comes from
`usage.cost`. When a free model omits the value, 0 is used at the published
rate with an explicit source label; for a paid model the value is `null`, and
the known part of the sum and the number of calls without a cost are shown
separately. Unknown spend is not reported as zero. Failed attempts are counted;
missing token data is marked separately and is not reported as a measured zero.

## Currencies and notifications

A single `GET https://open.er-api.com/v6/latest/USD` returns EUR, GEL, and other
rates relative to USD. Formula: `amount_usd = amount / rates[currency]`.
The latest rate available at processing time is used, **not the historical rate
on the invoice date**. The source, rate time, units of currency per USD, and
the total are stored. Stored results are not recomputed on a normal repeat run.
A stale cache is not treated as fresh. If the API is unavailable, the invoice
stays in the database with an enrichment error and enters the manual queue.
USD does not need an external request.

**[Rates By Exchange Rate API](https://www.exchangerate-api.com)** — source
attribution is also present in the console and in every report.

Notifications are printed to the console with an owner:

- `engineering` — critical bug: a department's work is blocked;
- `finance` — amount **strictly greater than** `INVOICE_ALERT_USD` (default 1000 USD);
- `operations` — any `unknown`;
- `security` — a detected attempt to override the instructions.

Notifications are deduplicated by message hash and event type. The console and
SQLite are not one transaction: a crash after printing and before the mark is
written can produce a repeat notification. That is an accepted prototype limit.

## Difficult inputs

- `02` / `07`: identical messages — one result, two input files. The issue date
  is absent; the payment due term does not replace it. The test has no
  attachments, so bank details are not extracted.
- `03`: the phone is masked and stored as given. The usable contact is the email.
- `04`: "the department's work has stopped" matches the `critical` policy.
  The product name is unknown: Excel is the export target, not the product.
- `06`: lari = GEL, spaces in the amount are normalized. `15.09.2026` is the
  invoice date. "Payment within 10 days" is kept as text; an exact date is not
  computed without a confirmed start of the count and calendar or business-day
  rules.
- `08`: a feature request is `unknown` because the assignment has no such
  category. That policy was chosen in advance; it is not a classifier error.
- `09`: an explicit invoice without payment details — `finance_invoice` +
  `needs_review`.
- `10`: explicit prompt injection. A small local filter sends these messages to
  `spam` + quarantine **before the LLM call**; the count of those decisions is
  visible as `classification_source=security_guard`. Fields of the fake invoice
  are not stored as payment details. A second guard clears business fields if
  the model itself noticed the attack. The filter is a heuristic, can be wrong,
  and does not promise to catch every rephrased attack. The LLM has no tools,
  no code execution, and no payment-approval mechanism — even a valid invoice
  is not paid automatically.

## Quality evaluation and checks

`evaluate.py` **does not call the LLM** and is not used by the service for
classification. It compares an independent `expected.json` with the run result:

1. Category accuracy — across all 10 input files, including the duplicate.
2. Key-field accuracy — numeric comparison of amounts, exact comparison of
   dates and null, strings compared without case or extra spaces. For free-text
   interest, the check looks for "1C" and "API"; for payment terms, for "10".
3. The number of fully matched messages, and the absence of technical errors.

Missing files and fields count as errors, even when `null` was expected.
Moving exchange rates are not baked into the fixture: the formula and rounding
are checked by separate tests. Ten examples are a smoke test, not a
statistically reliable quality estimate. The fixture is not edited to hide the
errors of the next model.

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Tests use their own synthetic messages and HTTP responses, not the ready-made
expectations in `expected.json`. They check the repeat run, notifications,
currencies, network and authorization failures, validation, security, and
evaluation completeness. They check the code; they do not replace a real LLM run.

## What to change for production

The prototype is one process, local SQLite, and the console. Production needs
a separate execution environment, not only a longer pipeline.

Environment: an isolated perimeter with least privilege, secrets outside the
image and outside the repository, egress only to mail, the LLM, and the rate
source. Attachment parsing runs in a sandbox, with no execution of embedded
code and no access to the host. The model still has no tools and no authority
to approve payments.

Load: a queue with several workers, locks, and idempotency on the invoice
business key (vendor + number), a concurrency limit toward the LLM and the
currency API, and backpressure when mail spikes. One failed worker must not
lose a message or process it twice.

Errors: retry only transient failures, a dead-letter path for messages that
cannot be processed, and separate signals for extraction failure, rate failure,
notification delivery failure, and prompt injection. A technical error on one
message does not stop the perimeter; `needs_review` stays a manual queue with
correction and an audit trail, not an automatic retry.

Uptime: health and readiness, process supervision, an SLO on latency and on the
share of technical errors, and an alert when mail, the LLM, or the rate source
is unavailable. Observability is metrics, logs, and traces without personal
data or message text in the clear.

The application layer on top of that: fetching mail and attachments, a
notification outbox, protected storage of secrets and personal data, and a
retention and deletion policy. Pin the model and the prompt version, expand
the labeled sample, and measure extraction errors and prompt injection
separately. For financial accounting, agree the rate source and the rate date.
Sending real customer data to an external LLM has to be agreed with company
requirements.

## Time and AI tools

The planned budget is **4 hours**. Actual work is **3 hours 30 minutes**,
broken down in [WORKLOG.md](WORKLOG.md):

- **30 minutes** — architecture: categories and the field schema, the untrusted
  input boundary, deduplication, currency conversion, and notification rules.
- **1 hour** — implementation: API clients, SQLite, the pipeline, the CLI, the
  fixture, and documentation.
- **2 hours** — testing: automated tests, adversarial cases, a live OpenRouter
  and GLM 5.2 run, quality evaluation, and the repeat run.

**OpenAI Codex** was used for design, code generation and fixes, tests,
documentation, and checking API documentation. The application uses
**OpenRouter** (`openrouter/free`, GLM 5.2 free / GLM 5.2); the models actually
selected are recorded in the report. Expectations and ambiguous cases were
written down before the real run.

Documentation: [OpenRouter free router](https://openrouter.ai/openrouter/free),
[structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs),
[usage accounting](https://openrouter.ai/docs/cookbook/administration/usage-accounting),
[ExchangeRate-API](https://www.exchangerate-api.com/docs/free).
