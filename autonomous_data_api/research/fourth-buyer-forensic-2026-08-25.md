# Fourth Buyer Forensic

Date: 25 August 2026
Service: `https://evidence.regulavita.com`
Scope: the fourth non-owner payer cluster and the two owner bootstrap calls it
motivated

## Founder Verdict

The fourth payer is a real external machine buyer, but its two calls are catalog
evaluation rather than evidence of task-driven demand. It paid real USDC, used
the published request examples, and bought 49 resources from 43 payees in four
bursts over less than four hours. Every call cost at most `$0.05`.

The useful new signal is selection, not transaction count. The buyer purchased
both of this service's wallet-safety routes while skipping equally cheap SEC and
web-extraction routes. Its surrounding basket included wallet risk, allowance
exposure, endpoint trust, settlement proof, WHOIS, vendor due diligence, and
agent verification. A similar safety/compliance basket appears in the first
buyer's much larger catalog sweep. This supports improving discovery for the
existing pre-payment safety products. It does not yet justify building another
API.

## Our Purchases

| Time (UTC) | Route | Price | Result |
| --- | --- | ---: | --- |
| 02:28:15.864 | `/v1/ofac/payment-preflight` | `$0.01` | `NO_EXACT_OFAC_MATCH_FOUND` for the published zero-address example |
| 03:09:36.127 | `/v1/ofac/exact-identifier-evidence` | `$0.05` | Signed exact-match evidence for the same published example |

Both calls came from `0xC7a277C1961E5Ca07F1319A5369CB4Ef9c98263e`,
shared one privacy-safe payer cluster, arrived through the San Jose Fly edge,
and had no declared referrer or discovery header. The request hashes exactly
match the public Bazaar examples. Application latency was 1.22 and 1.20
seconds.

Settlements:

- Preflight: https://base.blockscout.com/tx/0x92ee98202ef4a1cfea4edc3974be6de02d798f338510e4938a17d2dd7e3077f0
- Evidence: https://base.blockscout.com/tx/0x41cc7d6acf49f449b821dbb105645759979dcb4c8a22bee0c28065b96b5f882a

The calls added `$0.06` of external cash revenue and had zero direct supplier
cost. Cumulative non-owner cash is now `$0.20` from eight fulfilled calls and
four payer clusters. All eight remain published-example catalog samples;
validated product-demand calls and cross-day repeat buyers remain zero.

## Buyer Shopping Pattern

On-chain USDC transfers from the buyer between 01:02:59 and 04:54:35 UTC show:

- 49 paid calls to 43 unique recipients;
- `$0.764301` total spend;
- four bursts of 11, 18, 12, and 8 calls separated by 43-54 minute gaps;
- a hard observed ceiling of `$0.05` per call;
- 20 calls at `$0.01` and six at `$0.05`;
- repeated purchases from a few sellers within the same run;
- 14 of 43 recipient wallets also appeared in the first buyer's sweep.

Price distribution:

| Price | Calls |
| ---: | ---: |
| `$0.001` | 7 |
| `$0.002` | 2 |
| `$0.003301` | 1 |
| `$0.005` | 4 |
| `$0.01` | 20 |
| `$0.02` | 5 |
| `$0.03` | 3 |
| `$0.04` | 1 |
| `$0.05` | 6 |

The purchase sequence is inconsistent with one coherent downstream task. It
contains market signals, prediction data, web search, token metadata, random
choice, dream interpretation, developer tools, and liveness tests. It is best
classified as a funded catalog evaluator or benchmark runner.

## What It Selected

The buyer did not simply buy every route from our merchant address. Of the seven
routes then visible in Coinbase Bazaar, it bought only:

1. the `$0.01` pre-payment OFAC decision;
2. the `$0.05` signed OFAC evidence upgrade.

It skipped the `$0.01` SEC filing signal and `$0.03` source snapshot, so price
alone does not explain our two sales. Nearby purchases included Agent Mercantile
allowance exposure, WalletTriage, Aura settlement evidence, website due
diligence, WHOIS, Trust402, A2A verification, and agent scoring. The preflight
response also advertises the signed evidence route, so the existing value ladder
may have helped, although the 41-minute gap prevents a causal claim.

Current Coinbase search strengthens the positioning opportunity. Our exact
evidence route ranks third for `OFAC wallet sanctions screening` and first for
`signed payee sanctions evidence`, with three measured payers. However, neither
OFAC route appears in the leading results for `agent payment safety preflight`.
Several returned competitors have only one or two measured payers. This is a
metadata relevance gap, not a reason to add a duplicate product.

## Cross-Cohort Evidence

The first buyer paid 477 resources across 222 recipients and spent `$8.132078`.
A transparent keyword classification finds 61 safety, sanctions, wallet,
counterparty, trust, verification, or compliance calls across 61 distinct
resources: 12.8 percent of its calls and `$1.381` of spend. The fourth buyer
overlaps 14 recipient wallets with that earlier sweep.

This repeat across two independent wallets suggests that agent payment safety is
a recurring catalog category. It does not show retention: both wallets still
behave like broad evaluators, and neither has returned to our service on another
UTC day.

## Replication Economics

Catalog sweeps are repeatable acquisition, but not yet a scalable business. Four
external clusters produced `$0.20` over roughly 15 days, which annualizes to only
about `$0.40` per month if the rate remains flat. At `$0.05` per call, `$100` of
monthly revenue would require 2,000 calls; at a `$0.02` retained margin it would
require 5,000 calls. The observed catalog traffic is orders of magnitude below
that.

The evidence rejects two tempting strategies:

- Raising catalog-sample prices: this buyer made no purchase above `$0.05`.
- Creating many arbitrary penny APIs: that may catch more sweeps but does not
  create task-driven demand or enough expected revenue to justify maintenance.

## Actions Taken

1. Completed an explicitly owner-tagged `$0.05` Form D call. It returned three
   leads and settled successfully.
2. Completed an explicitly owner-tagged `$0.03` one-call Python execution. It
   created, executed, and terminated the sandbox successfully. Direct upstream
   cost was `$0.015`.
3. Kept both owner calls excluded from independent demand and revenue quality.
4. Changed only the existing OFAC discovery metadata to emphasize `Agent
   Payment Safety`, `payment-preflight`, `transaction-safety`, `wallet-risk`,
   and `counterparty-check`, while preserving the exact-match and no-clearance
   limitations.

The `$0.08` owner payments moved between owner-controlled wallets. The compute
supplier cost, not the transfer to our payee wallet, is the material cash cost.

## Decision Gates

Primary product evidence remains any one of:

- a non-example paid request;
- one payer returning on a later UTC day;
- a cheap decision buyer progressing to a task-specific evidence request;
- an independent compute call using non-example code.

Secondary distribution evidence is another independent catalog buyer purchasing
the newly indexed Form D or compute route. It proves indexing and acquisition,
not product-market fit.

Do not build a composed transaction-safety bundle yet. Reconsider a single
`GO`, `STOP`, or `REVIEW` bundle priced at no more than `$0.05` only after either
two independent non-example safety requests or one cross-day safety buyer. Any
such bundle must cap upstream cost below `$0.02` and state that exact OFAC checks
are not complete sanctions or ownership/control analysis.

## Sources

- Live experiment ledger: https://evidence.regulavita.com/v1/experiments/status
- Coinbase merchant catalog: https://api.cdp.coinbase.com/platform/v2/x402/discovery/merchant?payTo=0x9500075649a70411c81f99c4314f6cff55d12579&limit=100
- Coinbase seller discovery guide: https://docs.cdp.coinbase.com/x402/seller/get-discovered
- Base USDC transfer evidence: https://base.blockscout.com/address/0xC7a277C1961E5Ca07F1319A5369CB4Ef9c98263e
- First-buyer comparison: `first-customer-forensic-2026-08-10.md`
- First-buyer transaction map: `first-buyer-purchase-map-2026-08-10.csv`
