# High-Value Autonomous Purchase Threshold

Date: 2026-08-13  
Production: https://evidence.regulavita.com  
Scope: Base-mainnet x402 services and Official Source Evidence price expansion

## Decision

Run a tightly bounded premium-price experiment, but do not launch a generic $10
research endpoint or make $10 the primary product. Continue the low-price
discovery lane unchanged, expose explicit upgrades into the existing $0.25
dossier, and test the existing $1 persistent monitor while specifying one
decision-ready premium product in parallel.

This is a go on a cheap experiment and a no-go on a material product investment.
The live evidence shows that programmatic wallets can authorize $10 when the
output is immediately spendable, operationally durable, or controls a
consequential decision. It does not show that most agents will pay $10 for a
bespoke research response from an unfamiliar provider.

## Observed Evidence

### Our service

As of 08:16 UTC, Official Source Evidence had:

- 2 independent buyer clusters.
- 4 independent fulfilled calls.
- $0.12 independent revenue and $0.00 measured independent direct cost.
- 0 repeat independent buyers across UTC days.
- 0 independent purchases of the $0.25 company dossier.
- 0 independent purchases of the $1.00 source-change monitor.
- 0 independent purchases of the $0.10 SEC delta route.

Both independent cohorts behaved like broad catalog evaluators and used
published examples. This validates autonomous discovery and settlement at
$0.01-$0.05, not task-specific willingness to pay.

### Live Coinbase Bazaar census

The fully paginated public Bazaar API returned 15,090 Base-mainnet USDC
resources. Current advertised prices and reported 30-day calls were:

| Current price | Resources | Reported 30-day calls | Reported payer sum |
|---|---:|---:|---:|
| Below $0.50 | 14,573 | 330,913 | Not summed |
| $0.50-$0.99 | 186 | 440 | Not summed |
| $1.00-$4.99 | 210 | 1,015 | Not summed |
| $5.00-$9.99 | 88 | 197 | Not summed |
| $10.00-$24.99 | 15 | 44 | Not summed |
| $25.00-$99.99 | 16 | 18 | Not summed |
| $100 or more | 2 | 2 | Not summed |

The listing-price median was $0.01, p95 $0.25, and p99 $2.00. On a call-weighted
basis the median was $0.007, p95 $0.05, and p99 $0.28. Only 64 reported calls,
0.019 percent, were on resources currently priced at $10 or more.

Bazaar quality counts are route-level and can include owner bootstrap calls and
evaluators. Current price multiplied by historical calls is not revenue because
prices can change. The catalog also changed slightly during pagination, so these
figures are a point-in-time census rather than a stable population total.

### Higher-price forensic checks

- A $5 prepaid-card route reports 28 calls and 19 payers. Base transfers show
  multiple distinct $5 `transferWithAuthorization` purchases. This is credible
  evidence that agents can cross $5 for stored value or real-world purchasing
  capability, but the transferred principal is not equivalent to API revenue.
- A current $350 agent-readiness package reports one call, but its receiving
  wallet has no $350 settlement associated with that call. The route was called
  while lower prices were being paid and was repriced later; it is not evidence
  of a $350 autonomous sale.
- A $10 directory-submission route has three on-chain $10 payments from two
  wallets. One payer received funds from the seller, sent only to that seller,
  and was funded immediately before its $10 payment. That cluster is consistent
  with operator testing, not independent demand.
- The strongest $10 API case is Arkham's address-counterparties product: three
  catalog calls from two payer wallets and two distinct on-chain $10
  settlements. Both wallets also bought cheaper Arkham routes; one progressed
  through $0.20, $1, and $10 purchases. That is consistent with an automated
  evaluator or workflow escalating spend, but does not rule out vendor or
  partner testing.
- The strongest adjacent company-dossier offers in Bazaar are currently
  $0.12-$0.95. Their payer counts are small and do not establish independent
  repeat demand.

### Buyer-side controls

- AWS AgentCore CLI currently defaults its auto-session spend cap to $10 and
  documents an operator-configurable example of $25. A $10 request can therefore
  consume an entire default session budget even when technically permitted.
- Coinbase AgentKit discovery supports a maximum-price filter. Buyers can omit
  expensive services before an agent evaluates their descriptions.
- The legacy official `x402-fetch` client defaulted to a $0.10 maximum unless
  the caller explicitly overrode it. Older unmodified clients will reject a $10
  challenge.
- x402 itself does not guarantee a universal buyer ceiling; spending authority
  is imposed by the wallet, framework, session, operator, and task policy.

## Interpretation

Price is not the main constraint. Trust, immediate utility, and budget share are.
A new provider asking $10 for a one-shot report faces three simultaneous gates:

1. The route must survive the buyer's discovery price filter.
2. Its declared output must be worth most or all of a common $10 session cap.
3. The buyer must trust irreversible payment before seeing the bespoke result.

Stored value, prepaid compute, and persistent operational jobs can clear these
gates more naturally than generic reports. For this service, persistent source
monitoring is the most defensible higher-value wedge because the buyer receives
30 days of state, scheduled execution, private polling, and optional webhooks.

## Pre-Registered Price Staircase

### Stage 1: existing products

- Preserve every current $0.01-$0.10 route and its pricing.
- Add a machine-readable $0.25 dossier upgrade to each returned Form D lead.
- Make the existing $1 source-change monitor discoverable in Coinbase Bazaar.
- Count owner bootstrap calls separately and freeze further owner purchases.

Success within 14 full days after the $1 route is indexed:

- one independent $1 monitor using a non-example URL; or
- one existing independent buyer upgrades to the $0.25 dossier; or
- one independent buyer returns on another UTC day and uses a non-example input.

Failure: no independent upgrade and no persistent-job purchase. On failure,
stop further polishing of the single-source lane; the separately pre-registered
premium test remains governed by its own 30-day or 500-challenge stop rule.

### Parallel premium specification

Two candidates were evaluated. A decision-ready SEC Claim Evidence Pack would
be valuable, but it requires a new claim interpretation and supplier layer to
avoid returning superficial rule matches. It therefore fails the one-day reuse
gate and remains unbuilt.

The candidate that passes the gate is a 30-day Source Change Portfolio. One
`$9.00` payment creates private six-hour-cadence monitoring for 2-10 public
HTTPS sources, with polling tokens and optional signed webhooks for every
source. It reuses the current bounded monitor engine and existing Fly machine,
performs reachability checks only after payment authorization and before
settlement, and has no metered supplier cost. The price sits below a common
`$10` session ceiling and leaves `$1.00` for discovery calls. Existing products
retain their prices.

Positive gate: three independent premium payer clusters or one independent repeat
buyer across different UTC days. Stop or redesign after 30 days or 500
independent non-crawler challenges with zero purchases. Do not count owner
settlements, catalog sweeps, or published-example requests as product demand.

### Higher tiers, conditional

Only after the `$9.00` premium experiment succeeds, test a higher-cadence or
larger durable evidence job whose output saves at least one full agent workflow.
Do not reprice an existing one-shot endpoint to manufacture this test.

## Sources

- Coinbase Bazaar discovery and quality fields:
  https://docs.cdp.coinbase.com/x402/buyer/discover-services
- Public Bazaar API used for the census:
  https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources
- AWS AgentCore payment controls:
  https://github.com/aws/agentcore-cli/blob/main/docs/payments.md
- Population-scale x402 authenticity study:
  https://arxiv.org/abs/2607.12575
- Chainalysis x402 transfer-size analysis:
  https://www.chainalysis.com/blog/x402-agentic-payments-adoption/
- Base transaction explorer used for settlement checks:
  https://base.blockscout.com/
- Arkham $10 settlement one:
  https://base.blockscout.com/tx/0xc141d16eca1f9dd8645b85aa27aad8be91725278dfd07aff06675914f4672fb5
- Arkham $10 settlement two:
  https://base.blockscout.com/tx/0x78ed382346897944686d49f772c523e7966e73c8e585f48d7c9509a2b1229851
- Legacy x402-fetch maximum-value behavior:
  https://www.npmjs.com/package/x402-fetch

## Cost and Stop Rule

The research and result-level conversion change add no production hosting cost.
Indexing the $1 monitor requires one explicitly owner-labelled $1 Base USDC
bootstrap settlement. Do not make that payment without a fresh approval. Do not
raise the current $10 daily accepted-revenue cap for this test.
