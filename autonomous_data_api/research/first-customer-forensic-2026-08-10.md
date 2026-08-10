# First Customer Forensic

Date: 10 August 2026  
Service: Official Source Evidence (`https://evidence.regulavita.com`)  
Scope: the first non-owner payment cluster only

## Founder Verdict

This is a genuine first external paying machine. It is not yet evidence of a
repeat customer or of organic compliance demand.

The most likely buyer was a funded autonomous catalog evaluator that discovered
the service through Coinbase Bazaar, read the declared endpoint schemas and
examples, and automatically bought callable resources in ascending price order.
That classification is high confidence. The wallet made 477 official-USDC
payments to 222 recipients over three active days, spending $8.132078 in total.
Our two $0.05 calls appeared inside a rapid run of many other $0.05 merchant
payments. The Form D request exactly matched our Bazaar example.

This still matters. A third party's machine discovered us without outreach,
authorized real USDC, completed two x402 settlements, and consumed valid output.
The full autonomous sale loop worked. What remains unproved is that the output
solved a real downstream job worth repeating.

## Confidence-Graded Classification

| Claim | Confidence | Evidence |
|---|---:|---|
| Funds came from a non-owner wallet | Very high | Different payer, two successful Base USDC settlements, internal payer HMAC matches both calls |
| Calls were automated | Very high | Two paid API calls two seconds apart; exact machine-readable Bazaar example; surrounding wallet activity is machine-timed |
| Coinbase Bazaar was the discovery surface | High | Exact Bazaar example body plus catalog-wide, price-ordered payments; Coinbase timestamps match our ledger |
| Buyer was evaluating/cataloging services | High | 477 outgoing USDC payments across 222 recipients and 90 calls at exactly $0.05 |
| Buyer had a specific Form D or OFAC business need | Low | Requests used default examples and were embedded in a broad sweep |
| Buyer will return | Unknown | No second-day non-owner use yet |
| x402-list itself made the purchase | Low | Its published monitoring method stops at the 402 handshake; it does not describe buying every service |

## Exact Purchase Session

Internal application ledger:

| Time (UTC) | Endpoint | Price | Result | Application latency |
|---|---|---:|---|---:|
| 03:46:05.567 | `/v1/gtm/form-d-funding-leads` | $0.05 | 10 valid Form D leads; more results available | 971 ms |
| 03:46:07.505 | `/v1/ofac/exact-identifier-evidence` | $0.05 | `NO_EXACT_MATCH` for the published zero-address example | 1,222 ms |

Base settlements:

- Form D: https://basescan.org/tx/0x14183ce44cd751a7dfd742a66d90b607db6922b4ae477000b960b4bd6283a729
- OFAC: https://basescan.org/tx/0xdb60a68cae6105cf4bebe19c0689e44f84fdab9fd48b31ec996c6bda74a571f6
- Payer: `0x72F6d77a78DbEc1B2fFf6F6DB4672dDdE1Bd04C4`
- Payee: `0x9500075649a70411c81f99c4314f6cff55d12579`

The Form D body was the exact example then published in Coinbase Bazaar,
including `since=2026-08-01T13:57:41Z`, empty filters, `limit=10`, and a
600-second source-age bound. Coinbase recorded `lastCalledAt` values within
milliseconds of our own timestamps.

## Wallet Behavior Around Us

The payer was not a single-purpose buyer:

- 477 outgoing transfers of official Base USDC
- 222 unique recipients
- $8.132078 total spent
- 90 payments at exactly $0.05
- Activity on 3, 4, and 10 August
- Price progression on 10 August from roughly $0.001 through $0.01, then
  $0.025-$0.04, and finally predominantly $0.05
- Our calls landed at 03:46:07 and 03:46:09 amid other $0.05 calls every few
  seconds
- Wallet retained approximately $56.08 USDC after the sweep

The sequence is much more consistent with an affordability-capped catalog
evaluation than a person or agent searching for two unrelated compliance
answers at the same moment.

## What Caused The Sale

1. **Machine discovery worked.** The routes had valid Bazaar metadata and had
   already been indexed by an owner bootstrap settlement.
2. **The examples were executable.** The buyer did not invent inputs; it used
   the declared body and got a valid result.
3. **The price was inside its scan band.** It bought both $0.05 routes but not
   our $0.10 route in this session. The wallet's run was visibly price ordered.
4. **There was no account or key ceremony.** Discovery, authorization,
   settlement, and fulfillment completed programmatically.
5. **Latency was acceptable.** Both responses completed in about one second.
6. **Descriptions matched searchable jobs.** As measured on 10 August, Form D
   ranked first for `SEC Form D funding sales trigger` in Coinbase search.

Important qualification: Coinbase currently reports two payers for each bought
route, but one is our disclosed owner bootstrap. Internally there is one
independent non-owner cluster, not two independent customers.

## Current Distribution Position

For `SEC Form D funding sales trigger`, Coinbase Bazaar currently ranks our
$0.05 Form D route first. The next close comparator is Stratalize at $0.10; a
$0.05 competitor shows 19 calls but only one payer, which is weak evidence of
broad demand.

For `OFAC wallet sanctions screening payment preflight`, the indexed premium
OFAC route currently ranks fourth at $0.05. Lower-priced competitors occupy much
of the top of the result set. Our newer $0.01 OFAC preflight and $0.01 SEC filing
signal are not yet indexed because Coinbase catalogs a resource only after a
successful settlement. Source Watch was added after this first purchase and is
also not indexed.

References:

- Coinbase Bazaar and ranking: https://docs.cdp.coinbase.com/x402/bazaar
- x402-list measurement method: https://x402-list.com/methodology
- Approved directory listing: https://x402-list.com/services/official-source-evidence
- Independent caution on manufactured x402 volume: https://arxiv.org/abs/2607.12575

## Replication Experiment

### Objective

Convert machine discovery into either repeat task-driven purchases or an upgrade
to a higher-value persistent job. Do not optimize for raw settlement count.

### Phase 1: Make the cheap doors discoverable

Make one explicitly owner-tagged $0.01 settlement on each of:

- `/v1/ofac/payment-preflight`
- `/v1/sec/filing-change-signal`

Maximum promotion cost: $0.02. These transactions are catalog-registration
events only and must remain excluded from revenue/demand metrics. Make no other
owner purchases during the observation window.

### Phase 2: Instrument intent

Record privacy-safe HMAC fingerprints for client, User-Agent, and optional agent
run identifier. Record broad User-Agent family, Fly edge/request metadata,
origin-only referrer, and optional self-declared `X-Agent-Discovery-Source`.
Never persist raw client IPs or full referrer URLs.

Intent classification rules:

- **Evaluator:** exact published example, several unrelated routes in minutes,
  no cursor progression, no return after 48 hours.
- **Task-driven trial:** non-example address/ticker/filter, cursor progression,
  or repeated calls to one product.
- **Retained buyer:** same payer or pseudonymous run/client cluster on at least
  two UTC dates.
- **Upgrade:** a cheap decision customer later buys premium evidence or Source
  Watch.

### Phase 3: Test a value ladder, not just lower prices

If Source Watch remains unindexed/unbought, add one $0.05 entry product: a
24-hour source-change watch with four checks and the same private polling API.
The output should explicitly point to the existing $1.00, 30-day product. This
tests whether catalog evaluators can become persistent-job customers without
reducing the full product's price.

### Outcomes and Decision Rule

Observe for seven full days after cheap-route indexing.

Primary success:

- at least one new non-owner payer cluster using a non-example input; or
- one non-owner cluster returning on a separate UTC day; or
- one independent purchase of a persistent monitor.

Secondary signal:

- another evaluator sweep buys the new $0.01 routes, proving repeatable Bazaar
  acquisition but not end-customer demand.

Failure:

- only owner/bootstrap transactions; or
- more example-only sweeps with no repeat or upgrade.

On failure, do not spend weeks polishing these endpoints. Use the discovery and
payment infrastructure to test a materially higher-value agent job.

## Immediate Founder Actions

1. Deploy the attribution migration and verify it with an unpaid attributed
   probe.
2. Authorize exactly $0.02 to index the two cheap decision routes.
3. Freeze further self-purchases for seven days.
4. Review every new settlement against request novelty, repeat behavior, and
   upgrade behavior, not merely revenue or transaction count.
5. Only build the $0.05 Source Watch trial if the cheap routes are discoverable
   and the $1 route remains outside observed buyer budgets.

## Bottom Line

We have proven autonomous distribution and autonomous payment fulfillment once.
We have not yet proven a customer problem, retention, or scalable economics.
The next dollar of founder attention should go toward distinguishing genuine
task intent and upgrades from catalog evaluation, while preserving the machine
discovery mechanics that demonstrably worked.
