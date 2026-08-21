# x402 Profitable-Pattern Audit - 2026-08-21

## Method

The audit used current x402scan 24-hour and 30-day transaction, volume, and buyer
counts, then checked provider documentation for the fee retained by the seller.
Settlement volume was not treated as profit. Pass-through model, telecom,
booking, game, and trading capital can dominate headline volume.

## Findings

1. BlockRun is the strongest observable economic case. Its Base routing service
   showed millions of 30-day calls and roughly `$200k` of settlement volume.
   Current pricing documents a flat `$0.001` fee on chat requests and 5 percent
   margins on several media/search products. This supports material retained
   fees, but does not prove net profit after infrastructure, failures, subsidies,
   or owner traffic.
2. The next recurring pattern is access to scarce infrastructure: AI models,
   sandbox compute, RPC, telecom, X/social data, proprietary enrichment, and
   transaction or booking execution. These products sit inside repeated machine
   loops and are difficult for an agent to reproduce with a free browser call.
3. Prediction-market activity is real but raw-data wrappers are crowded. Current
   examples generally show tens of buyers and only cents to a few dollars of
   30-day volume. Vishwa is materially more active because it also participates
   in order creation/execution, not because it summarizes public market data.
4. Trust checks, public-source summaries, generic audits, and deterministic tools
   usually show low or zero measured demand. Adding more endpoints in these
   categories is unlikely to solve the structural problem.
5. High gross volume with few buyers is weak evidence. Revenue quality requires
   independent buyer breadth, repeat use across days, retained fee, direct cost,
   and failure rate.

## Product Decision

Launch one-call isolated Python execution at `$0.03`. It combines BlockRun's
three sandbox payments into one stable buyer contract and has a maximum direct
supplier cost of `$0.015`. This is not a raw proxy: lifecycle orchestration,
cleanup, replay protection, cost pinning, settlement reconciliation, and signed
delivery are handled for the buyer.

Do not launch another prediction-market wrapper. Revisit prediction markets only
if we can obtain a licensed proprietary signal or participate directly in a
repeated action loop.

## Sources

- https://www.x402scan.com/resources
- https://www.x402scan.com/server/cbe8caef-6324-4bd1-aee7-63d09fb4d1b9
- https://blockrun.ai/docs/products/intelligence/pricing
- https://blockrun.ai/docs/api-reference/modal-sandbox
- https://www.x402scan.com/server/7fdd6134-681d-4556-82b8-6fbb2c707a36
- https://www.x402scan.com/server/018d1682-9ea1-4068-b276-fc4d7c607acb
- https://www.x402scan.com/server/954b5563-218c-4224-8cc4-2acb09c8d664
