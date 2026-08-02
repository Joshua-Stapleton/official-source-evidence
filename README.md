# Official Source Evidence

Pay-per-call official-source evidence for autonomous agents. No account, API
key, subscription, or sales call.

**Live service:** [iti-official-source-evidence.fly.dev](https://iti-official-source-evidence.fly.dev/)
| [OpenAPI](https://iti-official-source-evidence.fly.dev/openapi.json)
| [Agent manifest](https://iti-official-source-evidence.fly.dev/.well-known/agent-service.json)
| [Coinbase Bazaar listing](https://api.cdp.coinbase.com/platform/v2/x402/discovery/merchant?payTo=0x9500075649a70411c81f99c4314f6cff55d12579&limit=100)

## Agent-Payable Endpoints

| Endpoint | Result | Price |
| --- | --- | ---: |
| `POST /v1/ofac/exact-identifier-evidence` | Exact OFAC SDN or Consolidated lookup for a crypto address, OFAC UID, or exact name, with versioned source proof | $0.05 USDC |
| `POST /v1/sec/filing-trigger-delta` | New SEC EDGAR 8-K, 10-Q, or 10-K filings since an accession, plus selected deterministic XBRL fact deltas | $0.10 USDC |

Both routes use x402 v2 on Base mainnet. An x402-compatible client receives a
standard HTTP 402 challenge, pays USDC, retries automatically, and receives the
JSON result. The production service has a hard `$10.00` accepted-revenue cap
per UTC day.

## Discover and Inspect

Search the Coinbase Bazaar from an Agentic Wallet CLI:

```bash
npx awal@latest x402 bazaar search "OFAC exact identifier evidence"
```

Inspect the free contracts before paying:

```bash
curl https://iti-official-source-evidence.fly.dev/v1/ofac/sample
curl https://iti-official-source-evidence.fly.dev/v1/sec/sample
curl https://iti-official-source-evidence.fly.dev/llms.txt
```

An unpaid valid request demonstrates the machine-readable payment challenge:

```bash
curl -i -X POST \
  https://iti-official-source-evidence.fly.dev/v1/ofac/exact-identifier-evidence \
  -H 'Content-Type: application/json' \
  -d '{"identifier_type":"ofac_uid","identifier":"36","lists":["SDN"]}'
```

## What a Paid Result Includes

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
