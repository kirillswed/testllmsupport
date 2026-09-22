# Validation results — 22 September 2026

## Final live run

Model: **`z-ai/glm-5.2`**, the regular paid OpenRouter endpoint.
Run: 15:39:16–15:39:59 Asia/Tbilisi, about **43 seconds**.

| Check | Result |
|---|---|
| Input files / unique messages | 10 / 9 |
| Categories | **10/10, 100%** |
| Checked fields and conditions | **55/55, 100%** |
| Fully matched messages | **10/10** |
| Technical errors | **0** |
| LLM calls / successful | **8 / 8** |
| Input / output tokens | 5155 / 1446 |
| Cost from `usage.cost` | **$0.00382280904** |
| New notifications | 5 |
| Unique items in the manual queue | 6 |

Source report: [latest_run.json](latest_run.json).
Evaluation: [evaluation.json](evaluation.json).
Eight LLM calls: duplicate `07` was not sent again, and the explicit attack `10`
was isolated by the local filter. These are metrics of the whole service, not of
a pure LLM on ten independent examples. The manual queue includes two large
invoices, a critical bug, a feature request, an incomplete invoice, and an
attempt to override the instructions.

A repeat run on the same data: **0 new records, 0 LLM calls, 0 currency
requests, 0 repeat notifications, $0**; SQLite still holds 9 messages.
Result: [repeat_run.json](repeat_run.json),
repeat evaluation: [repeat_evaluation.json](repeat_evaluation.json).

## Currency check

The live ExchangeRate-API returned EUR 1240 = USD 1422.41 and
GEL 3200 = USD 1227.75. Rate timestamp: `2026-09-22T00:02:31+00:00`.
Snapshot: [fx_live_check.json](fx_live_check.json).
The full run used a still-valid cache from that live request, so
`fx_http_requests=0` in its report. These are not fixed test rates.

[Rates By Exchange Rate API](https://www.exchangerate-api.com).

## Code checks

**36 passed**, JUnit: [tests.xml](tests.xml). The snapshot was refreshed after
the adversarial tests were added; the earlier figure of 28 was the run before them.

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.pytest_tmp --junitxml=submission/tests.xml
```

Covered: duplicates and restart, the currency formula and rounding, cache
expiry, API failure and enrichment retry, notifications, validation, the free
GLM adapter, paid-model cost, and evaluation completeness. Untrusted input is
covered separately: fake roles, nested JSON, spoofed closing tags, spaces inside
a command, base64, and HTML comments. The message goes into a user-role JSON
envelope, tags are escaped, and business fields of a fake invoice are not stored.
HTTP in these tests is replaced with synthetic responses; these are not LLM results.
The API key was checked to be absent from the submitted sources and reports.

## Earlier live attempts

| Configuration | Categories | Fields | Processing errors | Cost |
|---|---|---|---|---|
| `openrouter/free`, original prompt | 7/10 | 38/55 | 3 | $0 |
| `z-ai/glm-5.2:free`, refined prompt | 5/10 | 33/55 | 6 | $0 |
| `z-ai/glm-5.2`, refined prompt | 10/10 | 55/55 | 0 | $0.00382280904 |

The first router used `liquid/lfm-2.5-2.6b:free`: extra fields, a false prompt
injection, a truncated response, and provider errors showed up. The free GLM
handled the first two messages correctly, then the endpoint returned 429.
Availability errors count as errors of the whole process, not as proof of poor
classification: the account's daily quota was still available.

Kept: [first report](initial_run.json), [first evaluation](initial_evaluation.json),
[free GLM](glm_free_run.json), [its evaluation](glm_free_evaluation.json).
This is an engineering check of configurations, **not a controlled model
benchmark**: the prompt was refined between the first and second runs, and
endpoint availability differed. The expected-results file was not changed.
100% on the ten given files does not mean the same accuracy on real mail.
