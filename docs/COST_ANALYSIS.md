# Cost Analysis — Taiwan Stock AI Alert System

A bottom-up monthly run-cost estimate. The headline: because the workload is tiny (one market snapshot per weekday) and 100% serverless/on-demand, **almost every service stays inside its perpetual free tier**, so real steady-state cost is **≈ US$1–2/month**, dominated by ECR image storage and S3. The README's "< US$10/month" badge is a deliberately conservative ceiling.

> Figures are engineering estimates from published on-demand pricing and the workload assumptions below — not a copy of an actual bill. `us-east-1` / `ap-northeast-1` list prices, 2026.

---

## Workload assumptions

| Parameter | Value |
|---|---|
| Trading days / month | ~21 (Mon–Fri) |
| Stocks per day (TWSE full market) | ~1,080 |
| `worker` SQS messages / month | ~1,080 × 21 ≈ **22,700** |
| DynamoDB writes / month | ~23,000 (OHLCV + summaries + yield) |
| Bedrock calls / month | ~21 (one grounded summary per trading day) |
| LINE bot interactive queries / month | low (personal bot), assume < 3,000 |
| Lambda architecture | ARM64 Graviton2, short-duration, small memory |

---

## Per-service breakdown

| Service | Driver | Free tier (perpetual unless noted) | Est. monthly |
|---|---|---|---|
| **Lambda** | ~150 invocations/day, all short | 1M requests + 400k GB-s/mo | **$0.00** (far under) |
| **SQS** | ~22.7k messages | 1M requests/mo | **$0.00** |
| **DynamoDB (on-demand)** | ~23k writes, modest reads, small storage | 25 GB storage | **~$0.03** |
| **S3** | Bronze JSON + Silver/Gold Parquet, small daily objects | — | **~$0.30–0.60** (grows slowly with history) |
| **Amazon Bedrock — Claude Haiku 4.5** | ~21 short calls (facts in, summary out) | none | **< $0.10** |
| **Athena** | 1 dbt build/day scanning MBs of Parquet | — ($5/TB scanned) | **< $0.05** |
| **Glue Data Catalog** | External tables via **partition projection** (no Crawler) | 1M objects stored | **$0.00** |
| **API Gateway (HTTP API)** | LINE webhook requests | 1M requests/mo (12-mo)† | **~$0.00** |
| **EventBridge Scheduler** | ~4 schedules × 21 = 84 invocations | 14M invocations/mo | **$0.00** |
| **CloudWatch** | ≤ 10 alarms, low log volume | 10 alarms + 5 GB logs | **~$0.00** |
| **SNS** | alarm + notify emails | 1,000 email notifications/mo | **$0.00** |
| **ECR** | Docker images for 6 Lambdas | 500 MB (12-mo)† | **~$0.10–0.30** |
| **SSM Parameter Store** | LINE tokens (Standard tier) | Standard params free | **$0.00** |
| **Secrets Manager** (if used instead of SSM) | per secret | none | $0.40 / secret |
| **GitHub Actions** | CI + daily dbt build | included minutes | **$0.00** |
| | | **Total** | **≈ $1–2 / month** |

† API Gateway and ECR free tiers are 12-month (new-account) rather than perpetual; after that both remain a few cents at this volume.

---

## Why it's this cheap — design choices that remove cost

- **No standing compute.** No EC2, no ECS/EKS, no NAT Gateway, no idle databases. Everything is triggered and billed per use. The single largest "gotcha" cost in serverless projects — a **NAT Gateway (~$32/mo)** — is avoided entirely by not putting Lambdas in a private subnet that needs one.
- **Glue partition projection instead of a Crawler.** Partitions are derived from S3 key patterns at query time, eliminating scheduled Crawler runs (which bill per DPU-hour).
- **Athena workgroup as a guardrail.** A per-query / per-workgroup bytes-scanned limit caps the one service that *could* run away if a bad query scanned the whole lake.
- **dbt on CI minutes, not compute.** The transform layer runs inside GitHub Actions on the daily schedule — no always-on dbt/orchestrator server.
- **ARM Graviton2 Lambdas.** ~20% cheaper per GB-second than x86 (moot here since we're under free tier, but it's the right default).
- **DynamoDB TTL** keeps the hot store small so it never leaves the free storage tier.

---

## Cost ceiling & what would move the needle

At current scale, cost is effectively **noise**. It only becomes material if the project scales up:

| Change | New dominant cost |
|---|---|
| Add per-minute intraday ingestion | Lambda + SQS leave free tier; DynamoDB writes grow |
| Years of history + full Bronze retention | S3 storage (mitigate with lifecycle → Glacier) |
| High-traffic public LINE bot | API Gateway + DynamoDB reads |
| Larger LLM / longer summaries / more calls | Bedrock tokens |

**Mitigations already in place or planned:** S3 lifecycle rules for Bronze, Athena scan caps, on-demand billing everywhere (no provisioned waste), and TTL on hot data.

---

*Interview soundbite: "It runs for the price of a coffee per year because there's nothing on — no NAT Gateway, no idle cluster, no Crawler. Every dollar of the architecture is tied to an actual event."*
