# Official Source Evidence and GTM Signals

Pay-per-call official-source GTM signals and evidence for autonomous agents. No
account, API key, subscription, or sales call.

**Live service:** [evidence.regulavita.com](https://evidence.regulavita.com/)
| [OpenAPI](https://evidence.regulavita.com/openapi.json)
| [Agent manifest](https://evidence.regulavita.com/.well-known/agent-service.json)
| [Coinbase Bazaar listing](https://api.cdp.coinbase.com/platform/v2/x402/discovery/merchant?payTo=0x9500075649a70411c81f99c4314f6cff55d12579&limit=100)

## Agent-Payable Endpoints

| Endpoint | Result | Price |
| --- | --- | ---: |
| `POST /v1/gtm/form-d-funding-leads` | New SEC Form D private-offering signals filtered by issuer state, industry keyword, and reported amount sold, with related people, official links, and a cursor | $0.05 USDC |
| `POST /v1/ofac/payment-preflight` | Compact stop/no-exact-match decision for an EVM destination address against current official OFAC data | $0.01 USDC |
| `POST /v1/sec/filing-change-signal` | New SEC 8-K, 10-Q, or 10-K filings for a ticker since a timestamp, with links and a next-check cursor | $0.01 USDC |
| `POST /v1/ofac/exact-identifier-evidence` | Exact OFAC SDN or Consolidated lookup for a crypto address, OFAC UID, or exact name, with versioned source proof | $0.05 USDC |
| `POST /v1/sec/filing-trigger-delta` | New SEC filings for a ticker and timestamp or CIK and accession, plus deterministic XBRL deltas and a signed receipt | $0.10 USDC |

All five routes use x402 v2 on Base mainnet. An x402-compatible client receives a
standard HTTP 402 challenge, pays USDC, retries automatically, and receives the
JSON result. The production service has a hard `$10.00` accepted-revenue cap
per UTC day.

Monitoring tools may send an empty unauthenticated `POST` to any paid route.
The service treats that as the published example request and returns the normal
HTTP 402 challenge without charging. Non-empty malformed or schema-invalid
requests are still rejected before payment.

## Discover and Inspect

Search the Coinbase Bazaar from an Agentic Wallet CLI:

```bash
npx awal@latest x402 bazaar search "OFAC exact identifier evidence"
npx awal@latest x402 bazaar search "SEC Form D funding sales trigger"
```

Inspect the free contracts before paying:

```bash
curl https://evidence.regulavita.com/v1/ofac/sample
curl https://evidence.regulavita.com/v1/sec/sample
curl https://evidence.regulavita.com/v1/gtm/form-d-funding-leads/sample
curl https://evidence.regulavita.com/llms.txt
```

An unpaid valid request demonstrates the machine-readable payment challenge:

```bash
curl -i -X POST \
  https://evidence.regulavita.com/v1/gtm/form-d-funding-leads \
  -H 'Content-Type: application/json' \
  -d '{"since":"2026-08-03T00:00:00Z","states":["CA","NY"],"minimum_amount_sold_usd":"1000000","limit":10}'
```

## Decision And Evidence Layers

The two `$0.01` routes return compact, unsigned decisions for frequent agent
workflows. The Form D GTM feed and premium evidence routes add:

- Official publisher and source-version metadata.
- Canonical request, source-bundle, component, and result SHA-256 hashes.
- An Ed25519-signed evidence receipt.
- Explicit data freshness and parser version.
- Paid-result replay bound to the original payment proof.

The OFAC route is exact-match evidence only. It is not fuzzy screening,
ownership/control analysis, sanctions clearance, transaction authorization, or
legal advice. The SEC route returns factual filing records and deterministic
deltas, never materiality opinions, valuation, investment advice, or execution
instructions.

The Form D route is a factual sales trigger. Form D is an issuer-filed notice of
an exempt offering; its total offering amount is not proof of capital raised.
The API exposes the separately reported amount sold and preserves official SEC
links so consuming agents can state that distinction accurately.

## Operator Documentation

The implementation, source controls, payment flow, deployment configuration,
operating limits, and experiment gates are documented in
[`autonomous_data_api/README.md`](autonomous_data_api/README.md).

Validate a checkout with:

```bash
PYTHONPATH=. uv run --with-requirements autonomous_data_api/requirements.txt \
  pytest autonomous_data_api/tests -q
uvx ruff check autonomous_data_api
uvx ruff format --check autonomous_data_api
docker build -t official-source-evidence-api:local \
  -f autonomous_data_api/Dockerfile .
```

This remains a capped demand experiment. Owner-funded calls prove technical
settlement and discovery, not independent demand, revenue quality, or a
validated business.
