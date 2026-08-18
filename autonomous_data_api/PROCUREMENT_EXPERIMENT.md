# Company Profile Procurement Experiment

## Purpose

Test whether autonomous buyers will pay a broker to select, purchase, normalize,
and reconcile machine services, rather than paying for another single-source
public-data wrapper.

The existing evidence, monitoring, SEC, OFAC, and dossier experiments remain
live and are measured separately.

## Product

- Paid route: `POST /v1/procure/company-profile`
- Free inspection: `POST /v1/procure/company-profile/quote`
- Price: `$0.25` USDC on Base through x402 v2
- Input: company name, public domain, and optional ticker
- Action: purchase pinned Tavily retrieval and BlockRun schema-constrained
  normalization services, enforce per-supplier price and recipient limits,
  return only bounded derived fields and source links, surface contradictions
  and partial-fulfillment state, and include response hashes, upstream
  settlement provenance, and an Ed25519-signed receipt.
- Maximum expected supplier cost: `$0.02` per fulfilled request.
- No founder labour, account creation, API key, or manual review is part of
  fulfillment.

## Hypothesis

Agents will pay a material markup to delegate provider selection, wallet and
payment handling, failure management, normalization, and reconciliation to one
stable contract.

This experiment does not test whether agents value generic company facts in
isolation. It tests the brokered execution layer.

## Measurement

Report these independently:

- owner/test settlements;
- unpaid challenges and identified catalog/crawler probes;
- independent paid attempts and completed procurements;
- unique independent payer clusters and cross-UTC-day repeats;
- non-example inputs;
- incoming revenue, actual supplier cost, gross margin, and supplier failures;
- full versus partial two-supplier fulfillment;
- buyer and discovery-source concentration.

An HTTP 402, owner payment, supplier purchase, or one-off catalog sweep is not
independent product demand.

## Gates

Evaluate after 21 days of live exposure.

Scale only if all are true:

- at least three independent payer clusters;
- at least one independent repeat buyer across UTC days;
- at least ten independent non-example completed procurements;
- at least 70% realized gross margin;
- at least 95% paid requests return a full or explicitly partial useful result.

Stop or redesign after 21 days or 500 valid non-crawler challenges if there are
no independent purchases. Do not add `$1` or `$5` tiers until the base route
passes the buyer or repeat-use gates. A later premium tier must add a real batch,
guarantee, scarce supplier, or action rather than merely more fields.

## Safety And Cost Limits

- Pin each supplier endpoint, network, asset, scheme, and maximum price. BlockRun's
  recipient is pinned exactly; Tavily's request-scoped recipient is accepted only
  from the authenticated hardcoded Tavily endpoint and must be a valid nonzero EVM
  address because Tavily rotates it between challenges.
- Reject changed supplier terms rather than silently paying more.
- Bound supplier response size and never persist raw payment proofs or private
  wallet keys.
- Enforce a separate UTC-day supplier-spend cap in SQLite.
- Treat retrieval success with normalization failure as an explicit partial
  result; do not invent fields.
- Preserve the service-wide `$10` accepted-revenue cap.
