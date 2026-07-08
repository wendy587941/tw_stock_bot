# Runbook — Taiwan Stock AI Alert System

Operational guide for on-call: what the system does on a normal day, what alarms fire, and how to recover. Written to be usable by someone who did not build the system.

---

## 1. Normal day at a glance

All times Taipei (UTC+8), Mon–Fri only. Each stage guards against non-trading days, so weekends/holidays are silent by design.

| Time | Function | Trigger | Healthy signal |
|---|---|---|---|
| 15:30 | `dispatcher` | EventBridge cron | Logs a stock count > 0; SQS `ingest` queue fills |
| 15:30+ | `worker` | SQS batch | DLQ stays empty; Bronze/Silver objects land in S3; DynamoDB item count rises |
| 16:00 | `analyzer` | EventBridge cron | `SUMMARY#<date>/DAILY` item written to DynamoDB; Gold marts written to S3 |
| 16:30 | `notifier` | EventBridge cron | Returns `{pushed: true}`; message appears in LINE |
| 17:00 | `dividend_ingest` | EventBridge cron | `YIELD#<date>/RANKING` item written |
| 17:30 | dbt `dbt build` | GitHub Actions | Workflow green; marts refreshed in Athena |

**Fast health check:** did a LINE message arrive by ~16:35? If yes, the hot path (15:30→16:30) is healthy end to end.

---

## 2. Alarms and what they mean

Two CloudWatch alarm families publish to an SNS topic (email). Alarms use `treat_missing_data = notBreaching`, so "no data" never pages.

### 2.1 `<function>-errors` — Lambda Errors ≥ 1
One of `dispatcher / worker / analyzer / notifier` threw an uncaught exception (for `worker`, this includes reported partial-batch failures).

**Triage**
1. Open CloudWatch Logs for the named function, newest stream.
2. Read the last `ERROR`/traceback. Common causes below.

| Symptom in logs | Likely cause | Action |
|---|---|---|
| `dispatcher` logs "skip / empty list" | Source API returned nothing, or non-trading day | Usually benign. If it *was* a trading day, re-run §3.1 |
| `worker` per-message errors | One stock's payload malformed / source hiccup | Failing messages retry, then land in DLQ → see §2.2 |
| `analyzer` `bedrock_failed_fallback` | Bedrock throttle / access / region issue | Non-fatal: a deterministic summary is still written. See §4 |
| `notifier` LINE 4xx/5xx | Bad/expired channel token, or LINE outage | Check SSM token (§5); retry §3.1 |

### 2.2 `<...>-dlq` — SQS DLQ has visible messages
A stock message failed `worker` processing 5× and dropped to the DLQ. Something is *reproducibly* failing for those stocks.

**Triage & recovery**
1. Inspect DLQ messages (console → SQS → DLQ → Poll for messages) to see which stock codes and why.
2. Fix the root cause (source-format change, code bug, etc.).
3. **Redrive:** SQS console → DLQ → *Start DMS/redrive* back to the source `ingest` queue, or re-run the day via §3.1.
4. Confirm DLQ depth returns to 0.

---

## 3. Common operations

### 3.1 Re-run / backfill a day
Every scheduled Lambda accepts a manual override event:

```jsonc
// bypass the trading-day guard (e.g. testing on a weekend)
{ "force": true }

// process a specific historical date
{ "trade_date": "2026-07-08" }

// both together
{ "force": true, "trade_date": "2026-07-08" }
```

Invoke via console (**Test**) or CLI:
```bash
aws lambda invoke --function-name tw-stock-bot-dispatcher-dev \
  --payload '{"trade_date":"2026-07-08"}' --cli-binary-format raw-in-base64-out out.json
```

**Order matters** — to rebuild a full day: `dispatcher` → (wait for `worker` to drain SQS) → `analyzer` → `notifier`. `dividend_ingest` is independent and can be run anytime.

### 3.2 Deploy new code
Push to the repo; GitHub Actions (`deploy-images.yml`) builds the ARM64 image, pushes to ECR, and updates the Lambda. Infra changes go through `terraform.yml`. No manual AWS credentials involved (OIDC).

### 3.3 Refresh BI marts manually
```bash
cd dbt && PYTHONUTF8=1 dbt build   # Windows: PYTHONUTF8=1 is required
```

---

## 4. Bedrock degradation
If Bedrock is throttled or access is revoked, `analyzer` catches the failure, logs `bedrock_failed_fallback`, and writes a **deterministic template summary** (`model_id = "fallback-deterministic"`). The pipeline stays green and LINE still gets a factual message — only the prose polish is lost.

**When to act:** if you see the fallback for several consecutive days, check Bedrock **model access** for `jp.anthropic.claude-haiku-4-5-*` in the Tokyo (`ap-northeast-1`) console and any account-level throttling. No emergency; the system is self-protecting.

---

## 5. Secrets & configuration
- **LINE channel access token & secret** live in **SSM Parameter Store** (read at runtime by `notifier` / `webhook`). Never in env vars or code.
- To rotate: update the SSM parameter; no redeploy needed (read on each invocation).
- `notifier` broadcasts to all followers by default; set an SSM `push_target` (a `U`/`C`/`R` id) to push to a specific recipient instead.

---

## 6. Escalation / known-benign list
- **Weekend/holiday silence** — expected. The `MARKET_HOLIDAYS` list + trading-day guard suppress runs.
- **ETF "dividend not announced yet"** — expected; `webhook` answers honestly rather than guessing.
- **Empty dispatcher on a real trading day** — the TWSE `STOCK_DAY_ALL` endpoint may lag the close; re-run §3.1 a bit later.

---

## 7. Incident log

### 2026-07-08 — dbt build fails: `work_group` kwarg (dependency drift)
**Symptom.** The scheduled dbt workflow went red for the first time (green the whole prior week). Every staging model (`stg_ohlcv` / `stg_signals` / `stg_yield`) died with:
```
Runtime Error in model stg_ohlcv (models/staging/stg_ohlcv.sql)
  BaseCursor._execute() got an unexpected keyword argument 'work_group'
```
All downstream models/tests were then SKIPped → `PASS=0 ERROR=3 SKIP=20`.

**Root cause.** Not data, not our code (last commit was docs-only). The CI install line was unpinned, so each run resolved the newest PyPI versions. `pyathena` published **3.35.0** between the 07-07 and 07-08 runs, which removed the `work_group` argument that `dbt-athena==1.10.2` still passes to the cursor.

| Run | pyathena | Result |
|---|---|---|
| 07-07 | 3.34.0 | ✅ |
| 07-08 | 3.35.0 | ❌ |

**Fix.** Pin the transitive dependency in `.github/workflows/dbt.yml`: `pyathena>=3.34,<3.35`. Re-run the workflow (**Actions → dbt → Run workflow**) to confirm green.

**Lesson / prevention.** A build with unpinned transitive deps is not reproducible — an upstream release can break a pipeline whose own code never changed. When `dbt-athena` ships a release compatible with pyathena 3.35, bump both together and widen the pin.

---

*Keep this file honest: when you hit an incident not covered here, add the symptom + fix.*
