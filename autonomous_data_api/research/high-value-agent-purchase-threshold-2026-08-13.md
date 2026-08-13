# High-Value Autonomous Purchase Threshold

Date: 2026-08-13  
Production: https://evidence.regulavita.com  
Scope: Base-mainnet x402 services and Official Source Evidence price expansion

## Decision

Do not launch a new $10 research endpoint yet. Continue the low-price discovery
lane unchanged, expose explicit upgrades into the existing $0.25 dossier, and
test the existing $1 persistent monitor before building a $5-$10 bundle.

This is a no-go on an immediate $10 launch, not a no-go on higher-value agent
commerce. The live evidence shows that agents can autonomously authorize larger
payments when the output is immediately spendable or operationally durable. It
does not yet show that they will pay $10 for a bespoke research response from an
unfamiliar provider.

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

The public Bazaar API returned 1,550 active Base-mainnet resources accepting
official Base USDC through the exact scheme. Current advertised prices were:

| Current price | Resources | Reported 30-day calls | Reported payer sum |
|---|---:|---:|---:|
| Below $0.01 | 715 | 119,207 | 15,279 |
| $0.01-$0.099 | 606 | 61,595 | 2,227 |
| $0.10-$0.99 | 203 | 4,182 | 489 |
| $1.00-$4.99 | 21 | 376 | 79 |
| $5.00-$9.99 | 2 | 51 | 21 |
| $10.00 or more | 3 | 6 | 4 |

The payer column is a sum across routes, not ecosystem-unique wallets. Bazaar
quality counts also include owner bootstrap calls and evaluators. Current price
multiplied by historical calls is not revenue because prices can change.

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
- The strongest adjacent company-dossier offers in Bazaar are currently
  $0.12-$0.95. Their payer counts are small and do not establish independent
  repeat demand.

### Buyer-side controls

- AWS AgentCore CLI currently defaults its auto-session spend cap to $10 and
  documents an operator-configurable example of $25. A $10 request can therefore
  consume an entire default session budget even when technically permitted.
- Coinbase AgentKit discovery supports a maximum-price filter. Buyers can omit
  expensive services before an agent evaluates their descriptions.
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

Failure: no independent upgrade and no persistent-job purchase. On failure, do
not build the $5-$10 monitoring bundle.

### Stage 2: $5 bundle, conditional

Only after Stage 1 succeeds, build one bounded portfolio monitor for up to ten
public sources for 30 days. It must reuse the current monitor engine, have no
new fixed hosting cost, cap response bytes and webhooks, and keep per-sale direct
cost below $0.50.

Success: two independent $5 buyers, at least one using non-example URLs, with no
buyer responsible for more than 50 percent of fulfilled calls.

### Stage 3: $10 bundle, conditional

Only after Stage 2 succeeds, test a larger portfolio or a durable evidence job
whose output saves at least one full agent workflow. Do not reprice an existing
one-shot endpoint to manufacture this test.

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

## Cost and Stop Rule

The research and result-level conversion change add no production hosting cost.
Indexing the $1 monitor requires one explicitly owner-labelled $1 Base USDC
bootstrap settlement. Do not make that payment without a fresh approval. Do not
raise the current $10 daily accepted-revenue cap for this test.
