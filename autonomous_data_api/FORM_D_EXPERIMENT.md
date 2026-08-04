# SEC Form D GTM Signal Experiment

Pre-registered on 4 August 2026. This document separates technical settlement,
distribution, independent demand, repeat usage, revenue, and sustainable profit.

## Hypothesis

Autonomous GTM agents will repeatedly pay `$0.05` for bounded pages of new SEC
Form D private-offering signals because the response removes source discovery,
parsing, filtering, provenance, and cursor-management work from their workflow.

## Primary Endpoint

`POST https://evidence.regulavita.com/v1/gtm/form-d-funding-leads`

The buyer supplies a UTC baseline plus optional issuer-state, industry-keyword,
reported-amount-sold, amendment, limit, and cursor filters. The response contains
official SEC links, issuer context, related people, reported offering values,
pagination, source hashes, and an Ed25519 receipt.

Form D is an issuer-filed notice of an exempt offering. Total offering amount is
not treated as proof of capital raised; the response labels amount sold
separately and carries this limitation on every result.

## Pre-Registered Gates

- Initial pass: at least 5 independent buyer clusters, 50 independent fulfilled
  calls, 2 repeat buyers across distinct UTC days, at least 99% paid fulfilment,
  and no buyer above 50% of independent calls.
- Scale signal by Day 30: at least 20 independent buyers, 500 calls, 8 repeat
  buyers, and `$25` in independent revenue.
- Kill signal: zero independent paid buyers by Day 21, or fewer than 2 after the
  planned directory exposure has been live for 14 days.
- Owner and testnet payments never count toward any demand or revenue gate.
- Uptime, HTTP 402 responses, and self-funded settlements prove infrastructure,
  not product-market demand.

Live measurements are exposed at
`https://evidence.regulavita.com/v1/experiments/status`.

## Launch Evidence

- Production deployed on Fly.io and externally verified as Base-mainnet
  revenue-ready with a `$10.00` UTC-day accepted-revenue cap.
- Live empty-POST probe returned HTTP 402 for exactly `50000` atomic USDC on
  `eip155:8453` to the configured receiving wallet.
- A real-source dry run returned three current SEC Form D signals and a valid
  signed receipt.
- One owner-funded `$0.05` catalog bootstrap settled successfully in transaction
  `0x7af41edc4bf70d5fa14347b8b8885c952b7a756f8490120a82a0a1ccb6d56d30`.
- The ledger classified that settlement as owner traffic: independent buyers,
  independent fulfilled calls, and independent revenue remained zero.

## Distribution Log

- Coinbase CDP Bazaar: discovery metadata declared; first settlement completed;
  catalog indexing is asynchronous.
- x402scan: all 13 OpenAPI resources registered successfully. Merchant page:
  `https://www.x402scan.com/server/2c8e6e59-5abe-4272-a1ed-856919983c84`.
- 402.ad: Form D endpoint submitted for review through the free provider form.
- x402-list: existing service remains live, but adding the new endpoint is
  blocked by its seven-day update cooldown. Retry only after the cooldown.
- Public social accounts are deliberately excluded from this experiment.

## Interpretation

The launch currently proves source extraction, paid fulfilment, settlement,
machine-readable discovery, and multi-directory exposure. Until a non-owner
buyer pays, independent demand and revenue are both zero.
