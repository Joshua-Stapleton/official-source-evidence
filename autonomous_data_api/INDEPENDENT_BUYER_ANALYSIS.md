# Independent Buyer Cohort Analysis

Snapshot date: 2026-08-10 UTC

## Observed Purchases

The production ledger records two fulfilled, non-owner calls from one payer
cluster:

| Time (UTC) | Route | Price | Base transaction |
| --- | --- | ---: | --- |
| 03:46:07 | `/v1/gtm/form-d-funding-leads` | $0.05 | `0x14183ce44cd751a7dfd742a66d90b607db6922b4ae477000b960b4bd6283a729` |
| 03:46:09 | `/v1/ofac/exact-identifier-evidence` | $0.05 | `0xdb60a68cae6105cf4bebe19c0689e44f84fdab9fd48b31ec996c6bda74a571f6` |

Both EIP-3009 authorizations name the same buyer,
`0x72f6d77a78dbec1b2fff6f6db4672ddde1bd04c4`, and the configured receiving
wallet, `0x9500075649a70411c81f99c4314f6cff55d12579`. The two transaction senders are
facilitator relayers and are not the buyer.

## Wider Buyer Cohort

The buyer's complete Base USDC `transferWithAuthorization` history available
through Blockscout contained:

- 476 paid calls to 221 distinct receiving wallets.
- $8.127078 USDC in total spend.
- 473 of 476 calls, or 99.4 percent, priced at $0.05 or less.
- 90 calls at $0.05, 41 calls at $0.03, and 47 calls at $0.01.
- On 2026-08-10, 454 calls to 221 recipients totaling $7.963078 between
  02:22:21 and 03:46:49 UTC.
- Earlier activity was narrow: 21 calls to one recipient on 2026-08-03 and one
  additional call on 2026-08-04. This is not evidence of a weekly broad sweep.

The August 10 behavior is best classified as an automated catalog sweep. Our
two calls occurred near the end of the sweep and were two of hundreds of
purchases. This validates discoverability, schema compatibility, payment
settlement, and fulfillment. It does not establish that the buyer selected the
products for their domain value, returned for them, or received economic value
from the results.

## Revenue Implications

1. Machine-readable distribution matters. A correctly indexed endpoint can be
   purchased without a sales interaction.
2. A price at or below $0.05 materially increases eligibility for this buyer's
   observed budget policy.
3. Broad catalog sweeps can provide useful launch revenue, but are not product
   market fit and must not be counted as repeat demand.
4. Creating many arbitrary low-value endpoints would overfit one subsidized
   cohort. New products still need a real agent workflow, low marginal cost,
   and an upgrade or repeat-use path.

## Actions Taken

- Added `/v1/web/source-snapshot` at $0.03. It performs bounded public-source
  extraction, returns normalized text and literal excerpts, hashes the content,
  signs a receipt, and offers a direct upgrade to the $1.00 source monitor.
- Kept unpaid discovery cheap: source fetching happens only after successful
  payment, preventing the 402 probe path from becoming a free web proxy.
- Registered all seven paid endpoints on x402scan using free SIWX ownership
  authentication. The snapshot route is live in that catalog.
- Queued the x402-list endpoint expansion for the first legitimate post-cooldown
  run, with no paid resubmission and no social posting.
- Added route-level conversion accounting that excludes owner calls, testnet
  calls, and unpaid probes.
- Prepared an exact-price, exact-network, exact-recipient owner purchase for the
  snapshot route. It remains unarmed until a $0.03 spend is explicitly approved.

## Next Test

The next highest-value action is one $0.03 owner-funded snapshot settlement to
trigger Coinbase Bazaar indexing, followed by observation rather than further
product proliferation. A new endpoint should be added only if a second
independent cohort, a repeat buyer, or a clearly observed agent workflow supports
it.

The portfolio continues to use these global scale gates:

- At least five independent buyer clusters.
- At least 50 independent fulfilled calls.
- At least two repeat buyers across distinct UTC dates.
- At least 99 percent paid fulfillment success.
- No buyer responsible for more than 50 percent of independent calls.

Until those gates move, the scientifically correct result is: autonomous
distribution and payment are validated; repeat commercial demand is not.

## Detailed Artifacts

- [First-customer forensic](research/first-customer-forensic-2026-08-10.md)
- [Buyer portfolio and expected-value analysis](research/buyer-portfolio-ev-analysis-2026-08-10.md)
- [Probabilistic purchase map](research/first-buyer-purchase-map-2026-08-10.csv)
