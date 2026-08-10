# Source Change Watch Revenue Experiment

Pre-registered on 10 August 2026. Revenue is the objective. Engineering output,
directory listings, unpaid challenges, and owner-funded calls are not demand.

## Offer

- Product: one 30-day monitor for a public HTTPS text, HTML, JSON, or XML source.
- Delivery: checks every six hours, private status polling, normalized diffs,
  and an optional signed webhook.
- Price: `$1.00` USDC on Base through x402.
- Cost boundary: run on the existing Fly machine and volume; do not add paid
  infrastructure before independent revenue justifies it.

## Hypothesis

Autonomous buyers will pay for a persistent job that removes repeated polling
and state management, even though they showed little willingness to buy
one-off official-source lookups.

## Attribution

- Count only successful mainnet settlements whose payer is not in the owner
  wallet list and which create a Source Change Watch monitor.
- Report owner-funded, testnet, challenged, paid, activated, changed, and
  webhook-delivered events separately.
- Unique demand is unique non-owner payer addresses. Multiple calls from one
  payer are repeat usage, not additional buyers.

## Distribution Fixed Before Measurement

1. Public OpenAPI, agent manifest, `llms.txt`, sample contract, and GitHub README.
2. x402scan origin registration.
3. x402-list owner update when its seven-day cooldown expires.
4. Coinbase Bazaar indexing after the first legitimate fulfilled purchase.
5. No personal social accounts and no owner-funded calls presented as demand.

## Gates

### Day 14

Continue only if there are at least three independent activated monitors and
`$3.00` independent gross revenue. No single buyer may represent all demand.

### Day 30

Scale only if there are at least ten independent activated monitors, `$10.00`
independent gross revenue, and either one repeat buyer or one delivered change
event consumed through polling or webhook.

If the day-14 gate fails, stop product polishing and test a marketplace-driven
automated service instead. If the day-30 gate fails, retire the offer from the
primary page. Do not keep it alive on the theory that perfect copy or another
directory will create demand.

## Scale Path

Only after the day-30 gate passes:

- Add a `$3.00` hourly-check tier.
- Add a `$10.00` bundle for up to ten sources with one webhook.
- Increase the daily accepted-revenue cap only after measured capacity and
  gross margin remain acceptable.
