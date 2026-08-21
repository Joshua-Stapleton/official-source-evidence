# One-Call Isolated Python Experiment

## Hypothesis

Agents will pay for a scarce execution capability that they cannot safely obtain
from a public web fetch: run bounded Python in an isolated environment without
provisioning infrastructure or managing a three-step sandbox lifecycle.

This runs in parallel with every existing product. No current route is retired.

## Contract And Economics

- Paid route: `POST /v1/compute/python-run`
- Customer price: `$0.03` USDC on Base through x402 v2
- Upstream workflow: create sandbox (`<= $0.011`), execute (`<= $0.002`),
  terminate (`<= $0.002`)
- Maximum direct supplier cost: `$0.015`
- Maximum contribution before hosting and shared overhead: `$0.015` per fulfilled run
- Existing procurement daily supplier cap remains the hard spending boundary

The route accepts Python source only. Source length, runtime, output, CPU, and
memory are bounded. Supplier endpoint, recipient, network, asset, scheme, and
stage price are pinned. The sandbox auto-expires upstream and this service also
attempts explicit termination after every created run.

## Attribution

Owner calls, testnet settlements, catalog sweeps, and published example requests
do not count as demand. Track these separately:

- independent paid attempts and fulfilled runs;
- unique independent payer clusters;
- repeat independent buyers across UTC days;
- independent revenue, direct supplier cost, and contribution;
- create, execute, and terminate failures;
- discovery source and non-example code usage.

## Decision Gates

Scale when either condition is met within 21 days:

1. Three independent payer clusters complete a run.
2. One independent buyer completes paid runs on two UTC days.

Stop or redesign after 21 days or 500 independent non-crawler payment challenges
with zero independent purchases, whichever comes first. Do not improve the route
past this gate merely because unpaid probes are increasing.

If the scale gate passes, raise the daily supplier cap only in proportion to
settled inbound revenue and observed fulfillment reliability. Test larger
runtime or package-enabled tiers only after repeat demand appears.
