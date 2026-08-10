# First Independent Buyer: Portfolio and Expected-Value Analysis

Date: 2026-08-10  
Service: Official Source Evidence  
Production: https://evidence.regulavita.com  
Buyer: `0x72F6d77a78DbEc1B2fFf6F6DB4672dDdE1Bd04C4`

## Executive verdict

The $0.10 is real independent revenue: the external wallet made two settled $0.05 USDC purchases from Official Source Evidence. It was not our wallet, an unpaid request, or a synthetic ledger entry.

However, the surrounding evidence shows that the wallet was conducting a broad, rapid, low-price sweep of Coinbase Bazaar resources. It made 477 USDC payments to 222 recipients, spending $8.132078 in total. Our two products were bought seconds apart during that run. This strongly validates discoverability, payment settlement, and machine usability. It does **not yet validate repeat demand for our specific evidence products**.

Building a separate hosted API to copy categories sampled by this wallet has negative expected value. The likely revenue from this buyer is one purchase per newly indexed route, usually $0.01-$0.05, while a second Fly application would cost about $6/month before build time and upstream data costs. The rational next product experiment, if undertaken, is one narrowly scoped route inside the existing application that reuses existing source-fetching, hashing, caching, and payment infrastructure.

## The two sales

| Time (UTC) | Amount | Settlement |
|---|---:|---|
| 2026-08-10 03:46:07 | $0.05 | [Base transaction](https://basescan.org/tx/0x14183ce44cd751a7dfd742a66d90b607db6922b4ae477000b960b4bd6283a729) |
| 2026-08-10 03:46:09 | $0.05 | [Base transaction](https://basescan.org/tx/0xdb60a68cae6105cf4bebe19c0689e44f84fdab9fd48b31ec996c6bda74a571f6) |

Both payments went to our production payee, `0x9500075649a70411c81f99c4314f6cff55d12579`, using Base USDC `transferWithAuthorization` settlement.

## What the buyer did

- 477 outgoing Base USDC payments
- 222 distinct recipient wallets
- $8.132078 total spend
- 459 of the 477 payments occurred on 2026-08-10 in an 84-minute run
- 432 purchases mapped to a likely current Bazaar route
- 429 distinct likely routes
- 39 ambiguous mappings and 6 unmatched payments
- Most prices were tiny: 116 at $0.001, 51 at $0.002, 59 at $0.005, 47 at $0.01, 41 at $0.03, and 90 at $0.05
- Only one clear high-price outlier: a $0.25 image-generation request
- No exact route was observed being purchased on multiple UTC days

The strongest repeated provider pattern was AgentReader: 21 calls covering 19 different extraction/reader routes across three days. The wallet bought different routes rather than repeatedly consuming the same route. This looks like inventory discovery or evaluation, not an agent repeatedly solving an operational problem.

Our two settlements landed two seconds apart and were interleaved with $0.05 payments to several unrelated providers. They were purchases 463 and 466 in chronological order across the wallet's observed 477-payment history, near the end of the large run. This is unusually strong contextual evidence for a catalog sweep.

The detailed mapping is in
[`first-buyer-purchase-map-2026-08-10.csv`](first-buyer-purchase-map-2026-08-10.csv).
Route assignment uses recipient, price, and call-time proximity; it is
probabilistic where multiple routes share a payee and price.

## Apparent preferences

Relative to the current sub-$0.05 Bazaar catalog, the buyer over-sampled these tags:

| Tag | Bought routes | Eligible catalog routes | Relative enrichment |
|---|---:|---:|---:|
| LLM tools | 21 | 21 | 26.44x |
| Content extraction | 20 | 29 | 18.23x |
| Web scraping | 22 | 34 | 17.11x |
| PDF | 22 | 47 | 12.37x |
| Markdown | 24 | 53 | 11.97x |
| Equities | 8 | 23 | 9.20x |
| Risk | 15 | 50 | 7.93x |
| Company data | 4 | 18 | 5.87x |
| Weather | 10 | 53 | 4.99x |
| OFAC | 4 | 29 | 3.65x |

This table must not be read as ten independent demand signals. The top four categories are heavily driven by one provider's family of AgentReader routes. The more defensible preference is: **newly discoverable, machine-callable, cheap endpoints with simple outputs**.

## Market benchmark

The current Bazaar catalog contains roughly 14,400 resources. Multiplying current listed price by reported last-30-day calls produces a rough gross-activity proxy, not verified seller revenue. Counts may include owner tests, evaluators, repeated probes, and coordinated activity.

| Endpoint/service | Price | 30-day calls | Payers | Gross proxy |
|---|---:|---:|---:|---:|
| x402.twit.sh tweet search | $0.006 | 93,753 | 42 | $562.52 |
| Tavily search | $0.01 | 46,232 | 422 | $462.32 |
| StableEnrich PDL people enrichment | $0.28 | 1,221 | 84 | $341.88 |
| StableEnrich FullEnrich people search | $0.15 | 1,151 | 85 | $172.65 |
| CheapTokens inference | $1.00 | 152 | 19 | $152.00 |
| Apify prepaid tokens | $1.00 | 147 | 30 | $147.00 |
| StableEnrich Exa search | $0.01 | 11,940 | 270 | $119.40 |
| StableTravel seats | $0.02 | 4,319 | 6 | $86.38 |
| Google Trends query | $0.05 | 594 | 7 | $29.70 |
| StableEnrich Firecrawl search | $0.0252 | 1,089 | 78 | $27.44 |

The economically strongest pattern is not arbitrary micro-APIs. It is agent-friendly access to capabilities that already have demand: search, enrichment, social data, scraping, inference, and prepaid access to established providers. Most of these businesses have an incumbent data advantage, upstream credentials, or redistribution risk that we cannot cheaply copy.

Coinbase documents that Bazaar ranking considers buyer reach, transaction volume, recency, and metadata, and indexes resources following successful settlement: https://docs.cdp.coinbase.com/x402/bazaar

## Expected value

| Strategy | Fixed monthly delta | Immediate likely revenue | Break-even problem | Verdict |
|---|---:|---:|---|---|
| New standalone Fly API | About $6.07 | One $0.01-$0.05 catalog-sweep purchase per route | 122 calls/month at $0.05 or 607 at $0.01, before development and variable costs | Do not build |
| New deterministic route in existing app | Near $0 hosting delta | Possibly one $0.01-$0.05 sweep purchase | A two-hour build valued at even $50/hour needs 2,000 purchases at $0.05 | Only with strong reuse and broader demand thesis |
| Resell an upstream API | Low fixed hosting, material variable cost | Market activity can reach tens to hundreds of dollars gross/month | Search margins are thin; licenses and redistribution terms can invalidate the model | Research only, no launch yet |
| Copy AgentReader route family | Low-to-moderate | Likely catalog-evaluator revenue | Crowded, provider-specific signal, no repeat route use | Reject |

Fly's published pricing supports the approximately $6/month baseline for the current small shared instance: https://fly.io/docs/about/pricing/

## Best adjacent hypothesis

If we add one route, the strongest low-cost candidate is a **one-shot signed public-source evidence snapshot**:

- Input: a public HTTP(S) page and optional extraction instructions
- Output: normalized extracted text, source URL, retrieval time, content hash, relevant quoted evidence, and a compact machine-readable change/evidence record
- Price test: $0.03-$0.05
- Reuse: existing Source Watch fetching, normalization, hashing, caching, abuse controls, payment middleware, deployment, domain, and monitoring
- Marginal infrastructure cost: near zero at current traffic
- Differentiation: auditable official-source evidence rather than another generic reader
- Stop condition: cap implementation at 90 minutes; do not add a paid upstream provider; do not create another Fly application

This is still not justified by the mass buyer alone. It is the only adjacent experiment with a plausible positive expected value because it can serve compliance, research, due-diligence, and monitoring agents beyond this wallet while reusing assets already paid for.

## Scientific next step

1. Treat the $0.10 as **external acquisition validation**, not product-market fit.
2. Leave the newly indexed $0.01 OFAC preflight and SEC filing-signal routes unchanged for seven days.
3. Separate owner traffic, the identified mass buyer, other evaluator wallets, and genuinely independent repeat buyers.
4. Require at least one of these before expanding the product family: an unrelated buyer, a repeat purchase on a later day, or a buyer using a non-example input that indicates a real task.
5. Do not create a second hosted API. Build at most one adjacent route inside the current service only if it meets the 90-minute/no-upstream-cost constraint.

## Bottom line

We have proved that an autonomous external system can discover our API, pay it, and receive results without human sales contact. We have not yet proved that it values our outputs enough to return. The buyer's portfolio is useful mainly because it reveals the acquisition mechanism: cheap new Bazaar listings are sampled. The route to meaningful revenue is to turn that one-time sampling into repeat utility, not to manufacture more endpoints for a five-cent first purchase.
