# Work report

Local time: Asia/Tbilisi (UTC+4), 22 September 2026.

Planned budget: **4 hours**. Time spent: **3 hours 30 minutes**.

- **30 minutes — architecture.** Categories and the shared field schema, the
  untrusted-input boundary, deduplication by text hash, conversion to USD, and
  notification rules. Expectations and ambiguous cases were fixed before the
  real run.
- **1 hour — implementation.** Environment, OpenRouter and ExchangeRate-API
  clients, SQLite, the processing pipeline, the CLI, `expected.json`, and
  documentation.
- **2 hours — testing.** Automated tests with synthetic HTTP responses,
  adversarial cases (fake roles, nested JSON, spoofed closing tags, whitespace,
  base64, HTML comments), a live run of the free router and the paid GLM 5.2,
  quality evaluation, the repeat run, and a check that the duplicate creates no
  new LLM calls. After the checks, **36 tests** pass.

Total LLM cost of the runs: **$0.00382280904**.

**OpenAI Codex** was used for design, code generation and fixes, tests,
documentation, and checking API documentation. The application uses
**OpenRouter** (`openrouter/free`, GLM 5.2 free / GLM 5.2); the models actually
selected are recorded in the report.
